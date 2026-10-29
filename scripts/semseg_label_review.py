"""Rank annotated photos by how strongly the hull model disagrees with their labels.

For reviewers: the photos at the top are where a label is most likely wrong or
incomplete (or where the model is confidently wrong, which is also worth
knowing). Writes review.csv plus a side-by-side image for the top photos.
The database and uploads are only read. Run from the repo root:

    python scripts/semseg_label_review.py --out C:\\datasets\\label_review

Three kinds of disagreement, each as a share of the photo's area and counted
only where the model is at least `--confidence` sure:
  unlabelled   model sees damage, label says sound hull
  overlabelled label says damage, model sees sound hull
  wrong_class  both see damage, but a different class
"""
import argparse
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_semseg_dataset as builder  # noqa: E402
import config  # noqa: E402
import models  # noqa: E402
from database import SessionLocal  # noqa: E402
from formats.common import ordered_annotations, polygon_points, safe_stem  # noqa: E402
from ml.semseg_arch import STRIDE, build_semseg_net  # noqa: E402

MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
# Builder class -> checkpoint class, for checkpoints trained with merged classes.
MERGED_NAMES = {"dirt1": "dirt", "dirt2": "dirt"}
KINDS = ("unlabelled", "overlabelled", "wrong_class")


def class_lookup(checkpoint_classes: list) -> np.ndarray:
    """256-entry table from the builder's class ids to the checkpoint's."""
    names = [c["name"] for c in checkpoint_classes]
    table = np.zeros(256, np.uint8)
    for old_id, (name, _) in enumerate(builder.CLASSES):
        target = name if name in names else MERGED_NAMES.get(name)
        if target not in names:
            sys.exit(f"Error: class {name!r} has no counterpart in the checkpoint ({names})")
        table[old_id] = names.index(target)
    return table


@torch.no_grad()
def predict(model, image: Image.Image, device) -> np.ndarray:
    """Class probabilities (C, H, W) for an already-resized RGB image."""
    array = (np.asarray(image).astype(np.float32) / 255.0 - MEAN) / STD
    tensor = torch.from_numpy(array.transpose(2, 0, 1))[None].to(device)
    h, w = tensor.shape[-2:]
    tensor = F.pad(tensor, (0, (-w) % STRIDE, 0, (-h) % STRIDE), mode="reflect")
    with torch.autocast(device.type, enabled=device.type == "cuda"):
        return model(tensor)["out"][0, :, :h, :w].float().softmax(0).cpu().numpy()


def side_by_side(image: Image.Image, label: np.ndarray, pred: np.ndarray, palette: np.ndarray) -> Image.Image:
    photo = np.asarray(image)
    tiles = [photo, (0.4 * photo + 0.6 * palette[label]).astype(np.uint8),
             (0.4 * photo + 0.6 * palette[pred]).astype(np.uint8)]
    return Image.fromarray(np.concatenate([np.asarray(Image.fromarray(t).resize((720, 540))) for t in tiles], 1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="output folder (created)")
    ap.add_argument("--weights", default=str(REPO / "models" / "detector" / "hull_semseg.pt"))
    ap.add_argument("--top", type=int, default=40, help="how many side-by-side images to write")
    ap.add_argument("--confidence", type=float, default=0.8)
    ap.add_argument("--min-polygons", type=int, default=20)
    ap.add_argument("--app-url", default="", help="e.g. http://192.168.110.250:8000 to add a link per task")
    args = ap.parse_args()

    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    checkpoint = torch.load(args.weights, map_location=device, weights_only=True)
    classes, scale = checkpoint["classes"], float(checkpoint.get("scale", 0.5))
    model = build_semseg_net(checkpoint["arch"], len(classes)).to(device).eval()
    model.load_state_dict(checkpoint["model"])
    lookup = class_lookup(classes)
    palette = np.array([c["rgb"] for c in classes], np.uint8)

    rows, kept = [], {}
    db = SessionLocal()
    try:
        projects = {p.id: p.name for p in db.query(models.Project)}
        label_class: dict[int, dict] = defaultdict(dict)
        for lab in db.query(models.Label):
            cid = builder.class_of_label(lab.name)
            if cid is not None:
                label_class[lab.project_id][lab.id] = cid
        eligible = {pid for pid, m in label_class.items() if len(set(m.values())) >= builder.MIN_MAPPED_LABELS}
        tasks = [t for t in db.query(models.Task).filter(models.Task.image_path.isnot(None))
                 if t.project_id in eligible and t.status in builder.FINISHED_STATUSES]
        for index, task in enumerate(tasks, 1):
            path = os.path.join(config.DATA_DIR, *task.image_path.split("/"))
            polygons = sum(1 for a in ordered_annotations(task) if polygon_points(a) is not None
                           and a.get("labelId") in label_class[task.project_id])
            if polygons < args.min_polygons or not os.path.isfile(path):
                continue
            with Image.open(path) as im:
                full_size = im.size
                size = (round(im.width * scale), round(im.height * scale))
                image = im.convert("RGB").resize(size, Image.BILINEAR)
            label = lookup[np.asarray(builder.render_task(task, label_class[task.project_id], full_size, scale))]
            prob = predict(model, image, device)
            pred, sure = prob.argmax(0), prob.max(0) >= args.confidence
            share = {
                "unlabelled": float(((label == 0) & (pred != 0) & sure).mean()),
                "overlabelled": float(((label != 0) & (prob[0] >= args.confidence)).mean()),
                "wrong_class": float(((label != 0) & (pred != 0) & (pred != label) & sure).mean()),
            }
            row = {"project": projects[task.project_id], "task_id": task.id, "file": safe_stem(task),
                   "annotator": (task.assignee or "").strip(), "status": task.status, "polygons": polygons,
                   "score": sum(share.values()), **share, "project_id": task.project_id}
            rows.append(row)
            # Keep pictures only for the current top photos, to bound memory.
            kept[task.id] = (row["score"], side_by_side(image, label, pred, palette))
            if len(kept) > args.top:
                del kept[min(kept, key=lambda k: kept[k][0])]
            if index % 50 == 0:
                print(f"{index}/{len(tasks)} tasks checked", flush=True)
    finally:
        db.close()

    rows.sort(key=lambda r: r["score"], reverse=True)
    with (out / "review.csv").open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.writer(fh)
        writer.writerow(["rank", "project", "file", "annotator", "status", "polygons", "disagreement_%",
                         "unlabelled_%", "overlabelled_%", "wrong_class_%", "main_issue", "picture", "link"])
        for rank, r in enumerate(rows, 1):
            picture = ""
            if r["task_id"] in kept:
                picture = f"{rank:03d}_{r['project'].replace(' ', '_')}_{r['file']}.jpg"
                kept[r["task_id"]][1].save(out / "images" / picture, quality=85)
            link = f"{args.app_url}/app.html?projectId={r['project_id']}&taskId={r['task_id']}" if args.app_url else ""
            writer.writerow([rank, r["project"], r["file"], r["annotator"], r["status"], r["polygons"],
                             round(r["score"] * 100, 2)] + [round(r[k] * 100, 2) for k in KINDS]
                            + [max(KINDS, key=lambda k: r[k]), picture, link])

    print(f"\n{len(rows)} photos ranked -> {out / 'review.csv'}; {len(kept)} pictures in {out / 'images'} "
          "(columns: photo | label | model)")
    by_annotator = defaultdict(list)
    for r in rows:
        by_annotator[r["annotator"] or "?"].append(r["score"])
    print("mean disagreement % by annotator (5+ photos), highest first:")
    for name, scores in sorted(by_annotator.items(), key=lambda kv: -np.mean(kv[1])):
        if len(scores) >= 5:
            print(f"  {name:20s} {np.mean(scores) * 100:5.2f}  ({len(scores)} photos)")


if __name__ == "__main__":
    main()

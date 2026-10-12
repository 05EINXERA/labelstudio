"""Build a semantic-segmentation dataset (image + class-id mask pairs).

Masks are rendered straight from the annotation database, so every mask is
aligned with the exact image it was drawn on. Optionally merges hand-supplied
image/mask pairs (`--extra`) and lists exported masks that have no photo
(`--exports`). The DB and uploads are only read. Run from the repo root:

    python scripts/build_semseg_dataset.py --dst C:\\datasets\\semseg \\
        [--extra C:\\datasets\\semseg_extra] [--exports C:\\Users\\me\\Downloads\\Exports]

Class ids: 0 background/sound (Sound Area is black, the same colour as the
unlabelled background in exported masks, so the two are one class), 1..7 defects.
"""
import argparse
import csv
import hashlib
import json
import os
import random
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import models
from config import DATA_DIR
from database import SessionLocal
from formats.common import ordered_annotations, polygon_points, safe_stem

CLASSES = [
    ("background_sound", (0, 0, 0)),
    ("af_paint_deterioration", (0xFF, 0x13, 0xA6)),
    ("dirt1", (0x64, 0xD4, 0x13)),
    ("dirt2", (0xFF, 0xD7, 0x00)),
    ("exposed_ac_paint", (0x0F, 0xFF, 0xFF)),
    ("exposed_steel_plate", (0x1E, 0x5A, 0xFF)),
    ("marine_organisms", (0xB7, 0x46, 0xFF)),
    ("rust", (0xD9, 0x53, 0x19)),
]
# Label-name prefix (lower-cased) -> class id.
NAME_TO_CLASS = [
    ("sound", 0), ("af paint", 1), ("dirt 1", 2), ("dirt 2", 3),
    ("exposed ac", 4), ("exposed steel", 5), ("residual marine", 6), ("rust", 7),
]
MIN_MAPPED_LABELS = 6  # a project must carry at least this many standard classes
# Statuses that mean an annotator finished the photo. "New", "In Progress" and
# "Declined" tasks are partly drawn or rejected; training on them teaches the
# model that unlabelled damage is background.
FINISHED_STATUSES = ("Completed", "Passed", "Reviewed", "Approved", "Monitored", "Checked")
IMAGE_EXTS = (".jpg", ".jpeg", ".png")


def class_of_label(name: str) -> int | None:
    low = (name or "").strip().lower()
    for prefix, cid in NAME_TO_CLASS:
        if low.startswith(prefix):
            return cid
    return None


def file_md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def render_task(task, label_class: dict, size: tuple[int, int], scale: float) -> Image.Image:
    width, height = size
    mask = Image.new("L", (round(width * scale), round(height * scale)), 0)
    draw = ImageDraw.Draw(mask)
    for ann in ordered_annotations(task):
        cid = label_class.get(ann.get("labelId"))
        poly = polygon_points(ann)
        if cid is None or poly is None:
            continue
        draw.polygon([(x * scale, y * scale) for x, y in poly], fill=cid)
    return mask


def load_db_samples(scale: float, min_polygons: int, statuses: set[str], exclude: set[str]):
    """-> list of dicts: project, stem, image_path, task, label_class, size, polygons."""
    db = SessionLocal()
    try:
        projects = {p.id: p.name for p in db.query(models.Project)}
        label_class: dict[int, dict] = defaultdict(dict)
        for lab in db.query(models.Label):
            cid = class_of_label(lab.name)
            if cid is not None:
                label_class[lab.project_id][lab.id] = cid
        eligible = {pid for pid, m in label_class.items() if len(set(m.values())) >= MIN_MAPPED_LABELS}
        samples = []
        for task in db.query(models.Task).filter(models.Task.image_path.isnot(None)):
            if task.project_id not in eligible or projects[task.project_id].lower() in exclude:
                continue
            if statuses and task.status not in statuses:
                continue
            path = os.path.join(DATA_DIR, *task.image_path.split("/"))
            if not os.path.isfile(path):
                continue
            polys = sum(1 for a in ordered_annotations(task) if polygon_points(a) is not None
                        and a.get("labelId") in label_class[task.project_id])
            if polys < min_polygons:
                continue
            samples.append({"project": projects[task.project_id], "stem": safe_stem(task),
                            "image_path": path, "task": task, "polygons": polys,
                            "label_class": label_class[task.project_id]})
        # Render while the session is open (task.annotations may lazy-load).
        for s in samples:
            with Image.open(s["image_path"]) as im:
                s["size"] = im.size
            s["mask"] = render_task(s["task"], s["label_class"], s["size"], scale)
            del s["task"]
        return samples
    finally:
        db.close()


def rgb_mask_to_ids(rgb: np.ndarray) -> tuple[np.ndarray, int]:
    ids = np.zeros(rgb.shape[:2], np.uint8)
    known = np.zeros(rgb.shape[:2], bool)
    for cid, (_, colour) in enumerate(CLASSES):
        hit = np.all(rgb == colour, axis=-1)
        ids[hit] = cid
        known |= hit
    return ids, int((~known).sum())


def load_extra(folder: Path, scale: float):
    samples, unknown = [], 0
    for img in sorted((folder / "images").glob("*")):
        if img.suffix.lower() not in IMAGE_EXTS:
            continue
        mpath = folder / "masks" / (img.stem + ".png")
        if not mpath.is_file():
            print(f"extra: no mask for {img.name}, skipped")
            continue
        with Image.open(img) as im, Image.open(mpath) as mk:
            if im.size != mk.size:
                print(f"extra: size mismatch {img.name} {im.size} vs {mk.size}, skipped")
                continue
            ids, bad = rgb_mask_to_ids(np.asarray(mk.convert("RGB")))
            size = im.size
        unknown += bad
        mask = Image.fromarray(ids).resize((round(size[0] * scale), round(size[1] * scale)), Image.NEAREST)
        samples.append({"project": "extra", "stem": img.stem, "image_path": str(img),
                        "polygons": -1, "size": size, "mask": mask})
    if unknown:
        print(f"extra: {unknown} mask pixels had a colour outside the class palette (treated as background)")
    return samples


def pick_val_projects(by_project: dict[str, int], fraction: float, seed: int) -> set[str]:
    names = sorted(by_project)
    random.Random(seed).shuffle(names)
    total, target, chosen, got = sum(by_project.values()), fraction, set(), 0
    for name in names:
        if got / total >= target:
            break
        if name == "extra":
            continue
        chosen.add(name)
        got += by_project[name]
    return chosen


def write_overlay_sheet(dst: Path, items: list[tuple[Path, Path]]) -> None:
    colours = [c for _, c in CLASSES]
    tiles = []
    for img_path, mask_path in items:
        im = Image.open(img_path).convert("RGB")
        ids = np.asarray(Image.open(mask_path))
        arr = np.asarray(im).astype(np.float32)
        for cid in range(1, len(CLASSES)):
            sel = ids == cid
            arr[sel] = 0.45 * arr[sel] + 0.55 * np.array(colours[cid], np.float32)
        tiles.append(Image.fromarray(arr.astype(np.uint8)).resize((648, 486)))
    sheet = Image.new("RGB", (648 * 3, 486 * ((len(tiles) + 2) // 3)))
    for i, t in enumerate(tiles):
        sheet.paste(t, ((i % 3) * 648, (i // 3) * 486))
    sheet.save(dst / "overlay_check.jpg", quality=80)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dst", required=True, help="output folder (recreated)")
    ap.add_argument("--extra", help="folder with images/ and masks/ (direct-colour PNG) to add to train")
    ap.add_argument("--exports", help="folder of exported semantic PNGs; masks without a photo go to missing_photos.csv")
    ap.add_argument("--scale", type=float, default=0.5, help="resize factor stored on disk (default 0.5)")
    ap.add_argument("--min-polygons", type=int, default=20, help="skip tasks with fewer polygons")
    ap.add_argument("--statuses", default=",".join(FINISHED_STATUSES),
                    help="comma-separated task statuses to keep; empty string keeps all")
    ap.add_argument("--exclude-projects", default="test,ai test", help="comma-separated project names to skip")
    ap.add_argument("--val-fraction", type=float, default=0.18)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    dst = Path(args.dst)
    statuses = {x.strip() for x in args.statuses.split(",") if x.strip()}
    exclude = {x.strip().lower() for x in args.exclude_projects.split(",") if x.strip()}
    samples = load_db_samples(args.scale, args.min_polygons, statuses, exclude)
    print(f"DB: {len(samples)} finished, annotated tasks with an image on disk")

    # The same photo is often uploaded to several projects; keep the fullest copy
    # so a duplicate cannot land on both sides of the split.
    best: dict[str, dict] = {}
    for s in samples:
        key = file_md5(s["image_path"])
        if key not in best or s["polygons"] > best[key]["polygons"]:
            best[key] = s
    print(f"after dropping duplicate photos: {len(best)}")
    samples = list(best.values())
    if args.extra:
        extra = load_extra(Path(args.extra), args.scale)
        print(f"extra: {len(extra)} pairs")
        samples += extra

    by_project = Counter(s["project"] for s in samples)
    val_projects = pick_val_projects(by_project, args.val_fraction, args.seed)
    if dst.exists():
        shutil.rmtree(dst)

    pixels = {"train": np.zeros(len(CLASSES), np.int64), "val": np.zeros(len(CLASSES), np.int64)}
    counts, written, used = Counter(), [], set()
    for s in samples:
        split = "val" if s["project"] in val_projects else "train"
        name = s["stem"]
        while (split, name) in used:  # same stem from different ships
            name += "_"
        used.add((split, name))
        (dst / "images" / split).mkdir(parents=True, exist_ok=True)
        (dst / "masks" / split).mkdir(parents=True, exist_ok=True)
        ipath = dst / "images" / split / (name + ".jpg")
        with Image.open(s["image_path"]) as im:
            if im.size == s["mask"].size and im.format == "JPEG":
                shutil.copyfile(s["image_path"], ipath)  # full size: keep the original bytes
            else:
                im.convert("RGB").resize(s["mask"].size, Image.LANCZOS).save(ipath, quality=95)
        mpath = dst / "masks" / split / (name + ".png")
        s["mask"].save(mpath)
        pixels[split] += np.bincount(np.asarray(s["mask"]).ravel(), minlength=len(CLASSES))[: len(CLASSES)]
        counts[split] += 1
        written.append((ipath, mpath))

    (dst / "classes.json").write_text(json.dumps(
        [{"id": i, "name": n, "rgb": list(c)} for i, (n, c) in enumerate(CLASSES)], indent=2), encoding="utf-8")
    print(f"validation projects: {sorted(val_projects)}")
    for split in ("train", "val"):
        share = pixels[split] / max(1, pixels[split].sum()) * 100
        print(f"{split}: {counts[split]} images | pixel share % " +
              ", ".join(f"{CLASSES[i][0]}={share[i]:.2f}" for i in range(len(CLASSES))))

    if args.exports:
        have = {Path(s["image_path"]).stem for s in samples} | {s["stem"] for s in samples}
        rows = [(p.stem, p.parent.name) for p in sorted(Path(args.exports).glob("*/*.png")) if p.stem not in have]
        with (dst / "missing_photos.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["stem", "export_folder"])
            w.writerows(rows)
        per = Counter(folder for _, folder in rows)
        print(f"exported masks without a photo in the dataset: {len(rows)} -> {dst / 'missing_photos.csv'}")
        print("  by folder: " + ", ".join(f"{k}={v}" for k, v in sorted(per.items())))

    pick = random.Random(args.seed).sample(written, min(6, len(written)))
    write_overlay_sheet(dst, pick)
    print(f"Wrote {dst} (check {dst / 'overlay_check.jpg'})")


if __name__ == "__main__":
    main()

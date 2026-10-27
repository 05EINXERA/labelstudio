"""Refine a YOLO dataset's labels with SAM, prompting it with each existing box.

Reads a YOLO dataset (detection boxes, or polygons), asks SAM for a mask inside
every label's box, and writes a new dataset next to it:

  --output boxes   detection labels tightened to the mask extent (train YOLOv8 detect)
  --output seg     polygon labels from the masks (train YOLOv8-seg)

A mask that disagrees with its box (see ml/sam_refine.py) is rejected and the
original geometry is kept, so labels never get worse. The source is untouched.
Accepts either layout the other dataset scripts produce: `images/<split>` +
`labels/<split>`, or `<split>/` holding labels beside their images. Run from
the repo root:

    python scripts/sam_refine_yolo_dataset.py --src C:\\datasets\\myproj --dst C:\\datasets\\myproj_sam --output seg
"""
import argparse
import shutil
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import yaml
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ml.common import WAND_MODEL_DIR
from ml.sam_refine import (
    format_box_line,
    format_polygon_line,
    parse_label_line,
    refine_box,
    refine_polygon,
)
from ml.weights import resolve_model_path

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp")
SPLITS = ("train", "val")


def load_class_names(src: Path) -> list[str]:
    classes_file = src / "classes.txt"
    if classes_file.is_file():
        return [c.strip() for c in classes_file.read_text(encoding="utf-8").splitlines() if c.strip()]
    data_yaml = src / "data.yaml"
    if data_yaml.is_file():
        names = yaml.safe_load(data_yaml.read_text(encoding="utf-8")).get("names", {})
        return [names[k] for k in sorted(names)] if isinstance(names, dict) else list(names)
    sys.exit(f"Error: no classes.txt or data.yaml in {src}")


def find_pairs(src: Path, split: str) -> list[tuple[Path, Path]]:
    """(label, image) pairs for one split, in either supported layout."""
    if (src / "labels" / split).is_dir():
        label_dir, image_dir = src / "labels" / split, src / "images" / split
    else:
        label_dir = image_dir = src / split
    pairs = []
    for label in sorted(label_dir.glob("*.txt")):
        image = next((p for p in image_dir.glob(label.stem + ".*") if p.suffix.lower() in IMAGE_EXTS), None)
        if image is not None:
            pairs.append((label, image))
    return pairs


def sam_masks(sam, image: Path, boxes: list[list[float]], width: int, height: int):
    """One binary uint8 mask per box at source-image size, or Nones if SAM returned nothing."""
    import cv2  # local: keeps `--help` fast, matching the other ML entry points

    results = sam(str(image), bboxes=boxes, verbose=False)
    if not results or results[0].masks is None or results[0].masks.data is None:
        return [None] * len(boxes)
    data = results[0].masks.data.cpu().numpy()
    masks = []
    for m in data[: len(boxes)]:
        m = (m > 0.5).astype(np.uint8)
        if m.shape != (height, width):
            m = cv2.resize(m, (width, height), interpolation=cv2.INTER_NEAREST)
        masks.append(m)
    return masks + [None] * (len(boxes) - len(masks))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", required=True, help="YOLO dataset folder")
    parser.add_argument("--dst", required=True, help="output folder (recreated)")
    parser.add_argument("--output", choices=("boxes", "seg"), default="seg")
    parser.add_argument("--sam", default="sam_b.pt", help="SAM weights in models/wand (default: sam_b.pt)")
    parser.add_argument("--min-iou", type=float, default=0.5, help="reject masks whose box overlaps the prompt less than this")
    parser.add_argument("--min-fill", type=float, default=0.1, help="reject masks covering less of the prompt box than this")
    parser.add_argument("--epsilon", type=float, default=0.002, help="polygon simplification, fraction of perimeter")
    parser.add_argument("--limit", type=int, help="only process the first N images per split (quick check)")
    args = parser.parse_args()

    try:
        from ultralytics import SAM
    except ImportError:
        sys.exit("Error: the 'ultralytics' package is not installed (pip install ultralytics)")

    src, dst = Path(args.src), Path(args.dst)
    classes = load_class_names(src)
    sam = SAM(resolve_model_path(args.sam, WAND_MODEL_DIR))

    if dst.exists():
        shutil.rmtree(dst)

    stats = Counter()
    for split in SPLITS:
        pairs = find_pairs(src, split)[: args.limit]
        (dst / "images" / split).mkdir(parents=True)
        (dst / "labels" / split).mkdir(parents=True)
        for label, image in pairs:
            with Image.open(image) as im:
                width, height = im.size
            parsed = [p for p in (parse_label_line(line, width, height) for line in label.read_text(encoding="utf-8").splitlines()) if p]
            out_lines = []
            if parsed:
                prompts = [[max(0.0, b[0]), max(0.0, b[1]), min(float(width), b[2]), min(float(height), b[3])] for _, b, _ in parsed]
                masks = sam_masks(sam, image, prompts, width, height)
                for (cls, _, _), prompt, mask in zip(parsed, prompts, masks):
                    if mask is None:
                        refined = False
                        points = [(prompt[0], prompt[1]), (prompt[2], prompt[1]), (prompt[2], prompt[3]), (prompt[0], prompt[3])]
                        box = tuple(prompt)
                    elif args.output == "seg":
                        points, refined = refine_polygon(mask, prompt, args.epsilon, args.min_iou, args.min_fill)
                    else:
                        box, refined = refine_box(mask, prompt, args.min_iou, args.min_fill)
                    out_lines.append(
                        format_polygon_line(cls, points, width, height)
                        if args.output == "seg"
                        else format_box_line(cls, box, width, height)
                    )
                    stats["refined" if refined else "kept_original"] += 1
            shutil.copyfile(image, dst / "images" / split / (label.stem + image.suffix.lower()))
            (dst / "labels" / split / label.name).write_text("\n".join(out_lines) + ("\n" if out_lines else ""), encoding="utf-8")
            stats[f"images_{split}"] += 1

    names = "\n".join(f"  {i}: {name}" for i, name in enumerate(classes))
    (dst / "data.yaml").write_text(
        f"path: {dst.resolve().as_posix()}\ntrain: images/train\nval: images/val\nnames:\n{names}\n",
        encoding="utf-8",
    )
    total = stats["refined"] + stats["kept_original"]
    print(f"Images: train={stats['images_train']} val={stats['images_val']}")
    if total:
        print(f"Labels: {total}. Refined by SAM: {stats['refined']} ({100 * stats['refined'] / total:.0f}%), "
              f"kept original (mask rejected): {stats['kept_original']}")
    print(f"Wrote {dst / 'data.yaml'}")


if __name__ == "__main__":
    main()

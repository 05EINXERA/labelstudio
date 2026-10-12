"""Derive a trainable YOLO segmentation dataset from an app YOLO export.

The app exports pixel-accurate polygons, so one photo holds thousands of
fragments only a few pixels wide. They cannot be learned at training
resolution and drown the validation metrics. This writes a copy of the dataset
that keeps only polygons at least `--min-px` wide and tall (in source-image
pixels), skips labels whose image is missing, and re-splits train/val. The
source folder is left untouched. Run from the repo root:

    python scripts/prepare_yolo_seg_dataset.py --src C:\\datasets\\myproj --dst C:\\datasets\\myproj_seg
"""
import argparse
import random
import shutil
import sys
from collections import Counter
from pathlib import Path

from PIL import Image

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".bmp")


def find_image(label: Path) -> Path | None:
    for candidate in label.parent.glob(label.stem + ".*"):
        if candidate.suffix.lower() in IMAGE_EXTS:
            return candidate
    return None


def filter_label(label: Path, width: int, height: int, min_px: float) -> tuple[list[str], Counter]:
    kept, classes = [], Counter()
    with label.open(encoding="utf-8") as fh:
        for line in fh:
            tokens = line.split()
            if len(tokens) < 7:
                continue
            xs = [float(v) for v in tokens[1::2]]
            ys = [float(v) for v in tokens[2::2]]
            if (max(xs) - min(xs)) * width < min_px or (max(ys) - min(ys)) * height < min_px:
                continue
            kept.append(" ".join(tokens))
            classes[int(tokens[0])] += 1
    return kept, classes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", required=True, help="dataset folder with classes.txt, train/ and val/")
    parser.add_argument("--dst", required=True, help="output folder (recreated)")
    parser.add_argument("--min-px", type=float, default=16, help="drop polygons narrower/shorter than this, in source pixels")
    parser.add_argument("--val-fraction", type=float, default=0.15)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    src, dst = Path(args.src), Path(args.dst)
    classes = [c.strip() for c in (src / "classes.txt").read_text(encoding="utf-8").splitlines() if c.strip()]

    labels = sorted(p for split in ("train", "val") for p in (src / split).glob("*.txt"))
    pairs = [(label, find_image(label)) for label in labels]
    missing = [label.stem for label, image in pairs if image is None]
    pairs = [(label, image) for label, image in pairs if image is not None]
    if not pairs:
        sys.exit(f"Error: no label in {src} has an image next to it")

    random.Random(args.seed).shuffle(pairs)
    n_val = max(1, round(len(pairs) * args.val_fraction))
    splits = {"val": pairs[:n_val], "train": pairs[n_val:]}

    if dst.exists():
        shutil.rmtree(dst)
    total_in = 0
    for split, items in splits.items():
        (dst / "images" / split).mkdir(parents=True)
        (dst / "labels" / split).mkdir(parents=True)
        counts = Counter()
        for label, image in items:
            with Image.open(image) as im:
                width, height = im.size
            with label.open(encoding="utf-8") as fh:
                total_in += sum(1 for line in fh if line.strip())
            kept, per_class = filter_label(label, width, height, args.min_px)
            counts += per_class
            shutil.copyfile(image, dst / "images" / split / (label.stem + image.suffix.lower()))
            (dst / "labels" / split / label.name).write_text("\n".join(kept) + "\n", encoding="utf-8")
        per_class = ", ".join(f"{classes[i]}={counts[i]}" for i in range(len(classes)))
        print(f"{split}: {len(items)} images, {sum(counts.values())} polygons kept ({per_class})")

    names = "\n".join(f"  {i}: {name}" for i, name in enumerate(classes))
    (dst / "data.yaml").write_text(
        f"path: {dst.resolve().as_posix()}\ntrain: images/train\nval: images/val\nnames:\n{names}\n",
        encoding="utf-8",
    )
    print(f"Source polygons: {total_in}. Labels skipped for a missing image: {len(missing)}")
    if missing:
        print("  " + " ".join(missing))
    print(f"Wrote {dst / 'data.yaml'}")


if __name__ == "__main__":
    main()

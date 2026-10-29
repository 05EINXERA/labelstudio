"""Turn an app YOLO export into an Ultralytics-ready dataset.

The app's export (Export -> format "YOLO", image output "Original image") is a
ZIP laid out as

    yolo/classes.txt            one class per line (line number = class index)
    yolo/annotations/<stem>.txt YOLOv8 segmentation labels, one file per task
    original_image/<stem>.<ext> the images (only with image output "Original")

Ultralytics wants images/{train,val}/, labels/{train,val}/ and a data.yaml, so
this script pairs labels with images by file stem, splits train/val with a
fixed seed, and writes data.yaml. Tasks without a matching image are skipped
and reported, never guessed at.

    python scripts/prepare_yolo_dataset.py --export ~/Downloads/P2-DD.zip
    python scripts/prepare_yolo_dataset.py --export export_dir --images other/images

Then train with scripts/train_yolo.py --data datasets/<name>.
"""
import argparse
import json
import posixpath
import random
import shutil
import sys
import zipfile
from pathlib import Path
from typing import Optional

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}


def read_export(path: Path):
    """Export ZIP or extracted folder -> (classes, {stem: label bytes}, {stem: image}, zip).

    The image value is (name, source) where source is a Path on disk, or None
    when the image lives inside the ZIP (read lazily by the caller). The
    trailing ZipFile is None for a folder.
    """
    classes: Optional[List[str]] = None
    labels: Dict[str, bytes] = {}
    images: Dict[str, Tuple[str, Optional[Path]]] = {}

    if path.is_dir():
        files = [(p.relative_to(path).as_posix(), p) for p in path.rglob("*") if p.is_file()]
        zf = None
    else:
        zf = zipfile.ZipFile(path)
        files = [(n, None) for n in zf.namelist() if not n.endswith("/")]

    def read(name: str, disk: Optional[Path]) -> bytes:
        return disk.read_bytes() if disk is not None else zf.read(name)

    for name, disk in files:
        base = posixpath.basename(name)
        stem, ext = posixpath.splitext(base)
        parts = name.split("/")
        if base.lower() == "classes.txt":
            classes = [l.strip() for l in read(name, disk).decode("utf-8-sig").splitlines() if l.strip()]
        elif "annotations" in parts[:-1] and ext.lower() == ".txt":
            labels[stem] = read(name, disk)
        elif ext.lower() in IMAGE_EXTS and parts[0] == "original_image":
            images[stem] = (name, disk)

    if classes is None:
        sys.exit(f"error: no classes.txt in {path}; is this a YOLO export?")
    return classes, labels, images, zf


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--export", required=True, type=Path, help="export .zip or extracted export folder")
    ap.add_argument("--images", type=Path, help="folder of images, if the export has no original_image/")
    ap.add_argument("--out", type=Path, help="output dir (default: datasets/<export name>)")
    ap.add_argument("--val-fraction", type=float, default=0.2, help="share of images used for validation (default: 0.2)")
    ap.add_argument("--seed", type=int, default=0, help="split seed (default: 0)")
    args = ap.parse_args()

    if not args.export.exists():
        sys.exit(f"error: {args.export} does not exist")
    if not 0 < args.val_fraction < 1:
        sys.exit("error: --val-fraction must be between 0 and 1")

    classes, labels, images, zf = read_export(args.export)

    if args.images:
        for p in args.images.rglob("*"):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                images.setdefault(p.stem, (p.name, p))
    if not images:
        sys.exit(
            "error: the export contains no images. Re-export with image output "
            '"Original image", or pass --images <folder>.'
        )

    stems = sorted(s for s in labels if s in images)
    missing = sorted(s for s in labels if s not in images)
    if missing:
        print(f"warning: {len(missing)} labelled task(s) have no image and were skipped: {', '.join(missing[:5])}"
              + (" ..." if len(missing) > 5 else ""))
    if len(stems) < 2:
        sys.exit("error: need at least 2 labelled images to make a train/val split")

    rng = random.Random(args.seed)
    rng.shuffle(stems)
    n_val = max(1, round(len(stems) * args.val_fraction))
    split = {"val": stems[:n_val], "train": stems[n_val:]}

    out = args.out or Path("datasets") / args.export.stem
    if out.exists():
        shutil.rmtree(out)
    for kind, group in split.items():
        (out / "images" / kind).mkdir(parents=True)
        (out / "labels" / kind).mkdir(parents=True)
        for stem in group:
            name, disk = images[stem]
            dest = out / "images" / kind / (stem + posixpath.splitext(name)[1].lower())
            if disk is not None:
                shutil.copyfile(disk, dest)
            else:
                dest.write_bytes(zf.read(name))
            (out / "labels" / kind / f"{stem}.txt").write_bytes(labels[stem])

    names = "\n".join(f"  {i}: {json.dumps(n, ensure_ascii=False)}" for i, n in enumerate(classes))
    (out / "data.yaml").write_text(
        f"path: {out.resolve().as_posix()}\ntrain: images/train\nval: images/val\nnames:\n{names}\n",
        encoding="utf-8",
    )
    print(f"{len(split['train'])} train / {len(split['val'])} val images, {len(classes)} classes -> {out / 'data.yaml'}")


if __name__ == "__main__":
    main()

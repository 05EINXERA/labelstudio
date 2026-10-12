"""Turn an extracted app YOLO export into a trainable Ultralytics dataset.

The app's YOLO export holds only labels (`classes.txt` plus `train/*.txt` and
`val/*.txt`). This copies each label's source image next to it (same stem) and
writes `data.yaml`, so `scripts/train_yolo.py --data <folder>` works.

Images are found through the database: a label's stem is the task's uploaded
filename (`formats.common.safe_stem`). Run from the repo root:

    python scripts/build_yolo_dataset.py --data C:\\datasets\\myproj [--project 3]
"""
import argparse
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import models
from config import DATA_DIR
from database import SessionLocal
from formats.common import safe_stem

SPLITS = ("train", "val")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True, help="extracted YOLO export folder")
    parser.add_argument("--project", type=int, help="restrict the lookup to one project id")
    args = parser.parse_args()

    root = Path(args.data)
    classes_file = root / "classes.txt"
    if not classes_file.is_file():
        sys.exit(f"Error: {classes_file} not found - is this an app YOLO export?")
    classes = [line.strip() for line in classes_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    db = SessionLocal()
    try:
        query = db.query(models.Task).filter(models.Task.image_path.isnot(None))
        if args.project is not None:
            query = query.filter(models.Task.project_id == args.project)
        by_stem = {}
        for task in query:
            by_stem.setdefault(safe_stem(task), []).append(task)
    finally:
        db.close()

    copied = missing = ambiguous = 0
    for split in SPLITS:
        for label in sorted((root / split).glob("*.txt")):
            tasks = by_stem.get(label.stem, [])
            if not tasks:
                print(f"No task found for {split}/{label.name}")
                missing += 1
                continue
            if len(tasks) > 1:
                print(f"Ambiguous: {len(tasks)} tasks named {label.stem} (use --project); using newest")
                ambiguous += 1
            task = max(tasks, key=lambda t: t.id)
            src = os.path.join(DATA_DIR, *task.image_path.split("/"))
            if not os.path.isfile(src):
                print(f"Image file missing on disk for {label.name}: {src}")
                missing += 1
                continue
            ext = os.path.splitext(src)[1] or ".jpg"
            shutil.copyfile(src, label.with_suffix(ext))
            copied += 1

    names = "\n".join(f"  {i}: {name}" for i, name in enumerate(classes))
    (root / "data.yaml").write_text(
        f"path: {root.resolve().as_posix()}\ntrain: train\nval: val\nnames:\n{names}\n",
        encoding="utf-8",
    )
    print(f"Copied {copied} images, {missing} missing, {ambiguous} ambiguous. Wrote {root / 'data.yaml'}")


if __name__ == "__main__":
    main()

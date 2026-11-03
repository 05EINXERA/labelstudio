r"""Retrain the hull model from the app's finished tasks and install it.

Started by the Hull "Train" button (api/hull_training.py) as a separate
process, so the GPU and a crash stay out of the web server. Three steps, each
a script that also works by hand:

    1. build_semseg_dataset.py  finished tasks in the database -> image/mask pairs
    2. train_semseg.py          U-Net/ResNet-34, recipe of hull_v6
    3. copy best.pt to models/detector/hull_semseg_<name>.pt

The new file shows up in the Hull model picker by itself; the default model is
not touched. Progress goes to <runs>/status.json, which the web server reads.

    venv\Scripts\python.exe scripts\train_hull_from_app.py --name hull_app_20261103_1200
"""
import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import config
from ml.common import DETECTOR_MODEL_DIR

logger = logging.getLogger("train_hull_from_app")

DEFAULT_RUNS = REPO / "runs" / "hull_app"
# Fewest training photos worth a run: below this the model would mostly learn noise.
MIN_TRAIN_PHOTOS = 20
EPOCH_LINE = re.compile(r"^epoch (\d+)/(\d+) loss [\d.]+ defect-mIoU ([\d.]+)( \*)?")
SPLIT_LINE = re.compile(r"^(train|val): (\d+) images")


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Status:
    """status.json, rewritten atomically so the server never reads half a file."""

    def __init__(self, path: Path, name: str):
        self.path = path
        self.data = {"state": "running", "stage": "building dataset", "name": name, "started_at": now(),
                     "message": None, "epoch": None, "epochs": config.HULL_TRAIN_EPOCHS, "best": None,
                     "last": None, "train_photos": None, "val_photos": None, "finished_at": None}

    def update(self, **fields) -> None:
        self.data.update(fields)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data), encoding="utf-8")
        os.replace(tmp, self.path)


def run_step(command: list[str], log, on_line) -> int:
    """Run a script, copying its output to the log and each line to `on_line`."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    process = subprocess.Popen(command, cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace")
    for line in process.stdout:
        log.write(line)
        log.flush()
        on_line(line.rstrip())
    return process.wait()


def tail(path: Path, lines: int = 3) -> str:
    try:
        return " | ".join(path.read_text(encoding="utf-8", errors="replace").strip().splitlines()[-lines:])
    except OSError:
        return ""


def install(best: Path, name: str) -> Path:
    """Put best.pt in the picker's folder, whole or not at all."""
    import torch
    checkpoint = torch.load(best, map_location="cpu", weights_only=True)
    missing = [key for key in ("model", "classes", "name") if key not in checkpoint]
    if missing:
        raise RuntimeError(f"{best} is missing {missing}")
    target = Path(DETECTOR_MODEL_DIR) / f"hull_semseg_{name}.pt"
    partial = target.with_name(target.name + ".part")  # not *.pt, so the picker ignores it
    shutil.copyfile(best, partial)
    os.replace(partial, target)
    return target


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--name", required=True, help="run name; also the model's name in the picker")
    ap.add_argument("--runs", default=str(DEFAULT_RUNS), help="folder holding status.json and one folder per run")
    args = ap.parse_args()

    runs = Path(args.runs)
    run_dir = runs / args.name
    data_dir, train_dir = run_dir / "data", run_dir / "train"
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "log.txt"
    status = Status(runs / "status.json", args.name)
    status.update()

    try:
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("No GPU available: training on the CPU would take days.")
        with log_path.open("a", encoding="utf-8") as log:
            def on_build(line):
                match = SPLIT_LINE.match(line)
                if match:
                    status.update(**{f"{match.group(1)}_photos": int(match.group(2))})

            build = [sys.executable, "scripts/build_semseg_dataset.py", "--dst", str(data_dir), "--scale", "1.0",
                     "--merge", "dirt1+dirt2=dirt"]
            if config.HULL_TRAIN_VAL_PROJECTS:
                build += ["--val-projects", config.HULL_TRAIN_VAL_PROJECTS]
            if run_step(build, log, on_build) != 0:
                raise RuntimeError(f"Building the dataset failed: {tail(log_path)}")
            if (status.data["train_photos"] or 0) < MIN_TRAIN_PHOTOS or not status.data["val_photos"]:
                raise RuntimeError(f"Not enough finished photos to train: {status.data['train_photos']} train, "
                                   f"{status.data['val_photos']} validation.")

            status.update(stage="training")

            def on_train(line):
                match = EPOCH_LINE.match(line)
                if match:
                    last = float(match.group(3))
                    best = max(status.data["best"] or 0.0, last)
                    status.update(epoch=int(match.group(1)), epochs=int(match.group(2)), last=last, best=best)

            train = [sys.executable, "scripts/train_semseg.py", "--data", str(data_dir), "--name", args.name,
                     "--out", str(train_dir), "--epochs", str(config.HULL_TRAIN_EPOCHS),
                     "--rare-classes", "exposed_steel_plate,marine_organisms,rust",
                     "--workers", str(config.HULL_TRAIN_WORKERS)]
            if run_step(train, log, on_train) != 0:
                raise RuntimeError(f"Training failed: {tail(log_path)}")

            status.update(stage="installing")
            target = install(train_dir / args.name / "best.pt", args.name)
        shutil.rmtree(data_dir, ignore_errors=True)  # rebuilt next run; the photos are copies
        status.update(state="done", stage=None, message=f"Installed {target.name}", finished_at=now())
        return 0
    except Exception as error:
        logger.error("Hull training failed: %s", traceback.format_exc())
        with log_path.open("a", encoding="utf-8") as log:
            log.write(traceback.format_exc())
        status.update(state="failed", stage=None, message=str(error)[:500], finished_at=now())
        return 1


if __name__ == "__main__":
    sys.exit(main())

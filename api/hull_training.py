"""Start, watch and cancel the hull-model training process.

Training runs in its own process (scripts/train_hull_from_app.py) because it
needs the whole GPU for hours. The process reports through runs/hull_app/
status.json and is found again after a web-server restart by the pid in
run.json, so a restart neither loses sight of a run nor allows a second one.
Like the AI job queue this is single-process state (CLAUDE.md rule 9).
"""
import json
import logging
import os
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

import psutil

from ml import release_gpu_models

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO / "runs" / "hull_app"
SCRIPT = "train_hull_from_app.py"

_lock = threading.Lock()


class TrainingBusy(Exception):
    """A run is already going."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, path)


def _running_process():
    """The live training process (matched by pid, start time and command line), or None."""
    info = _read(RUNS_DIR / "run.json")
    if not info:
        return None
    try:
        process = psutil.Process(info["pid"])
        if abs(process.create_time() - info["create_time"]) > 1 or SCRIPT not in " ".join(process.cmdline()):
            return None
        return process if process.is_running() and process.status() != psutil.STATUS_ZOMBIE else None
    except (psutil.Error, KeyError):
        return None


def is_training() -> bool:
    return _running_process() is not None


def status() -> dict:
    """Latest run's progress; a run whose process vanished without finishing is "failed"."""
    data = _read(RUNS_DIR / "status.json")
    if data is None:
        return {"state": "idle"}
    if data.get("state") == "running" and _running_process() is None:
        data = {**data, "state": "failed", "stage": None, "finished_at": _now(),
                "message": "Training stopped unexpectedly (the server or the PC was restarted?)."}
    return data


def start() -> dict:
    """Free the GPU and launch a run. Raises TrainingBusy when one is already going."""
    with _lock:
        if is_training():
            raise TrainingBusy("A training run is already in progress.")
        name = "hull_app_" + datetime.now().strftime("%Y%m%d_%H%M")
        release_gpu_models()
        # Written before the child starts so a poll never sees the previous run's result.
        _write(RUNS_DIR / "status.json", {"state": "running", "stage": "building dataset", "name": name,
                                          "started_at": _now()})
        flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        process = subprocess.Popen(
            [sys.executable, str(REPO / "scripts" / SCRIPT), "--name", name, "--runs", str(RUNS_DIR)],
            cwd=REPO, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=flags,
        )
        _write(RUNS_DIR / "run.json", {"pid": process.pid, "create_time": psutil.Process(process.pid).create_time()})
        logger.info("Hull training %s started (pid %s)", name, process.pid)
        return status()


def cancel() -> dict:
    """Stop the run, including the dataset/training scripts it started."""
    with _lock:
        process = _running_process()
        if process is None:
            return status()
        for victim in [*process.children(recursive=True), process]:
            try:
                victim.kill()
            except psutil.NoSuchProcess:
                continue
        data = _read(RUNS_DIR / "status.json") or {}
        _write(RUNS_DIR / "status.json", {**data, "state": "cancelled", "stage": None, "message": "Cancelled.",
                                          "finished_at": _now()})
        logger.info("Hull training cancelled")
        return status()

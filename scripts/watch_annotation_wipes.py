"""Detect a task's annotation count dropping unexpectedly since the last check.

Backups catch a wipe eventually, but only once someone notices and asks for a
restore — which took days for the 2026-08-23 pool-exhaustion incident. This
runs cheaply and often (every few minutes via Task Scheduler) and writes a
FAIL the moment any task's count drops, so a wipe is caught in minutes instead
of via a user report days later. It does not page or message anyone — same
low-tech status-file pattern as backup.py / health-check.ps1
(06_RESILIENCE_PLAN.md P7): a human or another script checks the file.

It has no opinion about *why* a count dropped — the wipe guard in
api/routers/tasks.py should make the known cause impossible going forward, but
this exists to catch whatever the guard doesn't (a bug in a new code path, a
manual DB mistake, a bad migration). Independent detection, independent of the
fix.

    python scripts/watch_annotation_wipes.py
    python scripts/watch_annotation_wipes.py --min-drop 25

State (previous per-task counts) is kept in $DATA_DIR/logs/annotation_watch_state.json.
Status (last run's outcome, for a human or scheduler to check) is written to
$DATA_DIR/logs/last_annotation_watch_status.json — same convention as
last_backup_status.json and last_health_status.json.
"""
import argparse
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from config import DATA_DIR  # noqa: E402
from database import SessionLocal  # noqa: E402
import models  # noqa: E402

STATE_FILENAME = "annotation_watch_state.json"
STATUS_FILENAME = "last_annotation_watch_status.json"

# A drop of at least this many annotations is flagged. Below this, ordinary
# single-annotation deletes during normal editing would flag constantly.
DEFAULT_MIN_DROP = 10


def _logs_dir() -> str:
    path = os.path.join(DATA_DIR, "logs")
    os.makedirs(path, exist_ok=True)
    return path


def _load_state(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        # A corrupt state file must not crash the watchdog or block detection
        # forever; starting from empty just means this run cannot compare
        # against history and reports no drops, which is safe (no false FAIL).
        return {}


def _current_counts(db) -> dict:
    rows = (
        db.query(models.Annotation.task_id, models.Task.description, models.Task.assignee)
        .join(models.Task, models.Task.id == models.Annotation.task_id)
        .all()
    )
    counts: dict = {}
    meta: dict = {}
    for task_id, description, assignee in rows:
        counts[task_id] = counts.get(task_id, 0) + 1
        meta[task_id] = (description, assignee)
    return counts, meta


def check(min_drop: int) -> tuple[bool, str, list]:
    logs_dir = _logs_dir()
    state_path = os.path.join(logs_dir, STATE_FILENAME)
    previous = _load_state(state_path)

    db = SessionLocal()
    try:
        current, meta = _current_counts(db)
        # Tasks that exist but currently have zero annotations are absent from
        # `current` (the join produces no row for them); still need to detect
        # THEIR wipe, so pull every task id that had a nonzero count before.
        all_task_ids = set(current) | {int(k) for k in previous}

        drops = []
        for task_id in all_task_ids:
            before = previous.get(str(task_id))
            after = current.get(task_id, 0)
            if before is None:
                continue  # first time seeing this task; nothing to compare
            if before - after >= min_drop:
                description, assignee = meta.get(task_id, (None, None))
                drops.append({
                    "task_id": task_id,
                    "before": before,
                    "after": after,
                    "description": description,
                    "assignee": assignee,
                })
    finally:
        db.close()

    new_state = {str(k): v for k, v in current.items()}
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(new_state, f, indent=2)

    if drops:
        detail = "; ".join(
            f"task {d['task_id']} ({d['description']}, {d['assignee']}): "
            f"{d['before']} -> {d['after']}"
            for d in drops
        )
        return False, detail, drops
    return True, f"no drops >= {min_drop}; {len(current)} annotated tasks tracked", []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--min-drop", type=int, default=DEFAULT_MIN_DROP,
                        help=f"Flag a task whose count drops by at least this much (default {DEFAULT_MIN_DROP}).")
    args = parser.parse_args()

    ok, detail, drops = check(args.min_drop)

    status_path = os.path.join(_logs_dir(), STATUS_FILENAME)
    payload = {
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "ok": ok,
        "detail": detail,
        "drops": drops,
    }
    with open(status_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    if ok:
        print(f"[OK] {detail}")
        return 0
    print(f"[FAIL] Annotation count dropped unexpectedly: {detail}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

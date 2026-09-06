"""
Restore one task's annotations back into the live database from a JSON file
(typically extracted from an hourly pg_dump snapshot in E:/annotation-backups).

Written for the 2026-08-06 incident on task 707: a stale undo stack let a fresh
Ctrl+Z restore an empty annotation array over 34 hydrated polygons and save the
wipe. The frontend fix is in frontend/js/state.js + canvas/interactions.js; this
script is the data-recovery half.

**This is now the only restore path.** `--from-history` and `--list-history` are
gone with `task_annotation_history`, which was removed because it cost ~1,325 ms
of GIL-held CPU and a 22 MB row write per save on the largest task
(.devnotes/remove-annotation-history/). Recovery is correspondingly coarser: the
hourly backup, rather than the last 20 saves of one task.

It is deliberately conservative:
  * dumps the task's CURRENT annotations to a rollback file before writing,
  * validates that every labelId in the payload still exists on the project,
  * refuses to shrink a task unless --allow-shrink is passed,
  * shows a diff and requires --commit to actually write (dry-run by default).

Restoring is deliberately a human decision, never automatic: the server cannot
tell a wipe from a genuine delete-all (both arrive as `[]` against a task that
had work), and guessing is what caused the incident this guards against.

Extracting a task's annotations from a backup dump is the caller's job; the
result is a JSON array of annotation objects, which is what --file takes.

Usage (dry run first, always):
    python scripts/restore_task_annotations.py --task 707 --file recovered.json
    python scripts/restore_task_annotations.py --task 707 --file recovered.json --commit
"""
import argparse
import datetime
import json
import os
import pathlib
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select  # noqa: E402

import models  # noqa: E402
from database import SessionLocal, commit_with_retry  # noqa: E402
from formats.annotation_rows import (  # noqa: E402
    rows_to_dicts,
    sync_task_annotations_for_project,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task", type=int, required=True, help="task id to restore")
    parser.add_argument(
        "--file", required=True, help="JSON file holding the annotation array"
    )
    parser.add_argument("--commit", action="store_true", help="actually write (default: dry run)")
    parser.add_argument(
        "--allow-shrink",
        action="store_true",
        help="permit a restore that leaves fewer objects than the task has now",
    )
    parser.add_argument(
        "--rollback-dir",
        default="backups",
        help="where to write the pre-restore copy of the current annotations",
    )
    args = parser.parse_args()

    payload_path = pathlib.Path(args.file)
    try:
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: could not read {payload_path}: {exc}")
        return 1
    payload_source = str(payload_path)

    if not isinstance(payload, list):
        print(f"ERROR: expected a JSON array of annotations, got {type(payload).__name__}")
        return 1
    if not payload and not args.allow_shrink:
        print("ERROR: payload is empty. Pass --allow-shrink if you really mean to clear the task.")
        return 1

    db = SessionLocal()
    try:
        task = db.execute(
            select(models.Task).where(models.Task.id == args.task)
        ).scalar_one_or_none()
        if task is None:
            print(f"ERROR: task {args.task} does not exist.")
            return 1

        # Read from the annotation ROWS, which is what the application serves.
        # `task.annotations` is the dead legacy blob (CLAUDE.md rule 11b):
        # reading it here would compare the payload against a value no client
        # has seen since the normalisation cutover, and the shrink guard below
        # would be measuring the wrong thing.
        current = rows_to_dicts(task.annotation_rows)

        print(f"task {task.id}  project {task.project_id}  status {task.status}")
        print(f"  current : {len(current)} object(s), updated_at {task.updated_at}")
        print(f"  incoming: {len(payload)} object(s), from {payload_source}")

        if len(payload) < len(current) and not args.allow_shrink:
            print(
                f"ERROR: restore would drop {len(current) - len(payload)} object(s). "
                "Re-run with --allow-shrink if that is intended."
            )
            return 1

        # Every class referenced must still exist on this project, otherwise the
        # restored shapes come back unlabelled and the annotator has to redo the
        # tagging by hand.
        known = {
            row for (row,) in db.execute(
                select(models.Label.id).where(models.Label.project_id == task.project_id)
            )
        }
        used = {a.get("labelId") for a in payload if isinstance(a, dict) and a.get("labelId")}
        missing = used - known
        if missing:
            print(f"ERROR: payload references label ids absent from project {task.project_id}: {sorted(missing)}")
            return 1
        print(f"  classes : {len(used)} referenced, all present on project {task.project_id}")

        if not args.commit:
            print("\nDRY RUN — nothing written. Re-run with --commit to apply.")
            return 0

        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        rollback_dir = pathlib.Path(args.rollback_dir)
        rollback_dir.mkdir(parents=True, exist_ok=True)
        rollback = rollback_dir / f"task{task.id}-pre-restore-{stamp}.json"
        rollback.write_text(json.dumps(current, indent=2), encoding="utf-8")
        print(f"\nrollback copy written to {rollback}")

        # Write the ROWS. This is the fix for a bug that made this script a
        # no-op: it used to assign only `task.annotations`, the dead legacy
        # blob, so a restore printed "RESTORED" and changed nothing the
        # application reads. See .devnotes/remove-annotation-history/
        # 01_ANALYSIS.md § 4.
        sync_task_annotations_for_project(db, task, payload)
        # The legacy blob is kept in step while it still exists (it remains the
        # normalisation's rollback path), but it is no longer what is restored.
        task.annotations = json.dumps(payload)
        task.updated_at = datetime.datetime.now(datetime.timezone.utc)
        # Force every open tab to lose the conflict race and re-read: the
        # per-tab client_id model (CLAUDE.md rule 11) only raises a 409 when a
        # *different* client wrote last, so clearing it means the next save from
        # any tab holding the wiped state is challenged instead of silently
        # overwriting the restore.
        task.last_client_id = None
        commit_with_retry(db)
        print(f"RESTORED task {task.id}: {len(current)} -> {len(payload)} object(s)")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

"""
Find tasks whose annotations were wiped or sharply reduced by a save.

This is the half an hourly backup cannot do at all. A pg_dump snapshot lets you
recover a task once you know it was damaged; nothing in a snapshot tells you it
happened. Loss therefore stays silent until a human notices — possibly days
later, by which point finding the last good version means picking through
twenty snapshots by hand.

Reads `task_annotation_history` (see .devnotes/task-history/01_DESIGN.md), which
records what each task held immediately before a replacing write, along with
the count the replacing write carried. A row where a non-empty blob was
replaced by an empty one IS a wipe, recorded at the moment it happened.

Read-only. It changes nothing; hand anything it finds to
scripts/restore_task_annotations.py.

Usage:
    python scripts/find_annotation_loss.py                       # wipes, last 7 days
    python scripts/find_annotation_loss.py --since 2026-08-11
    python scripts/find_annotation_loss.py --days 30 --project 270
    python scripts/find_annotation_loss.py --min-drop 50         # also partial losses
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import select  # noqa: E402

import models  # noqa: E402
from database import SessionLocal  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since",
        help="only consider writes at or after this date (YYYY-MM-DD). Overrides --days.",
    )
    parser.add_argument(
        "--days", type=int, default=7, help="look back this many days (default 7)"
    )
    parser.add_argument("--project", type=int, help="restrict to one project id")
    parser.add_argument(
        "--min-drop",
        type=float,
        default=None,
        help="also report partial losses: writes that removed at least this "
             "percent of a task's objects. Without it, only full wipes "
             "(non-empty -> empty) are reported.",
    )
    args = parser.parse_args()

    if args.since:
        try:
            cutoff = datetime.datetime.strptime(args.since, "%Y-%m-%d").replace(
                tzinfo=datetime.timezone.utc
            )
        except ValueError:
            print(f"ERROR: --since must be YYYY-MM-DD, got {args.since!r}")
            return 1
    else:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
            days=args.days
        )

    db = SessionLocal()
    try:
        stmt = (
            select(models.TaskAnnotationHistory)
            .where(
                models.TaskAnnotationHistory.created_at >= cutoff,
                # Something was actually lost: the superseded blob held work.
                models.TaskAnnotationHistory.annotation_count > 0,
            )
            .order_by(models.TaskAnnotationHistory.created_at.desc())
        )
        rows = list(db.execute(stmt).scalars())

        findings = []
        for row in rows:
            before, after = row.annotation_count, row.replaced_with_count
            is_wipe = after == 0
            drop_pct = ((before - after) / before * 100) if before > 0 else 0.0
            is_partial = (
                args.min_drop is not None and not is_wipe and drop_pct >= args.min_drop
            )
            if not (is_wipe or is_partial):
                continue

            task = db.get(models.Task, row.task_id)
            if task is None:
                continue  # task deleted since; CASCADE will clear the row
            if args.project is not None and task.project_id != args.project:
                continue
            findings.append((row, task, before, after, drop_pct, is_wipe))

        window = args.since or f"last {args.days} day(s)"
        if not findings:
            print(f"No annotation loss recorded since {window}.")
            print(
                "\nNote: history only covers writes since the deploy that added it, "
                "and only writes that changed the annotations."
            )
            return 0

        wipes = sum(1 for f in findings if f[5])
        print(f"{len(findings)} event(s) since {window} — {wipes} full wipe(s).\n")
        print(
            f"{'task':>7}  {'project':>7}  {'before':>6}  {'after':>5}  "
            f"{'drop':>6}  {'when':<22}  who / client"
        )
        for row, task, before, after, drop_pct, is_wipe in findings:
            user = (
                db.get(models.User, row.replaced_by_user_id)
                if row.replaced_by_user_id
                else None
            )
            mark = "WIPE" if is_wipe else f"{drop_pct:.0f}%"
            print(
                f"{task.id:>7}  {task.project_id:>7}  {before:>6}  {after:>5}  "
                f"{mark:>6}  {row.created_at:%Y-%m-%d %H:%M:%S}  "
                f"{(user.username if user else '?')} / {row.client_id or '?'}"
            )

        print("\nInspect and restore (dry run by default):")
        first = findings[0][1].id
        print(f"  python scripts/restore_task_annotations.py --task {first} --list-history")
        print(f"  python scripts/restore_task_annotations.py --task {first} --from-history")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

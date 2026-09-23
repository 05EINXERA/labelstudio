"""Roll up finished attendance days, then prune raw observations past retention.

    python scripts/rollup_attendance.py                    # yesterday + today
    python scripts/rollup_attendance.py --days 7           # the last 7 local days
    python scripts/rollup_attendance.py --date 2026-09-20  # one day
    python scripts/rollup_attendance.py --no-prune         # roll up only
    python scripts/rollup_attendance.py --dry-run

**The order is the whole point, and it is not configurable.** Raw observations
are pruned at 31 days; past that the `attendance_days` rollup is the *only*
surviving record. So:

1. The rollup runs first, and
2. **a failed rollup prunes nothing** — the prune is skipped entirely if any
   day failed to roll up, rather than being attempted for the days that
   worked. Deleting raw rows whose rollup did not land destroys history
   silently, and nothing would notice until someone asked about a month that
   is now empty.

The prune is the **only** deleter of `attendance_observations` anywhere. The
table is otherwise strictly append-only in application code: a corrected break
is a new row pair, never an edit (Q24). If you are adding a second DELETE, stop
— that is the invariant this feature is built on.

Re-runnable and idempotent: the rollup UPSERTs on `uq_attendance_day`, which is
also what lets a day be re-rolled after a manual break is backdated into it
(R10, Q25).
"""
import argparse
import os
import sys
from datetime import date, datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import func  # noqa: E402

import config  # noqa: E402
import models  # noqa: E402
from api import attendance_report as report  # noqa: E402
from database import SessionLocal, commit_with_retry  # noqa: E402
from logging_service import log_event  # noqa: E402

# Raw observations older than this are deleted once their day is rolled up.
# Answered by Q1 ("a month is enough"), which caps the table at ~372,000 rows.
RETENTION_DAYS = 31


def rollup_day(db, local_date: date, dry_run: bool = False) -> int:
    """Write one local day's `attendance_days` rows. Returns the row count.

    UPSERT semantics by hand rather than a dialect-specific ON CONFLICT: the
    app runs on SQLite in development and Postgres in production, and a
    read-then-write is correct on both. The rollup runs once a day over ~25
    rows, so the extra SELECT costs nothing worth optimising.
    """
    start, end = report.day_bounds(local_date)

    rows = (
        db.query(models.AttendanceObservation)
        .filter(
            models.AttendanceObservation.seen_at >= start,
            models.AttendanceObservation.seen_at < end,
        )
        .order_by(models.AttendanceObservation.seen_at)
        .all()
    )
    if not rows:
        return 0

    by_user = {}
    for observation in report.observation_dicts(rows):
        by_user.setdefault(observation["user_id"], []).append(observation)

    usernames = dict(
        db.query(models.User.id, models.User.username)
        .filter(models.User.id.in_([u for u in by_user if u is not None]))
        .all()
    )

    review_counts = dict(
        db.query(models.TaskReview.reviewer_id, func.count(models.TaskReview.id))
        .filter(
            models.TaskReview.created_at >= start,
            models.TaskReview.created_at < end,
        )
        .group_by(models.TaskReview.reviewer_id)
        .all()
    )

    written = 0
    for user_id, observations in by_user.items():
        summary = report.summarise_day(
            observations, local_date, reviews=review_counts.get(user_id, 0)
        )
        if summary is None:
            continue

        # Denormalised so a kept row stays readable after the account is gone
        # (Q30). A null user_id with no name is storage without evidence.
        username = usernames.get(user_id) or f"user-{user_id}"

        existing = (
            db.query(models.AttendanceDay)
            .filter(
                models.AttendanceDay.user_id == user_id,
                models.AttendanceDay.local_date == local_date,
                models.AttendanceDay.instance_id == config.ATTENDANCE_INSTANCE_ID,
            )
            .first()
        )

        values = {
            "username": username,
            "first_seen": summary["first_seen"],
            "last_seen": summary["last_seen"],
            "end_reason": summary["end_reason"],
            "session_count": summary["session_count"],
            "present_seconds": summary["present_seconds"],
            "break_seconds": summary["break_seconds"],
            "manual_break_seconds": summary["manual_break_seconds"],
            "active_seconds": summary["active_seconds"],
            "tasks_touched": summary["tasks_touched"],
            "tasks_reviewed": summary["tasks_reviewed"],
        }

        if dry_run:
            written += 1
            continue

        if existing is None:
            db.add(models.AttendanceDay(
                user_id=user_id,
                local_date=local_date,
                instance_id=config.ATTENDANCE_INSTANCE_ID,
                **values,
            ))
        else:
            # The one legitimate UPDATE in the feature, and it is on the
            # *rollup*, not on the observations. Re-rolling must overwrite: a
            # day gains rows as the day goes on, and R10 re-rolls a day after
            # a backdated break, so an accumulating write would double-count.
            for key, value in values.items():
                setattr(existing, key, value)
        written += 1

    if not dry_run:
        commit_with_retry(db)  # rule 10, never raw db.commit()
    return written


def prune(db, before: date, dry_run: bool = False) -> int:
    """Delete raw observations older than `before` (a local date).

    The only deleter of this table anywhere. Call it **after** a successful
    rollup, never before and never instead.
    """
    cutoff, _ = report.day_bounds(before)
    query = db.query(models.AttendanceObservation).filter(
        models.AttendanceObservation.seen_at < cutoff
    )
    count = query.count()
    if count and not dry_run:
        query.delete(synchronize_session=False)
        commit_with_retry(db)
    return count


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Roll up attendance days, then prune raw observations.",
    )
    parser.add_argument("--date", help="Roll up this one local day (YYYY-MM-DD).")
    parser.add_argument(
        "--days", type=int, default=2,
        help="Roll up the last N local days, ending today. Default 2 "
             "(yesterday and today), so a run that is missed one night is "
             "repaired by the next one rather than leaving a hole.",
    )
    parser.add_argument(
        "--retention-days", type=int, default=RETENTION_DAYS,
        help=f"Delete raw observations older than this many days (default "
             f"{RETENTION_DAYS}).",
    )
    parser.add_argument(
        "--no-prune", action="store_true",
        help="Roll up without pruning. Safe in every direction: the rollup is "
             "idempotent and the raw rows simply stay.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be written and deleted, and change nothing.",
    )
    args = parser.parse_args()

    today = datetime.now(report.site_tz()).date()
    if args.date:
        days = [date.fromisoformat(args.date)]
    else:
        days = [today - timedelta(days=n) for n in range(args.days - 1, -1, -1)]

    prefix = "[dry run] " if args.dry_run else ""
    print(f"{prefix}instance {config.ATTENDANCE_INSTANCE_ID}, tz {config.ATTENDANCE_TZ}")

    db = SessionLocal()
    failures = []
    total = 0
    try:
        for day in days:
            try:
                written = rollup_day(db, day, dry_run=args.dry_run)
                total += written
                print(f"{prefix}rolled up {day}: {written} row(s)")
            except Exception as error:
                # Recorded and carried, not raised: one bad day must not stop
                # the others being rolled up. But it DOES stop the prune.
                db.rollback()
                failures.append((day, error))
                print(f"ERROR rolling up {day}: {error}", file=sys.stderr)

        if failures:
            # The safety interlock. Pruning now would delete raw rows whose
            # rollup never landed, and past retention the rollup is the only
            # record — so the loss would be silent and permanent.
            print(
                f"\n{len(failures)} day(s) failed to roll up. "
                "SKIPPING THE PRUNE: deleting raw rows whose rollup did not "
                "land would destroy history that cannot be rebuilt.",
                file=sys.stderr,
            )
            log_event(
                "attendance.rollup_failed", level="ERROR",
                days=len(failures), pruned=False,
            )
            return 1

        if args.no_prune:
            print(f"{prefix}prune skipped (--no-prune)")
        else:
            cutoff = today - timedelta(days=args.retention_days)
            deleted = prune(db, cutoff, dry_run=args.dry_run)
            print(f"{prefix}pruned {deleted} observation(s) before {cutoff}")
            log_event("attendance.prune", rows=deleted, before=str(cutoff))

        log_event("attendance.rollup", days=len(days), rows=total)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

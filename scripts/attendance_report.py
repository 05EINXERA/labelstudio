"""Print the attendance register for a day or a range, from the command line.

This is the validation step the rollout depends on (R3): it runs the *identical*
aggregation the dashboard and the nightly rollup will use, so the numbers can be
checked against a real day — and the +05:45 day bucketing verified on real
Nepali data — before any UI exists to be wrong.

    python scripts/attendance_report.py                     # today, local
    python scripts/attendance_report.py --date 2026-09-21
    python scripts/attendance_report.py --from 2026-09-01 --to 2026-09-07
    python scripts/attendance_report.py --date 2026-09-21 --user alice --sessions

Read-only: it issues SELECTs and nothing else. It does not flush the buffer
(that is a different process's in-memory state and is not visible here), so a
day being read while the app is running may lag by up to one flush interval.
"""
import argparse
import os
import sys
from datetime import date, datetime, timedelta

from sqlalchemy import func

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config  # noqa: E402
import models  # noqa: E402
from api import attendance_report as report  # noqa: E402
from database import SessionLocal  # noqa: E402


def _hours(seconds: int) -> str:
    """Decimal hours. The export stores real numbers for the same reason: a
    duration written as "7h 32m" cannot be summed."""
    return f"{seconds / 3600:6.2f}"


def _fmt_local(moment) -> str:
    if moment is None:
        return "--:--"
    return moment.astimezone(report.site_tz()).strftime("%H:%M")


def collect_day(db, local_date: date) -> list:
    """Every user's summary for one local day, newest-first by present time.

    One query for the observations and one for the review counts, regardless of
    how many users appear — never a per-user fan-out (CLAUDE.md rule 11b; the
    pattern `api/routers/projects.py::_aggregate_metrics` establishes).
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
        return []

    by_user = {}
    for observation in report.observation_dicts(rows):
        by_user.setdefault(observation["user_id"], []).append(observation)

    usernames = dict(
        db.query(models.User.id, models.User.username)
        .filter(models.User.id.in_([u for u in by_user if u is not None]))
        .all()
    )

    # Counted in SQL, grouped, not by pulling rows into Python.
    review_counts = dict(
        db.query(models.TaskReview.reviewer_id, func.count(models.TaskReview.id))
        .filter(
            models.TaskReview.created_at >= start,
            models.TaskReview.created_at < end,
        )
        .group_by(models.TaskReview.reviewer_id)
        .all()
    )

    summaries = []
    for user_id, observations in by_user.items():
        summary = report.summarise_day(
            observations, local_date, reviews=review_counts.get(user_id, 0)
        )
        if summary is None:
            continue
        summary["user_id"] = user_id
        # A deleted user's rows keep a null user_id (Q23); they stay in the
        # report rather than being hidden, labelled so.
        summary["username"] = usernames.get(user_id, f"<deleted:{user_id}>")
        summaries.append(summary)

    summaries.sort(key=lambda s: s["present_seconds"], reverse=True)
    return summaries


def print_day(summaries: list, local_date: date, show_sessions=False) -> None:
    print(f"\n{local_date}  ({config.ATTENDANCE_TZ})")
    print("=" * 104)
    if not summaries:
        print("  no observations")
        return

    print(
        f"{'user':<18}{'first':>7}{'last':>7} {'end':<8}"
        f"{'sess':>5}{'present':>9}{'active':>8}{'breaks':>8}"
        f"{'touched':>9}{'reviewed':>10}"
    )
    print("-" * 104)

    for s in summaries:
        marker = ""
        if s["manual_break_seconds"]:
            # Provenance, not suspicion (Q16).
            marker += " *entered later"
        if s["has_unended_break"]:
            marker += " *break not ended"
        print(
            f"{s['username']:<18}"
            f"{_fmt_local(s['first_seen']):>7}"
            f"{_fmt_local(s['last_seen']):>7} "
            f"{s['end_reason']:<8}"
            f"{s['session_count']:>5}"
            f"{_hours(s['present_seconds']):>9}"
            f"{_hours(s['active_seconds']):>8}"
            f"{_hours(s['break_seconds']):>8}"
            f"{s['tasks_touched']:>9}"
            f"{s['tasks_reviewed']:>10}"
            f"{marker}"
        )

        if show_sessions:
            for session in s["sessions"]:
                print(
                    f"    {_fmt_local(session['started_at'])}"
                    f"-{_fmt_local(session['ended_at'])}"
                    f"  {session['end_reason']:<8}"
                    f"{_hours(session['seconds'])}h"
                    f"  tasks={session['tasks_touched']}"
                )
                for brk in session["breaks"]:
                    state = "ended" if brk["ended"] else "NOT ENDED"
                    print(
                        f"      break {_fmt_local(brk['started_at'])}"
                        f"-{_fmt_local(brk['ended_at'])}"
                        f"  {brk['seconds'] // 60}m  {brk['source']}  {state}"
                    )

    print("-" * 104)
    print(
        f"{'TOTAL':<18}{'':>14} {'':<8}"
        f"{sum(s['session_count'] for s in summaries):>5}"
        f"{_hours(sum(s['present_seconds'] for s in summaries)):>9}"
        f"{_hours(sum(s['active_seconds'] for s in summaries)):>8}"
        f"{_hours(sum(s['break_seconds'] for s in summaries)):>8}"
        f"{sum(s['tasks_touched'] for s in summaries):>9}"
        f"{sum(s['tasks_reviewed'] for s in summaries):>10}"
    )
    print(
        "\n  present excludes declared breaks; a 'timeout' end is a lower bound,\n"
        "  not a logout time. 'touched' is not 'completed' — per-user completion\n"
        "  is not derivable (no author column on the annotation write path)."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Print the attendance register. Read-only.",
    )
    parser.add_argument("--date", help="A single local day, YYYY-MM-DD.")
    parser.add_argument("--from", dest="date_from", help="Range start, YYYY-MM-DD.")
    parser.add_argument("--to", dest="date_to", help="Range end, YYYY-MM-DD.")
    parser.add_argument("--user", help="Only this username.")
    parser.add_argument(
        "--sessions", action="store_true",
        help="Break each day down into its sessions and breaks.",
    )
    args = parser.parse_args()

    today = datetime.now(report.site_tz()).date()
    if args.date_from or args.date_to:
        if not (args.date_from and args.date_to):
            print("--from and --to must be given together.")
            return 2
        start = date.fromisoformat(args.date_from)
        end = date.fromisoformat(args.date_to)
        if end < start:
            print("--to is before --from.")
            return 2
    else:
        start = end = date.fromisoformat(args.date) if args.date else today

    print(f"instance: {config.ATTENDANCE_INSTANCE_ID}   timezone: {config.ATTENDANCE_TZ}")
    if not config.ATTENDANCE_ENABLED:
        # Worth saying plainly: an empty report during rollout almost always
        # means the flag, not a broken aggregation.
        print("NOTE: ATTENDANCE_ENABLED is off, so nothing is being captured.")

    db = SessionLocal()
    try:
        day = start
        while day <= end:
            summaries = collect_day(db, day)
            if args.user:
                summaries = [s for s in summaries if s["username"] == args.user]
            print_day(summaries, day, show_sessions=args.sessions)
            day += timedelta(days=1)
    finally:
        db.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

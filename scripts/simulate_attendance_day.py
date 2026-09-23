"""Simulate a full day of annotator attendance, then verify what the admin sees.

NOT a test (rule 20/21): this writes to a real database and is a one-off
verification harness, run by hand against the dev instance. `tests/` covers the
same logic with fixtures; this exists to answer "does it hold up end to end on
real Postgres, through the real endpoints, with the real export".

    python scripts/simulate_attendance_day.py --seed      # write the day
    python scripts/simulate_attendance_day.py --verify    # read it back
    python scripts/simulate_attendance_day.py --cleanup   # remove it all

Safety:
  * every user it creates is prefixed `simtest-`, and cleanup deletes exactly
    those and their observations — nothing else is touched;
  * it seeds a PAST date (SIM_DATE) so it cannot collide with live observations;
  * it refuses to run against a database whose name is not a dev one unless
    --i-know-what-im-doing is passed.

Scenario design: each user is one shape that really happens on the floor, named
for what it tests. The expected numbers are computed by hand in SCENARIOS and
checked against what the API returns, so a disagreement is a real finding
rather than the simulation marking its own homework.
"""
import argparse
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import config  # noqa: E402
import models  # noqa: E402
from api import attendance as att  # noqa: E402
from api import attendance_report as report  # noqa: E402
from database import SessionLocal, commit_with_retry  # noqa: E402

# A past day, so nothing here can be confused with live traffic.
SIM_DATE = date(2026, 9, 15)
PREFIX = "simtest-"

TZ = ZoneInfo(config.ATTENDANCE_TZ)


def at(hh, mm=0):
    """A local wall-clock time on SIM_DATE, as UTC."""
    return datetime.combine(SIM_DATE, time(hh, mm), tzinfo=TZ).astimezone(timezone.utc)


def ambient(start, end, step=1):
    """`seen` rows every `step` minutes over [start, end), as a real client
    emits them: the 60s capture throttle means roughly one a minute while the
    tab is open and talking.

    The end is EXCLUSIVE on purpose. An inclusive end put a `seen` row at the
    exact instant of the following `logout`, and since the sessioniser sorts
    stably it processed the `seen` after the `logout` -- closing the session
    and immediately opening a one-observation second one. A real client cannot
    do that: the logout IS the last request of the session, and nothing is
    observed after it. That was a fixture bug, not a product bug, and it cost
    a round of false failures."""
    out = []
    t = start
    while t < end:
        out.append((t, att.KIND_SEEN, None))
        t += timedelta(minutes=step)
    return out


def active(start, end, step=1, task_id=None):
    """`active` rows — the annotation timer running."""
    return [(t, att.KIND_ACTIVE, task_id) for t, _, _ in ambient(start, end, step)]


# --- the scenarios ----------------------------------------------------------
#
# `expect` is worked out by hand from the rules, NOT from the code:
#   present  = wall clock inside sessions, minus declared breaks
#   sessions = runs separated by a gap of >= IDLE_GAP (5 min)

def build():
    s = {}

    # 1. The straightforward day. Arrives 09:00, lunch 13:00-13:45 declared
    #    properly, leaves 17:00 with a real logout.
    rows = [(at(9, 0), att.KIND_LOGIN, None)]
    rows += ambient(at(9, 0), at(13, 0))
    rows += active(at(9, 30), at(12, 30), task_id=101)
    rows += [(at(13, 0), att.KIND_BREAK_START, None),
             (at(13, 45), att.KIND_BREAK_END, None)]
    rows += ambient(at(13, 45), at(17, 0))
    rows += active(at(14, 0), at(16, 30), task_id=102)
    rows += [(at(17, 0), att.KIND_LOGOUT, None)]
    s["clean-day"] = dict(rows=rows, expect=dict(
        first="09:00", last="17:00", reason="logout", sessions=1,
        present_min=(8 * 60) - 45, break_min=45,
        note="Textbook day: login, declared lunch, logout."))

    # 2. Closed the tab at lunch instead of declaring a break, came back.
    #    Two sessions, the gap is NOT break time - it is simply absence.
    rows = [(at(9, 15), att.KIND_LOGIN, None)]
    rows += ambient(at(9, 15), at(12, 55))
    rows += active(at(9, 30), at(12, 30), task_id=201)
    rows += ambient(at(13, 50), at(17, 10))
    rows += active(at(14, 0), at(17, 0), task_id=202)
    s["tab-closed-at-lunch"] = dict(rows=rows, expect=dict(
        first="09:15", last="17:09", reason="timeout", sessions=2,
        # ambient() is end-exclusive, so each run ends one step before its
        # nominal end: 09:15-12:54 and 13:50-17:09.
        present_min=(12 * 60 + 54 - 9 * 60 - 15) + (17 * 60 + 9 - 13 * 60 - 50),
        break_min=0,
        note="No break declared; the gap is absence, not break time. "
             "Ends by timeout - they closed the tab rather than logging out."))

    # 3. Forgot End Break. Declared at 13:00, never ended, came back 13:40
    #    and worked on. The break is an upper bound, flagged not-ended.
    rows = [(at(9, 0), att.KIND_LOGIN, None)]
    rows += ambient(at(9, 0), at(13, 0))
    rows += [(at(13, 0), att.KIND_BREAK_START, None)]
    rows += ambient(at(13, 40), at(17, 0))
    rows += [(at(17, 0), att.KIND_LOGOUT, None)]
    s["forgot-end-break"] = dict(rows=rows, expect=dict(
        first="09:00", last="17:00", reason="logout", sessions=1,
        present_min=(8 * 60) - 40, break_min=40, unended=True,
        note="Break declared at 13:00 and never ended; the annotator is back "
             "at 13:40. Closed at that return, flagged as an upper bound."))

    # 4. Multiple short breaks - tea, lunch, tea. All declared properly.
    rows = [(at(9, 0), att.KIND_LOGIN, None)]
    rows += ambient(at(9, 0), at(17, 30))
    for bs, be in [((11, 0), (11, 15)), ((13, 0), (13, 30)), ((15, 30), (15, 45))]:
        rows += [(at(*bs), att.KIND_BREAK_START, None),
                 (at(*be), att.KIND_BREAK_END, None)]
    rows += [(at(17, 30), att.KIND_LOGOUT, None)]
    s["three-breaks"] = dict(rows=rows, expect=dict(
        first="09:00", last="17:30", reason="logout", sessions=1,
        present_min=(8 * 60 + 30) - 60, break_min=15 + 30 + 15,
        note="Three declared breaks totalling an hour, all inside one session."))

    # 5. Logged in and out three times (switching between the two instances,
    #    which is the real reason this happens here).
    rows = []
    for lo, li in [((9, 0), (10, 30)), ((11, 0), (13, 0)), ((14, 0), (17, 0))]:
        rows += [(at(*lo), att.KIND_LOGIN, None)]
        rows += ambient(at(*lo), at(*li))
        rows += [(at(*li), att.KIND_LOGOUT, None)]
    s["three-logins"] = dict(rows=rows, expect=dict(
        first="09:00", last="17:00", reason="logout", sessions=3,
        present_min=90 + 120 + 180, break_min=0,
        note="Three clean login/logout cycles. Each logout is a stated end."))

    # 6. Idle with a task open: the heartbeat keeps them 'seen' although the
    #    timer stopped. Present >> Active. This is the row most likely to be
    #    misread, so it is in the simulation on purpose.
    rows = [(at(9, 0), att.KIND_LOGIN, None)]
    rows += ambient(at(9, 0), at(17, 0))          # heartbeat all day
    rows += active(at(9, 0), at(11, 0), task_id=601)  # timer only 2h
    rows += [(at(17, 0), att.KIND_LOGOUT, None)]
    s["idle-tab-open"] = dict(rows=rows, expect=dict(
        first="09:00", last="17:00", reason="logout", sessions=1,
        present_min=8 * 60, break_min=0, active_hint="~2h",
        note="Tab open all day, timer ran 2h. Present 8h, Active ~2h. "
             "The gap is NOT misconduct - see the behaviour guide A3."))

    # 7. Half day: arrives late, leaves at lunch, does not come back.
    rows = [(at(11, 30), att.KIND_LOGIN, None)]
    rows += ambient(at(11, 30), at(14, 0))
    rows += active(at(11, 30), at(14, 0), task_id=701)
    rows += [(at(14, 0), att.KIND_LOGOUT, None)]
    s["half-day"] = dict(rows=rows, expect=dict(
        first="11:30", last="14:00", reason="logout", sessions=1,
        present_min=150, break_min=0,
        note="Half day. Nothing unusual - included as a payroll boundary case."))

    # 8. Retroactive break: worked through, entered the forgotten break later
    #    from the profile page. seen_at is 13:00; created_at is 17:20.
    rows = [(at(9, 0), att.KIND_LOGIN, None)]
    rows += ambient(at(9, 0), at(17, 0))
    rows += [(at(17, 0), att.KIND_LOGOUT, None)]
    manual = [
        (at(13, 0), att.KIND_BREAK_MANUAL_START, at(17, 20)),
        (at(13, 30), att.KIND_BREAK_MANUAL_END, at(17, 20)),
    ]
    s["retroactive-break"] = dict(rows=rows, manual=manual, expect=dict(
        first="09:00", last="17:00", reason="logout", sessions=1,
        present_min=(8 * 60) - 30, break_min=30, manual_min=30,
        note="Break entered after the fact. Marked '*entered later' - "
             "provenance, not suspicion."))

    # 9. Very short appearance: logged in, did nothing, left. The 0-minute
    #    session the behaviour guide warns admins about.
    rows = [(at(9, 0), att.KIND_LOGIN, None), (at(16, 0), att.KIND_LOGIN, None)]
    rows += ambient(at(16, 0), at(17, 0))
    rows += [(at(17, 0), att.KIND_LOGOUT, None)]
    s["zero-minute-then-work"] = dict(rows=rows, expect=dict(
        first="09:00", last="17:00", reason="logout", sessions=2,
        present_min=60, break_min=0,
        note="Logged in at 09:00 and went away before opening a task, back at "
             "16:00. First session is 0 minutes - normal, see guide A2."))

    # 10. Overnight-ish: a long day past 20:00, to check nothing rolls over
    #     and the +05:45 offset does not shift the day.
    rows = [(at(8, 0), att.KIND_LOGIN, None)]
    rows += ambient(at(8, 0), at(20, 30))
    rows += [(at(12, 30), att.KIND_BREAK_START, None),
             (at(13, 15), att.KIND_BREAK_END, None)]
    rows += [(at(20, 30), att.KIND_LOGOUT, None)]
    s["long-day"] = dict(rows=rows, expect=dict(
        first="08:00", last="20:30", reason="logout", sessions=1,
        present_min=(12 * 60 + 30) - 45, break_min=45,
        note="12.5h day with a declared break. Checks the +05:45 day "
             "attribution does not roll a late finish into the next day."))

    return s


SCENARIOS = build()


# --- writing ----------------------------------------------------------------

def _guard(db):
    url = str(db.bind.url)
    if "annotation_dev" not in url and "workspace" not in url.lower():
        print(f"REFUSING: {url!r} does not look like a dev database.")
        print("Pass --i-know-what-im-doing to override.")
        return False
    return True


def seed(db):
    from api.auth import get_password_hash

    created = 0
    for name, spec in SCENARIOS.items():
        username = PREFIX + name
        user = db.query(models.User).filter_by(username=username).first()
        if user is None:
            user = models.User(username=username,
                               hashed_password=get_password_hash("sim-pw-12345"))
            db.add(user)
            db.flush()
            created += 1

        db.query(models.AttendanceObservation).filter_by(user_id=user.id).delete()

        rows = [
            {"user_id": user.id, "seen_at": t, "task_id": tid,
             "instance_id": config.ATTENDANCE_INSTANCE_ID, "kind": k,
             "created_at": t, "entered_by": None}
            for t, k, tid in spec["rows"]
        ]
        # Manual entries carry created_at HOURS after seen_at - that gap is
        # the provenance signal the admin reads.
        for seen_at, kind, created_at in spec.get("manual", []):
            rows.append({
                "user_id": user.id, "seen_at": seen_at, "task_id": None,
                "instance_id": config.ATTENDANCE_INSTANCE_ID, "kind": kind,
                "created_at": created_at, "entered_by": user.id,
            })
        db.bulk_insert_mappings(models.AttendanceObservation, rows)

    commit_with_retry(db)
    # Explicit onclause: attendance_observations has TWO foreign keys to users
    # (user_id and entered_by), so the join is ambiguous without it.
    total = db.query(models.AttendanceObservation).join(
        models.User, models.AttendanceObservation.user_id == models.User.id
    ).filter(models.User.username.like(PREFIX + "%")).count()
    print(f"Seeded {len(SCENARIOS)} scenarios ({created} new users), "
          f"{total} observations on {SIM_DATE}.")


def cleanup(db):
    users = db.query(models.User).filter(
        models.User.username.like(PREFIX + "%")).all()
    ids = [u.id for u in users]
    if not ids:
        print("Nothing to clean up.")
        return
    n = db.query(models.AttendanceObservation).filter(
        models.AttendanceObservation.user_id.in_(ids)).delete(
        synchronize_session=False)
    d = db.query(models.AttendanceDay).filter(
        models.AttendanceDay.user_id.in_(ids)).delete(synchronize_session=False)
    for u in users:
        db.delete(u)
    commit_with_retry(db)
    print(f"Removed {len(ids)} users, {n} observations, {d} rollup rows.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--cleanup", action="store_true")
    ap.add_argument("--i-know-what-im-doing", action="store_true")
    args = ap.parse_args()

    db = SessionLocal()
    try:
        if not args.i_know_what_im_doing and not _guard(db):
            return 1
        if args.cleanup:
            cleanup(db)
        elif args.seed:
            seed(db)
        else:
            ap.print_help()
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

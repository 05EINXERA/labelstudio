"""R3 — sessionisation, day bucketing and the +05:45 offset.

The aggregation is pure, so these tests need no server and no database. They
are the definitions in `02-requirements-definitions.md` written as assertions.

The most important test in this file — and, per the implementation brief, in
the plan — is `test_observation_after_local_midnight_lands_on_the_local_day`.
A whole-hour-offset assumption passes every test written in UTC and fails only
on real Nepali data, where the symptom looks like a rounding bug.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

import config
from api.attendance import (
    KIND_ACTIVE,
    KIND_BREAK_END,
    KIND_BREAK_MANUAL_END,
    KIND_BREAK_MANUAL_START,
    KIND_BREAK_START,
    KIND_LOGIN,
    KIND_LOGOUT,
    KIND_SEEN,
)
from api import attendance_report as report


def obs(minute, kind=KIND_SEEN, task_id=None, day=1, hour=9, created_at=None):
    """An observation `minute` MINUTES into the fixture day, UTC.

    The unit is deliberately minutes rather than something finer: these tests
    are about session boundaries, and a reader comparing `obs(0)` with
    `obs(40)` should be able to see at a glance whether that gap crosses
    `IDLE_GAP` (5 minutes). Tests that are *about* the threshold derive their
    spacing from `report.IDLE_GAP` rather than hardcoding a number, so
    retuning the constant cannot silently invert them.
    """
    return {
        "user_id": 1,
        "seen_at": datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc),
        "task_id": task_id,
        "instance_id": "test",
        "kind": kind,
        "created_at": created_at,
        "entered_by": None,
    }


# --- The +05:45 offset ------------------------------------------------------


def test_site_timezone_is_kathmandu_and_offset_is_not_a_whole_hour():
    """The premise the rest of the timezone handling rests on."""
    moment = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    offset = moment.astimezone(report.site_tz()).utcoffset()
    assert offset == timedelta(hours=5, minutes=45)
    assert offset.total_seconds() % 3600 != 0, (
        "the offset is a whole number of hours; this test guards the exact "
        "case naive date handling breaks on"
    )


def test_observation_after_local_midnight_lands_on_the_local_day():
    """00:15 in Kathmandu is 18:30 UTC the *previous* day.

    THE test. Bucketing by the UTC date — or by any whole-hour offset —
    misfiles every early-morning arrival by a full day, and the register then
    shows people arriving the evening before they did.
    """
    utc_moment = datetime(2026, 9, 20, 18, 30, tzinfo=timezone.utc)

    local = utc_moment.astimezone(report.site_tz())
    assert (local.hour, local.minute) == (0, 15)
    assert local.date() == date(2026, 9, 21)

    assert report.local_day(utc_moment) == date(2026, 9, 21), (
        "an observation just after local midnight was filed on the UTC day"
    )
    assert report.local_day(utc_moment) != utc_moment.date()


def test_observation_just_before_local_midnight_stays_on_its_day():
    """The other side of the same boundary: 23:45 local = 18:00 UTC."""
    utc_moment = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)
    local = utc_moment.astimezone(report.site_tz())
    assert (local.hour, local.minute) == (23, 45)
    assert report.local_day(utc_moment) == date(2026, 9, 20)


def test_day_bounds_span_exactly_one_local_day():
    start, end = report.day_bounds(date(2026, 9, 21))
    # Local midnight in Kathmandu is 18:15 UTC the previous day.
    assert start == datetime(2026, 9, 20, 18, 15, tzinfo=timezone.utc)
    assert end - start == timedelta(days=1)
    # The boundary is half-open, so the day's last instant is inside and the
    # next day's first is not.
    assert report.local_day(start) == date(2026, 9, 21)
    assert report.local_day(end) == date(2026, 9, 22)


def test_day_bounds_are_not_built_by_adding_a_whole_hour_offset():
    """A `timedelta(hours=5)` or `hours=6` implementation lands 45 or 15
    minutes away. This pins the exact instant so either would fail."""
    start, _ = report.day_bounds(date(2026, 9, 21))
    assert start.minute == 15, (
        f"day start is at :{start.minute:02d}; a whole-hour offset was used "
        "somewhere instead of the zone database"
    )


def test_local_day_treats_a_naive_datetime_as_utc():
    """Not the machine's local zone, which would make results depend on where
    the server runs."""
    naive = datetime(2026, 9, 20, 18, 30)
    assert report.local_day(naive) == date(2026, 9, 21)


# --- Sessionisation ---------------------------------------------------------


def test_no_observations_yields_no_sessions():
    assert report.sessionise([]) == []


def test_a_continuous_run_is_one_session():
    rows = [obs(0), obs(1), obs(2), obs(3)]
    sessions = report.sessionise(rows, now=obs(3)["seen_at"])
    assert len(sessions) == 1
    assert sessions[0]["started_at"] == rows[0]["seen_at"]


def test_a_gap_longer_than_idle_gap_splits_the_session():
    rows = [obs(0), obs(1), obs(30), obs(31)]
    sessions = report.sessionise(rows, now=obs(31)["seen_at"])
    assert len(sessions) == 2
    assert sessions[0]["end_reason"] == report.END_TIMEOUT


def test_gap_exactly_at_idle_gap_splits():
    """A gap of EXACTLY IDLE_GAP ends the session.

    This assertion was inverted until a real case exposed it. An
    annotator closed her tab at 10:50 and logged back in at 11:00 — a
    gap of exactly the then-10-minute threshold — and with a `>`
    comparison the two sessions merged into one 25-minute stretch. The
    single duration the threshold is named for was the one value it let
    through.
    """
    first = obs(0)
    second = {**obs(0), "seen_at": first["seen_at"] + report.IDLE_GAP}
    sessions = report.sessionise([first, second], now=second["seen_at"])
    assert len(sessions) == 2, (
        "a gap of exactly IDLE_GAP merged; the boundary must include the "
        "threshold itself"
    )


def test_gap_just_under_idle_gap_does_not_split():
    first = obs(0)
    second = {
        **obs(0),
        "seen_at": first["seen_at"] + report.IDLE_GAP - timedelta(seconds=1),
    }
    assert len(report.sessionise([first, second], now=second["seen_at"])) == 1


def test_a_closed_tab_and_a_later_login_are_two_sessions():
    """The production case, with the timings as reported.

    Login 10:40, observations to 10:50, tab closed. Back at 11:00, still
    working at 11:05. That must read as two sessions totalling 15
    minutes, never as one 25-minute stretch.
    """
    rows = [obs(m, hour=10) for m in range(40, 51)]
    rows += [obs(m, hour=11) for m in range(0, 6)]
    now = datetime(2026, 9, 1, 11, 5, tzinfo=timezone.utc)

    sessions = report.sessionise(rows, now=now)
    assert len(sessions) == 2, (
        f"expected two sessions, got {len(sessions)}: a ten-minute "
        "absence was absorbed into one"
    )
    assert sessions[0]["end_reason"] == report.END_TIMEOUT
    assert sessions[0]["seconds"] == 10 * 60
    assert sessions[1]["seconds"] == 5 * 60
    assert sum(s["seconds"] for s in sessions) == 15 * 60


def test_observations_are_sorted_before_sessionising():
    """The buffer merge concatenates stored and live rows, which are not
    ordered relative to each other."""
    rows = [obs(3), obs(0), obs(2), obs(1)]
    sessions = report.sessionise(rows, now=obs(3)["seen_at"])
    assert len(sessions) == 1


def test_two_tabs_interleaved_collapse_into_one_session():
    """Sessions are keyed on the user, not the tab: `client_id` is not stored
    (Q6), so two tabs are indistinguishable and must not double-count."""
    rows = [obs(0), obs(1), obs(2), obs(3), obs(4)]
    sessions = report.sessionise(rows, now=obs(4)["seen_at"])
    assert len(sessions) == 1


# --- End reasons ------------------------------------------------------------


def test_an_unclosed_session_is_reported_as_timeout_never_logout():
    """A timeout end is a lower bound. Rendering it as a logout time overstates
    the precision of an attendance record."""
    rows = [obs(0), obs(1)]
    long_after = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)
    sessions = report.sessionise(rows, now=long_after)
    assert sessions[0]["end_reason"] == report.END_TIMEOUT
    assert sessions[0]["end_reason"] != report.END_LOGOUT


def test_a_recent_session_is_open():
    rows = [obs(0), obs(1)]
    sessions = report.sessionise(rows, now=obs(1)["seen_at"] + timedelta(minutes=1))
    assert sessions[0]["end_reason"] == report.END_OPEN


def test_a_logout_closes_the_session_at_the_logout_time():
    rows = [obs(0), obs(1), obs(2, kind=KIND_LOGOUT)]
    sessions = report.sessionise(rows, now=obs(2)["seen_at"])
    assert len(sessions) == 1
    assert sessions[0]["end_reason"] == report.END_LOGOUT
    assert sessions[0]["ended_at"] == rows[-1]["seen_at"]


def test_activity_after_a_logout_starts_a_new_session():
    rows = [obs(0), obs(1, kind=KIND_LOGOUT), obs(2), obs(3)]
    sessions = report.sessionise(rows, now=obs(3)["seen_at"])
    assert len(sessions) == 2
    assert sessions[0]["end_reason"] == report.END_LOGOUT


# --- Breaks -----------------------------------------------------------------


def test_a_declared_break_does_not_split_the_session():
    """A break is a marked interval *inside* a session (§ 1.1), and its
    span (39 min here, far over the 5-minute IDLE_GAP) would otherwise
    split it."""
    rows = [obs(0), obs(1, kind=KIND_BREAK_START), obs(40, kind=KIND_BREAK_END), obs(41)]
    sessions = report.sessionise(rows, now=obs(41)["seen_at"])
    assert len(sessions) == 1
    assert len(sessions[0]["breaks"]) == 1


def test_present_time_excludes_the_break():
    rows = [obs(0), obs(1, kind=KIND_BREAK_START), obs(31, kind=KIND_BREAK_END), obs(32)]
    session = report.sessionise(rows, now=obs(32)["seen_at"])[0]
    assert session["span_seconds"] == 32 * 60
    assert session["break_seconds"] == 30 * 60
    assert session["seconds"] == 2 * 60, (
        "present time still includes the declared break"
    )


def test_an_unended_break_is_reported_not_dropped():
    """Q17. A break that vanishes silently inflates present time."""
    rows = [obs(0), obs(1, kind=KIND_BREAK_START), obs(2)]
    session = report.sessionise(rows, now=obs(2)["seen_at"])[0]
    assert session["has_unended_break"] is True
    assert len(session["breaks"]) == 1
    assert session["breaks"][0]["ended"] is False
    assert session["break_seconds"] > 0


def test_a_break_open_at_read_time_does_not_invent_presence():
    """Suspending the idle rule during a break must not run forever.

    A user who presses Take a Break and never returns would otherwise be
    reported present for every hour until the read — the founding problem,
    reappearing. The break is a lower bound flagged "not ended" instead.
    """
    rows = [obs(0), obs(0, kind=KIND_BREAK_START, hour=10)]
    much_later = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)
    sessions = report.sessionise(rows, now=much_later)

    assert any(s["has_unended_break"] for s in sessions)

    # The hour of work before the break (09:00 -> 10:00) is real and must be
    # kept: pressing Take a Break is evidence the annotator was there, so it
    # continues that session rather than splitting it away.
    #
    # What must NOT be invented is the eight hours of silence AFTER the break
    # started. Nothing was observed in it and nobody ended the break, so it
    # counts as neither present nor break. The bound is therefore on time
    # attributed past the break start, not on the total.
    break_start = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    latest_end = max(s["ended_at"] for s in sessions)
    assert latest_end <= break_start, (
        f"attendance runs to {latest_end} — past the abandoned break start "
        f"{break_start}; the idle rule must still bound an unended break"
    )

    total_present = sum(s["seconds"] for s in sessions)
    assert total_present == 3600, (
        f"expected exactly the observed hour of presence, got {total_present}s"
    )
    for session in sessions:
        assert session["end_reason"] != report.END_OPEN


def test_an_abandoned_break_does_not_swallow_later_work():
    """A forgotten *End Break* must not absorb the rest of the day.

    The regression the original unended-break test could not catch: its
    fixture had no observation AFTER the break_start, so `sessionise` fell
    straight to the tail branch and the loop body — where the idle rule is
    suspended — never ran. The failure needs the annotator to come *back*.

    Reported shape: break at 10:05, tab closed, working again at 13:00. That
    used to read as one session to 13:02 with a 175-minute declared break and
    almost no present time.

    The bound is MAX_BREAK, deliberately not request traffic: see the comment
    in the break branch. The return here is therefore past MAX_BREAK.
    """
    start_hour = 9
    back_hour = start_hour + int(report.MAX_BREAK.total_seconds() // 3600) + 1
    rows = [
        obs(0), obs(2),
        obs(5, kind=KIND_BREAK_START),
        obs(0, hour=back_hour), obs(2, hour=back_hour),
    ]
    now = datetime(2026, 9, 1, back_hour, 5, tzinfo=timezone.utc)
    sessions = report.sessionise(rows, now=now)

    # Two sessions: the abandoned one and the one they came back to. The
    # afternoon's work must not land on the far side of the morning's break.
    assert len(sessions) == 2, (
        f"{len(sessions)} session(s); an abandoned break absorbed the return"
    )

    breaks = [b for s in sessions for b in s["breaks"]]
    assert len(breaks) == 1
    assert not breaks[0]["ended"], "a break nobody ended must stay flagged"

    # The abandoned interval counts as neither present nor break: the session
    # closes at the break start, the last moment with evidence of presence.
    assert sessions[0]["ended_at"] == datetime(
        2026, 9, 1, start_hour, 5, tzinfo=timezone.utc
    )
    assert sessions[0]["end_reason"] == report.END_TIMEOUT

    # The work either side of it is present time, not break time.
    total_present = sum(s["seconds"] for s in sessions)
    assert total_present >= 7 * 60, (
        f"only {total_present}s present; the work before and after an "
        "abandoned break was swallowed by it"
    )


def test_a_manual_break_is_not_truncated_by_the_traffic_it_covers():
    """A retroactive break spans time the tab was open and sending `seen`.

    Unlike a declared break, a manual one is entered after the fact for a
    stretch the annotator sat through logged in, so ambient traffic runs right
    through it. Treating that traffic as "they came back" would clip every
    retroactive break to one idle gap — defeating the point of Q16's entry.
    """
    rows = [obs(m) for m in range(0, 34, 2)]
    rows += [
        obs(20, kind=KIND_BREAK_MANUAL_START),
        obs(30, kind=KIND_BREAK_MANUAL_END),
    ]
    now = datetime(2026, 9, 1, 9, 35, tzinfo=timezone.utc)
    sessions = report.sessionise(rows, now=now)

    breaks = [b for s in sessions for b in s["breaks"]]
    assert len(breaks) == 1
    assert breaks[0]["seconds"] == 600, (
        f"the manual break was recorded as {breaks[0]['seconds']}s, not the "
        "600s that was entered"
    )
    assert breaks[0]["ended"]


def test_a_declared_break_is_not_truncated_by_traffic_during_it():
    """The reported bug: a 9-minute break recorded as 6 and then frozen.

    The overlay pauses the annotation *timer*, but it does not stop the page
    making authenticated requests, and every one records a `seen` observation
    in `get_current_user`. A second tab on the same login does it too. So a
    declared break is NOT a period of client silence, and nothing may infer
    the annotator's return from request traffic.
    """
    rows = [obs(m) for m in range(0, 5)]
    rows.append(obs(5, kind=KIND_BREAK_START))
    rows += [obs(m) for m in range(6, 15)]  # the tab stays open throughout
    rows.append(obs(14, kind=KIND_BREAK_END))
    rows += [obs(15), obs(16)]
    now = datetime(2026, 9, 1, 9, 17, tzinfo=timezone.utc)

    breaks = [b for s in report.sessionise(rows, now=now) for b in s["breaks"]]
    assert len(breaks) == 1
    assert breaks[0]["ended"]
    assert breaks[0]["seconds"] == 9 * 60, (
        f"the 9-minute break was recorded as {breaks[0]['seconds'] / 60:.1f} "
        "minutes; traffic during a break must not close it"
    )


def test_a_break_in_progress_accrues_up_to_now():
    """A running break must keep growing between observations.

    Closing the session at the last `seen` row froze a live break at whatever
    moment traffic last happened to land on, so the dashboard showed it
    ticking up and then stopping while the annotator was still away.
    """
    rows = [obs(0), obs(2), obs(4), obs(5, kind=KIND_BREAK_START), obs(6)]

    for elapsed in (8, 10, 12, 14):
        now = datetime(2026, 9, 1, 9, elapsed, tzinfo=timezone.utc)
        breaks = [b for s in report.sessionise(rows, now=now) for b in s["breaks"]]
        assert len(breaks) == 1
        assert breaks[0]["seconds"] == (elapsed - 5) * 60, (
            f"at {elapsed}m the break read "
            f"{breaks[0]['seconds'] / 60:.1f}m, expected {elapsed - 5}m"
        )


def test_a_break_start_does_not_split_the_session_it_interrupts():
    """Pressing Take a Break is evidence of presence, so it continues the run.

    The throttle spaces ambient rows up to a minute apart and a quiet stretch
    before stepping away is ordinary, so a break_start often arrives more than
    IDLE_GAP after the last `seen`. Splitting there stranded the pre-break
    work as a separate session reporting zero present time.
    """
    rows = [
        obs(0),
        obs(int(report.IDLE_GAP.total_seconds() // 60) + 5, kind=KIND_BREAK_START),
    ]
    now = datetime(2026, 9, 1, 9, 30, tzinfo=timezone.utc)
    sessions = report.sessionise(rows, now=now)

    assert len(sessions) == 1, (
        f"{len(sessions)} sessions; a break_start must not open a new one"
    )
    assert sessions[0]["seconds"] > 0, "the work before the break was lost"


def test_a_manual_break_is_distinguishable_from_a_declared_one():
    """The provenance signal (Q16): the admin must be able to see that part of
    a day was reconstructed rather than observed."""
    entered = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)
    rows = [
        obs(0),
        obs(1, kind=KIND_BREAK_MANUAL_START, created_at=entered),
        obs(31, kind=KIND_BREAK_MANUAL_END, created_at=entered),
        obs(32),
    ]
    session = report.sessionise(rows, now=obs(32)["seen_at"])[0]
    assert session["breaks"][0]["source"] == "manual"
    assert session["manual_break_seconds"] == 30 * 60
    assert session["break_seconds"] == 30 * 60


def test_declared_breaks_are_not_counted_as_manual():
    rows = [obs(0), obs(1, kind=KIND_BREAK_START), obs(11, kind=KIND_BREAK_END)]
    session = report.sessionise(rows, now=obs(11)["seen_at"])[0]
    assert session["manual_break_seconds"] == 0
    assert session["break_seconds"] == 10 * 60


def test_present_time_never_goes_negative():
    """A break longer than its session (a manual entry spanning more than the
    observed run) must floor at zero, not report negative attendance."""
    rows = [
        obs(10, kind=KIND_BREAK_MANUAL_START),
        obs(0),
        obs(50, kind=KIND_BREAK_MANUAL_END),
    ]
    session = report.sessionise(rows, now=obs(50)["seen_at"])[0]
    assert session["seconds"] >= 0


# --- Tasks and active time --------------------------------------------------


def test_tasks_touched_counts_distinct_tasks():
    rows = [obs(0, task_id=1), obs(1, task_id=1), obs(2, task_id=2), obs(3)]
    session = report.sessionise(rows, now=obs(3)["seen_at"])[0]
    assert session["tasks_touched"] == 2


def test_active_seconds_come_from_active_observations():
    rows = [obs(m, kind=KIND_ACTIVE) for m in range(0, 10)]
    sessions = report.sessionise(rows, now=obs(9)["seen_at"])
    assert report.active_seconds(sessions, throttle_seconds=60) == 9 * 60


def test_active_seconds_never_exceed_the_session_span():
    """Each observation stands for up to one throttle window, so a naive
    multiplication can credit more time than the session lasted."""
    rows = [obs(0, kind=KIND_ACTIVE), obs(1, kind=KIND_ACTIVE)]
    sessions = report.sessionise(rows, now=obs(1)["seen_at"])
    assert report.active_seconds(sessions, throttle_seconds=600) == 60


def test_ordinary_observations_contribute_no_active_time():
    rows = [obs(0), obs(1)]
    sessions = report.sessionise(rows, now=obs(1)["seen_at"])
    assert report.active_seconds(sessions) == 0


# --- The day summary --------------------------------------------------------


def test_summarise_day_aggregates_across_sessions():
    rows = [
        obs(0, kind=KIND_LOGIN), obs(1, task_id=1),
        obs(40, task_id=2), obs(41, kind=KIND_LOGOUT),
    ]
    day = report.summarise_day(rows, date(2026, 9, 1), now=obs(41)["seen_at"], reviews=3)
    assert day["session_count"] == 2
    assert day["tasks_touched"] == 2
    assert day["tasks_reviewed"] == 3
    assert day["first_seen"] == rows[0]["seen_at"]
    assert day["last_seen"] == rows[-1]["seen_at"]
    # The last session ended in a logout, so the day did.
    assert day["end_reason"] == report.END_LOGOUT


def test_summarise_day_returns_none_for_no_observations():
    assert report.summarise_day([], date(2026, 9, 1)) is None


def test_summarise_day_present_time_is_the_sum_of_sessions():
    rows = [obs(0), obs(2), obs(40), obs(44)]
    day = report.summarise_day(rows, date(2026, 9, 1), now=obs(44)["seen_at"])
    assert day["present_seconds"] == sum(s["seconds"] for s in day["sessions"])
    assert day["present_seconds"] == (2 * 60) + (4 * 60)


def test_summarise_day_surfaces_an_unended_break():
    rows = [obs(0), obs(1, kind=KIND_BREAK_START), obs(2)]
    day = report.summarise_day(rows, date(2026, 9, 1), now=obs(2)["seen_at"])
    assert day["has_unended_break"] is True


def test_a_day_summary_is_unaffected_by_input_order():
    """Rows arrive from two sources (the table and the live buffer)."""
    rows = [obs(0), obs(1, task_id=1), obs(2, kind=KIND_LOGOUT)]
    forward = report.summarise_day(rows, date(2026, 9, 1), now=obs(2)["seen_at"])
    reverse = report.summarise_day(list(reversed(rows)), date(2026, 9, 1), now=obs(2)["seen_at"])
    assert forward["present_seconds"] == reverse["present_seconds"]
    assert forward["session_count"] == reverse["session_count"]
    assert forward["end_reason"] == reverse["end_reason"]


# --- Naive datetimes from SQLite -------------------------------------------


def test_naive_stored_datetimes_do_not_crash_the_aggregation():
    """SQLite returns DateTime(timezone=True) columns as NAIVE datetimes.

    Postgres returns aware ones, so mixing a stored row with a live buffered
    row raises TypeError on the dev/test database while working on the
    deployment. Rows are always stored UTC, so the boundary restores that.
    """
    naive = {
        "user_id": 1, "kind": KIND_SEEN, "task_id": None, "instance_id": "t",
        "seen_at": datetime(2026, 9, 21, 9, 0),          # as SQLite hands it back
        "created_at": datetime(2026, 9, 21, 9, 0),
    }
    aware = {
        "user_id": 1, "kind": KIND_SEEN, "task_id": None, "instance_id": "t",
        "seen_at": datetime(2026, 9, 21, 9, 1, tzinfo=timezone.utc),  # buffered
        "created_at": None,
    }
    sessions = report.sessionise(
        [naive, aware], now=datetime(2026, 9, 21, 9, 2, tzinfo=timezone.utc)
    )
    assert len(sessions) == 1
    assert sessions[0]["started_at"].tzinfo is not None


def test_as_utc_treats_naive_as_utc_not_machine_local():
    naive = datetime(2026, 9, 21, 9, 0)
    assert report.as_utc(naive) == datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
    assert report.as_utc(None) is None

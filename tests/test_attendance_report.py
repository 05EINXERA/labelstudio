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
    """An observation at a given wall time, UTC."""
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
    rows = [obs(0), obs(5), obs(10), obs(15)]
    sessions = report.sessionise(rows, now=obs(15)["seen_at"])
    assert len(sessions) == 1
    assert sessions[0]["started_at"] == rows[0]["seen_at"]


def test_a_gap_longer_than_idle_gap_splits_the_session():
    rows = [obs(0), obs(5), obs(30), obs(35)]
    sessions = report.sessionise(rows, now=obs(35)["seen_at"])
    assert len(sessions) == 2
    assert sessions[0]["end_reason"] == report.END_TIMEOUT


def test_gap_exactly_at_idle_gap_does_not_split():
    """The boundary is inclusive: a gap *greater than* IDLE_GAP splits."""
    rows = [obs(0), obs(10)]  # exactly 10 minutes
    assert len(report.sessionise(rows, now=obs(10)["seen_at"])) == 1


def test_gap_one_second_over_idle_gap_splits():
    first = obs(0)
    second = {**obs(0), "seen_at": first["seen_at"] + report.IDLE_GAP + timedelta(seconds=1)}
    assert len(report.sessionise([first, second], now=second["seen_at"])) == 2


def test_observations_are_sorted_before_sessionising():
    """The buffer merge concatenates stored and live rows, which are not
    ordered relative to each other."""
    rows = [obs(15), obs(0), obs(10), obs(5)]
    sessions = report.sessionise(rows, now=obs(15)["seen_at"])
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
    rows = [obs(0), obs(5)]
    long_after = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)
    sessions = report.sessionise(rows, now=long_after)
    assert sessions[0]["end_reason"] == report.END_TIMEOUT
    assert sessions[0]["end_reason"] != report.END_LOGOUT


def test_a_recent_session_is_open():
    rows = [obs(0), obs(5)]
    sessions = report.sessionise(rows, now=obs(5)["seen_at"] + timedelta(minutes=1))
    assert sessions[0]["end_reason"] == report.END_OPEN


def test_a_logout_closes_the_session_at_the_logout_time():
    rows = [obs(0), obs(5), obs(7, kind=KIND_LOGOUT)]
    sessions = report.sessionise(rows, now=obs(7)["seen_at"])
    assert len(sessions) == 1
    assert sessions[0]["end_reason"] == report.END_LOGOUT
    assert sessions[0]["ended_at"] == rows[-1]["seen_at"]


def test_activity_after_a_logout_starts_a_new_session():
    rows = [obs(0), obs(5, kind=KIND_LOGOUT), obs(6), obs(8)]
    sessions = report.sessionise(rows, now=obs(8)["seen_at"])
    assert len(sessions) == 2
    assert sessions[0]["end_reason"] == report.END_LOGOUT


# --- Breaks -----------------------------------------------------------------


def test_a_declared_break_does_not_split_the_session():
    """A break is a marked interval *inside* a session (§ 1.1), and its span
    would otherwise exceed IDLE_GAP and split it."""
    rows = [obs(0), obs(5, kind=KIND_BREAK_START), obs(40, kind=KIND_BREAK_END), obs(45)]
    sessions = report.sessionise(rows, now=obs(45)["seen_at"])
    assert len(sessions) == 1
    assert len(sessions[0]["breaks"]) == 1


def test_present_time_excludes_the_break():
    rows = [obs(0), obs(10, kind=KIND_BREAK_START), obs(40, kind=KIND_BREAK_END), obs(45)]
    session = report.sessionise(rows, now=obs(45)["seen_at"])[0]
    assert session["span_seconds"] == 45 * 60
    assert session["break_seconds"] == 30 * 60
    assert session["seconds"] == 15 * 60, (
        "present time still includes the declared break"
    )


def test_an_unended_break_is_reported_not_dropped():
    """Q17. A break that vanishes silently inflates present time."""
    rows = [obs(0), obs(10, kind=KIND_BREAK_START), obs(15)]
    session = report.sessionise(rows, now=obs(15)["seen_at"])[0]
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
    total_present = sum(s["seconds"] for s in sessions)
    assert total_present < 3600, (
        f"{total_present}s of presence invented from an abandoned break; the "
        "idle rule must still bound an unended break"
    )
    for session in sessions:
        assert session["end_reason"] != report.END_OPEN


def test_a_manual_break_is_distinguishable_from_a_declared_one():
    """The provenance signal (Q16): the admin must be able to see that part of
    a day was reconstructed rather than observed."""
    entered = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)
    rows = [
        obs(0),
        obs(10, kind=KIND_BREAK_MANUAL_START, created_at=entered),
        obs(40, kind=KIND_BREAK_MANUAL_END, created_at=entered),
        obs(45),
    ]
    session = report.sessionise(rows, now=obs(45)["seen_at"])[0]
    assert session["breaks"][0]["source"] == "manual"
    assert session["manual_break_seconds"] == 30 * 60
    assert session["break_seconds"] == 30 * 60


def test_declared_breaks_are_not_counted_as_manual():
    rows = [obs(0), obs(10, kind=KIND_BREAK_START), obs(20, kind=KIND_BREAK_END)]
    session = report.sessionise(rows, now=obs(20)["seen_at"])[0]
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
    rows = [obs(0), obs(5)]
    sessions = report.sessionise(rows, now=obs(5)["seen_at"])
    assert report.active_seconds(sessions) == 0


# --- The day summary --------------------------------------------------------


def test_summarise_day_aggregates_across_sessions():
    rows = [
        obs(0, kind=KIND_LOGIN), obs(5, task_id=1),
        obs(40, task_id=2), obs(45, kind=KIND_LOGOUT),
    ]
    day = report.summarise_day(rows, date(2026, 9, 1), now=obs(45)["seen_at"], reviews=3)
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
    rows = [obs(0), obs(5), obs(40), obs(50)]
    day = report.summarise_day(rows, date(2026, 9, 1), now=obs(50)["seen_at"])
    assert day["present_seconds"] == sum(s["seconds"] for s in day["sessions"])
    assert day["present_seconds"] == (5 * 60) + (10 * 60)


def test_summarise_day_surfaces_an_unended_break():
    rows = [obs(0), obs(5, kind=KIND_BREAK_START), obs(8)]
    day = report.summarise_day(rows, date(2026, 9, 1), now=obs(8)["seen_at"])
    assert day["has_unended_break"] is True


def test_a_day_summary_is_unaffected_by_input_order():
    """Rows arrive from two sources (the table and the live buffer)."""
    rows = [obs(0), obs(5, task_id=1), obs(9, kind=KIND_LOGOUT)]
    forward = report.summarise_day(rows, date(2026, 9, 1), now=obs(9)["seen_at"])
    reverse = report.summarise_day(list(reversed(rows)), date(2026, 9, 1), now=obs(9)["seen_at"])
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
        "seen_at": datetime(2026, 9, 21, 9, 5, tzinfo=timezone.utc),  # buffered
        "created_at": None,
    }
    sessions = report.sessionise(
        [naive, aware], now=datetime(2026, 9, 21, 9, 6, tzinfo=timezone.utc)
    )
    assert len(sessions) == 1
    assert sessions[0]["started_at"].tzinfo is not None


def test_as_utc_treats_naive_as_utc_not_machine_local():
    naive = datetime(2026, 9, 21, 9, 0)
    assert report.as_utc(naive) == datetime(2026, 9, 21, 9, 0, tzinfo=timezone.utc)
    assert report.as_utc(None) is None

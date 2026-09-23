"""R10 — retroactive break entry.

The one place in the feature where a user asserts a fact about themselves
rather than the server observing one, so it carries constraints the observed
path does not need.

Two tests matter more than the rest:

- **The re-roll consistency test.** After a manual break re-rolls a day, the
  stored rollup must equal a fresh recomputation from raw rows. Divergence
  here is invisible until the raw rows prune at 31 days, at which point it is
  unrecoverable.
- **The +05:45 backdating boundary.** "Yesterday" in Kathmandu is not
  "yesterday" in UTC, and a window computed in UTC rejects legitimate entries
  for 45 minutes either side of local midnight.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

import config
import models
from api import attendance
from api import attendance_report as report
from api.routers import attendance as router
from database import SessionLocal


@pytest.fixture(autouse=True)
def _clean_buffer():
    attendance.reset_for_tests()
    original = config.ATTENDANCE_ENABLED
    config.ATTENDANCE_ENABLED = True
    yield
    config.ATTENDANCE_ENABLED = original
    attendance.reset_for_tests()


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


def _user_id(client, headers):
    return client.get("/api/auth/me", headers=headers).json()["id"]


def _today():
    return datetime.now(report.site_tz()).date()


def _at(hour, minute, day=None):
    """A UTC instant from a Kathmandu wall time on a local day."""
    day = day or _today()
    return datetime(
        day.year, day.month, day.day, hour, minute, tzinfo=report.site_tz()
    ).astimezone(timezone.utc)


def _iso(moment):
    return moment.isoformat()


def _post(client, headers, start, end):
    return client.post(
        "/api/attendance/break/manual",
        json={"started_at": _iso(start), "ended_at": _iso(end)},
        headers=headers,
    )


def _cleanup(db, user_id):
    db.query(models.AttendanceObservation).filter(
        models.AttendanceObservation.user_id == user_id
    ).delete(synchronize_session=False)
    db.query(models.AttendanceDay).filter(
        models.AttendanceDay.user_id == user_id
    ).delete(synchronize_session=False)
    db.commit()


# --- The happy path ---------------------------------------------------------


def test_a_manual_break_is_written_as_a_row_pair(client, bob, db):
    """Never an UPDATE of an observed row: a new pair with its own kinds."""
    user_id = _user_id(client, bob)
    try:
        # An hour ago, so it is safely in the past and inside today.
        end = datetime.now(timezone.utc) - timedelta(minutes=30)
        start = end - timedelta(minutes=15)
        res = _post(client, bob, start, end)
        assert res.status_code == 200, res.text
        assert res.json()["seconds"] == 900

        rows = (
            db.query(models.AttendanceObservation)
            .filter(models.AttendanceObservation.user_id == user_id)
            .order_by(models.AttendanceObservation.seen_at)
            .all()
        )
        kinds = [r.kind for r in rows]
        assert attendance.KIND_BREAK_MANUAL_START in kinds
        assert attendance.KIND_BREAK_MANUAL_END in kinds
    finally:
        _cleanup(db, user_id)


def test_the_created_at_gap_is_the_provenance_signal(client, bob, db):
    """`created_at` (when it was entered) is distinct from `seen_at` (when the
    break was). Equal for observed rows, apart for a remembered one — and that
    gap is how an admin sees the difference, with no approval queue."""
    user_id = _user_id(client, bob)
    try:
        end = datetime.now(timezone.utc) - timedelta(hours=2)
        start = end - timedelta(minutes=20)
        assert _post(client, bob, start, end).status_code == 200

        row = (
            db.query(models.AttendanceObservation)
            .filter(
                models.AttendanceObservation.user_id == user_id,
                models.AttendanceObservation.kind
                == attendance.KIND_BREAK_MANUAL_START,
            )
            .first()
        )
        gap = report.as_utc(row.created_at) - report.as_utc(row.seen_at)
        assert gap > timedelta(minutes=90), (
            "created_at and seen_at are too close; the provenance signal is lost"
        )
    finally:
        _cleanup(db, user_id)


def test_the_entering_user_is_recorded(client, bob, db):
    user_id = _user_id(client, bob)
    try:
        end = datetime.now(timezone.utc) - timedelta(minutes=30)
        assert _post(client, bob, end - timedelta(minutes=10), end).status_code == 200
        row = (
            db.query(models.AttendanceObservation)
            .filter(
                models.AttendanceObservation.user_id == user_id,
                models.AttendanceObservation.kind
                == attendance.KIND_BREAK_MANUAL_START,
            )
            .first()
        )
        assert row.entered_by == user_id
    finally:
        _cleanup(db, user_id)


def test_a_manual_break_reaches_the_read_as_manual(client, bob, db):
    """It must count as break time *and* be distinguishable from a declared
    one — provenance, not suspicion."""
    user_id = _user_id(client, bob)
    try:
        end = datetime.now(timezone.utc) - timedelta(minutes=30)
        start = end - timedelta(minutes=20)
        assert _post(client, bob, start, end).status_code == 200

        rows = client.get("/api/attendance/me", headers=bob).json()["rows"]
        today_rows = [r for r in rows if r["local_date"] == str(_today())]
        assert today_rows
        assert today_rows[0]["manual_break_seconds"] == 1200
        assert today_rows[0]["break_seconds"] >= 1200
    finally:
        _cleanup(db, user_id)


# --- Validation -------------------------------------------------------------


def test_a_start_after_its_end_is_refused(client, bob):
    end = datetime.now(timezone.utc) - timedelta(minutes=30)
    res = _post(client, bob, end, end - timedelta(minutes=10))
    assert res.status_code == 400
    assert "end after it starts" in res.json()["detail"]


def test_a_zero_length_break_is_refused(client, bob):
    moment = datetime.now(timezone.utc) - timedelta(minutes=30)
    assert _post(client, bob, moment, moment).status_code == 400


def test_a_future_break_is_refused(client, bob):
    start = datetime.now(timezone.utc) + timedelta(hours=1)
    res = _post(client, bob, start, start + timedelta(minutes=10))
    assert res.status_code == 400
    assert "future" in res.json()["detail"]


def test_an_absurdly_long_break_is_refused(client, bob):
    """A sanity ceiling, not a policy cap: Q16 says breaks are not capped.
    This rejects a typo, which is unfixable once written."""
    end = datetime.now(timezone.utc) - timedelta(minutes=5)
    res = _post(client, bob, end - timedelta(hours=20), end)
    assert res.status_code == 400
    assert "maximum" in res.json()["detail"].lower()


def test_backdating_beyond_yesterday_is_refused(client, bob):
    """Q25: the current local day plus the previous one. Far shorter than the
    31-day retention window, which matters because past that the rollup is the
    only surviving record."""
    old = _at(10, 0, day=_today() - timedelta(days=5))
    res = _post(client, bob, old, old + timedelta(minutes=20))
    assert res.status_code == 400
    assert "today or yesterday" in res.json()["detail"]


def test_yesterday_is_allowed(client, bob, db):
    user_id = _user_id(client, bob)
    try:
        yesterday = _today() - timedelta(days=1)
        start = _at(10, 0, day=yesterday)
        res = _post(client, bob, start, start + timedelta(minutes=20))
        assert res.status_code == 200, res.text
        assert res.json()["local_date"] == str(yesterday)
    finally:
        _cleanup(db, user_id)


def test_a_break_spanning_midnight_is_refused(client, bob):
    """Q7: no shifts cross midnight, so a pair that does is a typo rather than
    a real break, and splitting it would invent a fact."""
    start = _at(23, 30, day=_today() - timedelta(days=1))
    end = _at(0, 30, day=_today())
    res = _post(client, bob, start, end)
    assert res.status_code == 400
    assert "same day" in res.json()["detail"]


# --- The +05:45 backdating boundary -----------------------------------------


def test_the_backdating_window_is_computed_in_local_days_not_utc():
    """THE timezone test for this endpoint.

    At 00:15 Kathmandu it is still *yesterday* in UTC. A window computed on
    UTC dates would reject an entry for the local day the user is actually
    living in — for 45 minutes either side of local midnight, and only then.
    """
    # 18:30 UTC is 00:15 the NEXT day in Kathmandu.
    utc_moment = datetime(2026, 9, 20, 18, 30, tzinfo=timezone.utc)
    local = report.local_day(utc_moment)

    assert local == date(2026, 9, 21)
    assert utc_moment.date() == date(2026, 9, 20)
    assert local != utc_moment.date(), (
        "the local day and the UTC date agree here, so this test proves nothing"
    )

    # The endpoint buckets on report.local_day, which is what makes an entry
    # made just after local midnight land on the day the user means.
    import inspect
    source = inspect.getsource(router.manual_break)
    assert "report.local_day(started_at)" in source
    assert "datetime.now(report.site_tz()).date()" in source, (
        "'today' is computed in UTC rather than in the site zone"
    )


# --- Overlap ----------------------------------------------------------------


def test_an_overlapping_manual_break_is_refused(client, bob, db):
    """An overlapping pair cannot be corrected afterwards (Q24), so it must be
    refused at the door rather than cleaned up later."""
    user_id = _user_id(client, bob)
    try:
        end = datetime.now(timezone.utc) - timedelta(minutes=30)
        start = end - timedelta(minutes=30)
        assert _post(client, bob, start, end).status_code == 200

        # Overlaps the middle of the first.
        res = _post(
            client, bob, start + timedelta(minutes=10), end + timedelta(minutes=10)
        )
        assert res.status_code == 409
        assert "overlaps" in res.json()["detail"]
        assert "cannot be edited" in res.json()["detail"]
    finally:
        _cleanup(db, user_id)


def test_an_adjacent_non_overlapping_break_is_allowed(client, bob, db):
    """Back-to-back breaks are legitimate; only true overlap is refused."""
    user_id = _user_id(client, bob)
    try:
        first_end = datetime.now(timezone.utc) - timedelta(minutes=60)
        assert _post(
            client, bob, first_end - timedelta(minutes=15), first_end
        ).status_code == 200
        second = _post(
            client, bob, first_end, first_end + timedelta(minutes=15)
        )
        assert second.status_code == 200, second.text
    finally:
        _cleanup(db, user_id)


def test_a_manual_break_cannot_overlap_a_declared_one(client, bob, db):
    """Observed and asserted breaks share one timeline."""
    user_id = _user_id(client, bob)
    try:
        start = datetime.now(timezone.utc) - timedelta(minutes=40)
        end = start + timedelta(minutes=20)
        attendance.note_seen(
            user_id, kind=attendance.KIND_BREAK_START, seen_at=start
        )
        attendance.note_seen(
            user_id, kind=attendance.KIND_BREAK_END, seen_at=end
        )
        res = _post(client, bob, start + timedelta(minutes=5), end)
        assert res.status_code == 409
    finally:
        _cleanup(db, user_id)


# --- Self-scoping -----------------------------------------------------------


def test_the_endpoint_takes_no_user_field(client, alice, bob, db):
    """Safe by construction: the payload cannot name another user."""
    alice_id = _user_id(client, alice)
    bob_id = _user_id(client, bob)
    try:
        end = datetime.now(timezone.utc) - timedelta(minutes=30)
        res = client.post(
            "/api/attendance/break/manual",
            json={
                "started_at": _iso(end - timedelta(minutes=10)),
                "ended_at": _iso(end),
                "user_id": alice_id,
                "username": "alice",
            },
            headers=bob,
        )
        assert res.status_code == 200

        written = (
            db.query(models.AttendanceObservation)
            .filter(
                models.AttendanceObservation.kind.in_([
                    attendance.KIND_BREAK_MANUAL_START,
                    attendance.KIND_BREAK_MANUAL_END,
                ])
            )
            .all()
        )
        assert written
        assert all(r.user_id == bob_id for r in written), (
            "a manual break was written against another user"
        )
    finally:
        _cleanup(db, alice_id)
        _cleanup(db, bob_id)


def test_the_endpoint_requires_authentication(client):
    res = client.post(
        "/api/attendance/break/manual",
        json={"started_at": "2026-09-20T10:00:00Z", "ended_at": "2026-09-20T10:10:00Z"},
    )
    assert res.status_code == 401


def test_the_endpoint_is_not_admin_gated(client, bob, db):
    user_id = _user_id(client, bob)
    try:
        end = datetime.now(timezone.utc) - timedelta(minutes=30)
        assert _post(client, bob, end - timedelta(minutes=5), end).status_code == 200
    finally:
        _cleanup(db, user_id)


# --- The re-roll ------------------------------------------------------------


def test_a_manual_break_rerolls_an_already_rolled_day(client, bob, db):
    """THE consistency test.

    Without the re-roll the rollup and the raw rows disagree silently, and the
    divergence only surfaces once the raw rows prune at 31 days — at which
    point it is unrecoverable.
    """
    from scripts.rollup_attendance import rollup_day

    user_id = _user_id(client, bob)
    today = _today()
    try:
        # A day with some observed activity, then rolled up.
        base = datetime.now(timezone.utc) - timedelta(hours=3)
        for minutes in range(0, 40, 5):
            attendance.note_seen(
                user_id, kind=attendance.KIND_LOGIN,
                seen_at=base + timedelta(minutes=minutes),
            )
        attendance.flush()
        rollup_day(db, today)
        db.expire_all()

        before = (
            db.query(models.AttendanceDay)
            .filter(
                models.AttendanceDay.user_id == user_id,
                models.AttendanceDay.local_date == today,
            )
            .first()
        )
        assert before is not None, "precondition: the day should be rolled up"
        assert before.manual_break_seconds == 0

        # Now enter a break into that already-rolled day.
        end = base + timedelta(minutes=30)
        start = base + timedelta(minutes=20)
        res = _post(client, bob, start, end)
        assert res.status_code == 200, res.text
        assert res.json()["rerolled"] is True, "the day was not re-rolled"

        db.expire_all()
        after = (
            db.query(models.AttendanceDay)
            .filter(
                models.AttendanceDay.user_id == user_id,
                models.AttendanceDay.local_date == today,
            )
            .first()
        )
        assert after.manual_break_seconds == 600, (
            "the rollup did not pick up the manual break"
        )

        # And the stored rollup equals a fresh recomputation from raw rows.
        day_start, day_end = report.day_bounds(today)
        raw = report.observation_dicts(
            db.query(models.AttendanceObservation)
            .filter(
                models.AttendanceObservation.user_id == user_id,
                models.AttendanceObservation.seen_at >= day_start,
                models.AttendanceObservation.seen_at < day_end,
            )
            .order_by(models.AttendanceObservation.seen_at)
            .all()
        )
        fresh = report.summarise_day(raw, today)
        for field in (
            "present_seconds", "break_seconds", "manual_break_seconds",
            "session_count", "tasks_touched",
        ):
            assert getattr(after, field) == fresh[field], (
                f"{field} diverged after the re-roll: "
                f"rollup={getattr(after, field)} fresh={fresh[field]}"
            )
    finally:
        _cleanup(db, user_id)


def test_an_unrolled_day_is_not_rerolled(client, bob, db):
    """Nothing to diverge from yet; the nightly job picks it up with the
    manual break included."""
    user_id = _user_id(client, bob)
    try:
        end = datetime.now(timezone.utc) - timedelta(minutes=30)
        res = _post(client, bob, end - timedelta(minutes=10), end)
        assert res.status_code == 200
        assert res.json()["rerolled"] is False
    finally:
        _cleanup(db, user_id)


def test_a_failed_reroll_does_not_lose_the_break(client, bob, db, monkeypatch):
    """The observations are committed and are the source of truth. Raising
    would tell the user their break was not saved when it was."""
    import scripts.rollup_attendance as rollup_module

    user_id = _user_id(client, bob)
    try:
        def explode(*args, **kwargs):
            raise RuntimeError("rollup is broken")

        # Break what _reroll CALLS, not _reroll itself: patching the guarded
        # function out would only prove that a function which raises, raises.
        monkeypatch.setattr(rollup_module, "rollup_day", explode)

        end = datetime.now(timezone.utc) - timedelta(minutes=30)
        res = _post(client, bob, end - timedelta(minutes=10), end)

        assert res.status_code == 200, (
            "a failed re-roll surfaced as an error; the break WAS saved, and "
            "telling the user otherwise is the worse failure"
        )
        assert res.json()["rerolled"] is False

        written = (
            db.query(models.AttendanceObservation)
            .filter(
                models.AttendanceObservation.user_id == user_id,
                models.AttendanceObservation.kind
                == attendance.KIND_BREAK_MANUAL_START,
            )
            .count()
        )
        assert written == 1, "the break was rolled back by a re-roll failure"
    finally:
        _cleanup(db, user_id)


# --- Append-only ------------------------------------------------------------


def test_entering_a_break_never_updates_an_existing_row(client, bob, db):
    """Q24. The table is strictly append-only in application code, with the
    retention prune as the sole exception."""
    from sqlalchemy import event
    from database import engine

    user_id = _user_id(client, bob)
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        head = statement.strip().split(None, 1)[0].upper()
        if head in ("UPDATE", "DELETE") and "attendance_observations" in statement:
            statements.append(statement.strip()[:120])

    try:
        end = datetime.now(timezone.utc) - timedelta(minutes=30)
        assert _post(client, bob, end - timedelta(minutes=10), end).status_code == 200

        event.listen(engine, "before_cursor_execute", record)
        try:
            # A second, non-overlapping entry on the same day.
            second_end = end - timedelta(hours=1)
            _post(client, bob, second_end - timedelta(minutes=10), second_end)
        finally:
            event.remove(engine, "before_cursor_execute", record)

        assert statements == [], (
            f"attendance_observations was mutated: {statements}"
        )
    finally:
        _cleanup(db, user_id)

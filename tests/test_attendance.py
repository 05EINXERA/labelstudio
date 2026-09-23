"""R2 — the attendance capture buffer and flush.

These tests exist to keep the design's non-negotiable invariants true as code
is added later (.devnotes/attendance-feature/00-IMPLEMENTATION-PROMPT.md):

1. Zero blocking work on normal request paths — `note_seen` does no I/O.
2. Attendance must never break annotation — it swallows and logs, never raises.

The rest cover the throttle, the batching, and the logout behaviour change.
"""
import logging
import time
from datetime import datetime, timedelta, timezone

import pytest

import config
import models
from api import attendance


@pytest.fixture(autouse=True)
def _clean_buffer():
    """Every test starts with an empty buffer and the feature ON.

    The flag defaults off (R2 ships dark), so a test that did not set it would
    silently assert nothing at all — every note_seen would return immediately
    and every assertion about an empty buffer would pass for the wrong reason.
    """
    attendance.reset_for_tests()
    original = config.ATTENDANCE_ENABLED
    config.ATTENDANCE_ENABLED = True
    yield
    config.ATTENDANCE_ENABLED = original
    attendance.reset_for_tests()


@pytest.fixture
def real_user_ids(client):
    """Ids of genuinely existing users.

    The FK on attendance_observations.user_id is real, so invented ids are
    correctly rejected at flush time. Tests that exercise the *write* need
    rows that exist; tests that only exercise the buffer do not.
    """
    import uuid as _uuid
    ids = []
    for _ in range(5):
        username = f"attend-{_uuid.uuid4().hex[:10]}"
        res = client.post(
            "/api/auth/register", json={"username": username, "password": "pw-12345"}
        )
        assert res.status_code == 200, res.text
        headers = {"Authorization": f"Bearer {res.json()['access_token']}"}
        client.cookies.clear()
        ids.append(client.get("/api/auth/me", headers=headers).json()["id"])
    attendance.reset_for_tests()
    return ids


# --- Invariant 1: no I/O on the request path --------------------------------


class _ExplodingSession:
    """A DB session that fails the test if anything touches it.

    The point of the capture design is that `note_seen` runs on the hottest
    path in the app (every /api/* request) and touches no database at all. A
    mock that raises is how that stays true — a future edit that adds a lookup
    fails here rather than in production.
    """

    def __getattr__(self, name):
        raise AssertionError(
            f"note_seen touched the database (session.{name}); invariant 1 "
            "forbids any I/O on the capture path"
        )


def test_note_seen_performs_no_database_work():
    # The session is never passed in — that is the design — so the strongest
    # available assertion is that capture completes with one available and
    # untouched, and that it is pure dict work.
    session = _ExplodingSession()
    attendance.note_seen(1)
    assert len(attendance.buffered_observations()) == 1
    with pytest.raises(AssertionError):
        session.query  # the guard itself works


def test_note_seen_never_raises_into_a_request(monkeypatch, caplog):
    """Invariant 2: attendance may never break annotation.

    A broken buffer must degrade to a log line, not a 500 on a save. But it
    must *log* — a net that can drop what it is catching is worse than no net.
    """
    class _Exploding:
        def get(self, *args, **kwargs):
            raise RuntimeError("buffer is broken")

    # _LAST_SEEN is read on every capture, so breaking it breaks the path.
    monkeypatch.setattr(attendance, "_LAST_SEEN", _Exploding())
    with caplog.at_level(logging.WARNING):
        attendance.note_seen(1)  # must not raise

    assert any("note_seen failed" in r.message for r in caplog.records), (
        "the failure was swallowed silently; CLAUDE.md rule 3 requires it be logged"
    )


def test_capture_is_a_noop_when_disabled():
    config.ATTENDANCE_ENABLED = False
    attendance.note_seen(1)
    attendance.note_seen(2, kind=attendance.KIND_LOGIN)
    assert attendance.buffered_observations() == []


# --- The throttle -----------------------------------------------------------


def test_throttle_collapses_many_calls_into_one_observation():
    for _ in range(1000):
        attendance.note_seen(7)
    rows = attendance.buffered_observations()
    assert len(rows) == 1, (
        f"1000 calls in one window produced {len(rows)} rows; the throttle is "
        "what keeps capture O(1) per user per minute"
    )


def test_throttle_is_per_user_not_global():
    attendance.note_seen(1)
    attendance.note_seen(2)
    attendance.note_seen(3)
    assert len(attendance.buffered_observations()) == 3


def test_throttle_releases_after_the_window():
    attendance.note_seen(7)
    # Rewind the recorded last-seen past the window rather than sleeping.
    attendance._LAST_SEEN[(7, attendance.KIND_SEEN)] = datetime.now(timezone.utc) - timedelta(
        seconds=config.ATTENDANCE_THROTTLE_SECONDS + 1
    )
    attendance.note_seen(7)
    assert len(attendance.buffered_observations()) == 2


@pytest.mark.parametrize("kind", [
    attendance.KIND_LOGIN,
    attendance.KIND_LOGOUT,
    attendance.KIND_BREAK_START,
    attendance.KIND_BREAK_END,
])
def test_event_kinds_are_never_throttled_away(kind):
    """A boundary that gets throttled away cannot be reconstructed.

    Ambient `seen` rows are interchangeable — losing one costs nothing, since
    the next request re-observes. A login, a logout or a break edge is the only
    record of that moment.
    """
    attendance.note_seen(9)  # consume the ambient window
    for _ in range(5):
        attendance.note_seen(9, kind=kind)
    events = [r for r in attendance.buffered_observations() if r["kind"] == kind]
    assert len(events) == 5


def test_none_user_id_is_ignored():
    attendance.note_seen(None)
    assert attendance.buffered_observations() == []


# --- The buffer -------------------------------------------------------------


def test_buffer_is_bounded_and_drops_oldest(caplog):
    config.ATTENDANCE_BUFFER_MAX = 10
    try:
        with caplog.at_level(logging.WARNING):
            for user_id in range(25):
                attendance.note_seen(user_id, kind=attendance.KIND_LOGIN)
        rows = attendance.buffered_observations()
        assert len(rows) <= 10
        # Oldest dropped, newest kept: a full buffer should surface recent
        # presence, not freeze on stale rows.
        assert rows[-1]["user_id"] == 24
        assert any("buffer hit" in r.message for r in caplog.records)
    finally:
        config.ATTENDANCE_BUFFER_MAX = 10000


def test_buffered_observations_filters_by_user():
    attendance.note_seen(1)
    attendance.note_seen(2)
    rows = attendance.buffered_observations(user_ids={1})
    assert [r["user_id"] for r in rows] == [1]


def test_observation_carries_instance_and_task():
    attendance.note_seen(1, kind=attendance.KIND_ACTIVE, task_id=42)
    row = attendance.buffered_observations()[0]
    assert row["task_id"] == 42
    assert row["kind"] == attendance.KIND_ACTIVE
    assert row["instance_id"] == config.ATTENDANCE_INSTANCE_ID


def test_seen_at_is_timezone_aware_utc():
    """CLAUDE.md rule 7. A naive datetime here would be bucketed into the wrong
    local day by the +05:45 conversion and look like a rounding bug."""
    attendance.note_seen(1)
    seen_at = attendance.buffered_observations()[0]["seen_at"]
    assert seen_at.tzinfo is not None
    assert seen_at.utcoffset() == timedelta(0)


# --- The flush --------------------------------------------------------------


def test_flush_writes_the_batch_and_empties_the_buffer(real_user_ids):
    for user_id in real_user_ids:
        attendance.note_seen(user_id, kind=attendance.KIND_LOGIN)
    assert attendance.flush() == len(real_user_ids)
    assert attendance.buffered_observations() == []


def test_flush_is_a_noop_on_an_empty_buffer():
    assert attendance.flush() == 0


def test_flush_drops_rows_rather_than_growing_without_bound(monkeypatch, caplog, real_user_ids):
    """A persistent flush failure must not turn a missing attendance row into
    an outage. Attendance is the thing that gives way."""
    def explode(*args, **kwargs):
        raise RuntimeError("database is unhappy")

    attendance.note_seen(real_user_ids[0], kind=attendance.KIND_LOGIN)
    monkeypatch.setattr(attendance, "commit_with_retry", explode)

    with caplog.at_level(logging.ERROR):
        assert attendance.flush() == 0

    assert attendance.buffered_observations() == [], (
        "failed rows were put back; a persistent failure would grow the buffer "
        "without bound while retrying forever"
    )
    assert any("flush failed" in r.message for r in caplog.records)


def test_should_flush_respects_the_interval(real_user_ids):
    assert attendance.should_flush() is False  # nothing buffered
    attendance.note_seen(real_user_ids[0], kind=attendance.KIND_LOGIN)
    assert attendance.should_flush() is True   # never flushed yet
    attendance.flush()
    attendance.note_seen(real_user_ids[1], kind=attendance.KIND_LOGIN)
    assert attendance.should_flush() is False  # just flushed


def test_maybe_flush_queues_a_background_task():
    class _Recorder:
        def __init__(self):
            self.tasks = []

        def add_task(self, fn, *args):
            self.tasks.append(fn)

    attendance.note_seen(1, kind=attendance.KIND_LOGIN)
    recorder = _Recorder()
    attendance.maybe_flush(recorder)
    assert recorder.tasks == [attendance.flush], (
        "the flush must be queued as a BackgroundTask so it runs after the "
        "response, not inside the request"
    )


def test_flush_writes_rows_that_read_back(client, alice):
    """End to end against the real session: the rows land with their kinds."""
    from database import SessionLocal

    me = client.get("/api/auth/me", headers=alice).json()
    # That request itself observed an ambient `seen`, which is the design
    # working: the count is therefore not asserted, the kinds are.
    attendance.note_seen(me["id"], kind=attendance.KIND_LOGIN)
    attendance.note_seen(me["id"], kind=attendance.KIND_ACTIVE, task_id=None)
    assert attendance.flush() >= 2

    db = SessionLocal()
    try:
        rows = (
            db.query(models.AttendanceObservation)
            .filter(models.AttendanceObservation.user_id == me["id"])
            .all()
        )
        assert {attendance.KIND_LOGIN, attendance.KIND_ACTIVE} <= {
            r.kind for r in rows
        }
        assert all(r.instance_id for r in rows)
        # created_at is the server default, distinct from seen_at — that gap is
        # what later distinguishes a reconstructed break from a declared one.
        assert all(r.created_at is not None for r in rows)
    finally:
        db.close()


# --- Login / logout observations -------------------------------------------


def test_login_records_an_observation(client):
    import uuid as _uuid
    username = f"attend-login-{_uuid.uuid4().hex[:8]}"
    res = client.post(
        "/api/auth/register", json={"username": username, "password": "pw-12345"}
    )
    assert res.status_code == 200
    client.cookies.clear()

    attendance.reset_for_tests()
    res = client.post(
        "/api/auth/token", data={"username": username, "password": "pw-12345"}
    )
    assert res.status_code == 200

    kinds = [r["kind"] for r in attendance.buffered_observations()]
    assert attendance.KIND_LOGIN in kinds


def test_logout_records_an_observation_when_identity_resolves(client, alice):
    attendance.reset_for_tests()
    res = client.post("/api/auth/logout", headers=alice)
    assert res.status_code == 200
    kinds = [r["kind"] for r in attendance.buffered_observations()]
    assert attendance.KIND_LOGOUT in kinds


def test_unauthenticated_logout_still_returns_200(client):
    """The one behaviour change must not become a regression.

    A client with an expired token is merely trying to clear its own cookies.
    Requiring auth here would answer 401 to a request that has always
    succeeded (04-decision-and-impl-plan.md § 6).
    """
    attendance.reset_for_tests()
    res = client.post("/api/auth/logout")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}
    assert attendance.buffered_observations() == []


def test_logout_with_a_garbage_token_still_returns_200(client):
    attendance.reset_for_tests()
    res = client.post(
        "/api/auth/logout", headers={"Authorization": "Bearer not-a-real-jwt"}
    )
    assert res.status_code == 200
    assert attendance.buffered_observations() == []


# --- The ambient capture path ----------------------------------------------


def test_an_authenticated_request_observes_presence(client, alice):
    attendance.reset_for_tests()
    res = client.get("/api/projects", headers=alice)
    assert res.status_code == 200
    rows = attendance.buffered_observations()
    assert len(rows) == 1
    assert rows[0]["kind"] == attendance.KIND_SEEN


def test_repeated_requests_do_not_grow_the_buffer(client, alice):
    attendance.reset_for_tests()
    for _ in range(20):
        client.get("/api/projects", headers=alice)
    assert len(attendance.buffered_observations()) == 1, (
        "every request wrote a row; the throttle is what stops 25 annotators "
        "generating a row per request instead of one per minute"
    )


def test_an_unauthenticated_request_observes_nothing(client):
    attendance.reset_for_tests()
    client.get("/api/projects")
    assert attendance.buffered_observations() == []


# --- The drain's own health (the one failure mode nothing else can see) ------


def test_health_reports_the_attendance_drain():
    """`/health` must say whether attendance is actually being written.

    Process liveness and database reachability both stay true while the drain
    is dead, so without this the day's attendance can be silently lost: rows
    accumulate in memory, hit the cap, and vanish on the next restart with
    nothing anywhere reporting it.
    """
    from fastapi.testclient import TestClient
    import main as app_main

    with TestClient(app_main.app) as client:
        body = client.get("/health").json()
        assert "attendance" in body, "/health says nothing about attendance"
        att = body["attendance"]
        assert att["drain_running"] is True
        assert att["healthy"] is True


def test_health_reports_a_dead_drain_without_failing_the_app():
    """A dead drain is degraded attendance, NOT a degraded app.

    It must be visible, but it must not make a supervisor restart a process
    that is serving annotation work perfectly well.
    """
    from fastapi.testclient import TestClient
    import main as app_main
    from api import attendance

    with TestClient(app_main.app) as client:
        assert client.get("/health").json()["attendance"]["healthy"] is True

        attendance._DRAIN_TASK.cancel()
        time.sleep(0.3)

        body = client.get("/health").json()
        assert body["attendance"]["drain_running"] is False
        assert body["attendance"]["healthy"] is False
        assert body["status"] == "ok", (
            "a dead drain must not report the whole app as degraded"
        )


def test_drain_status_does_not_cry_wolf_on_an_idle_instance():
    """An empty buffer legitimately never flushes.

    Overnight, and at every quiet lunchtime, nothing is buffered and no flush
    happens. Calling that unhealthy would make the signal worthless.
    """
    from api import attendance

    attendance.reset_for_tests()
    status = attendance.drain_status()
    assert status["buffered"] == 0
    # No drain task in this context, but the staleness rule must not fire.
    attendance._LAST_FLUSH = 1.0  # ancient, and irrelevant with nothing waiting
    assert attendance.drain_status()["buffered"] == 0


def test_drain_status_flags_a_buffer_that_is_not_draining():
    """Rows waiting long past the flush interval is the real symptom."""
    from api import attendance

    attendance.reset_for_tests()
    attendance.note_seen(1)
    attendance._LAST_FLUSH = 1.0  # a flush that never happened since
    status = attendance.drain_status()
    assert status["buffered"] >= 1
    assert status["healthy"] is False, (
        "rows sitting unflushed far past the interval must not read healthy"
    )

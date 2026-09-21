"""R4 — the admin gate, the read endpoints and the grant script.

The security-shaped assertions here matter more than the aggregation ones,
which `test_attendance_report.py` already covers:

- a non-admin gets **404**, not 403, and gets it *before* any aggregation runs
- the reads issue no INSERT/UPDATE (CLAUDE.md rule 4)
- `GET /api/attendance/me` is self-scoped **by construction** — it accepts no
  user parameter at all, which is the leak class this codebase already fixed
  once in `api/routers/time_logs.py`
- `is_admin` is reachable from no API path
"""
import sys
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

import config
import models
from api import attendance, attendance_report as report
from database import SessionLocal


@pytest.fixture(autouse=True)
def _clean_buffer():
    attendance.reset_for_tests()
    original = config.ATTENDANCE_ENABLED
    config.ATTENDANCE_ENABLED = True
    yield
    config.ATTENDANCE_ENABLED = original
    attendance.reset_for_tests()


def _user_id(client, headers):
    return client.get("/api/auth/me", headers=headers).json()["id"]


def _make_admin(user_id, admin=True):
    """Grant admin directly, as only a script may (there is no API path)."""
    db = SessionLocal()
    try:
        user = db.get(models.User, user_id)
        user.is_admin = admin
        db.commit()
    finally:
        db.close()


@pytest.fixture
def admin(client, alice):
    _make_admin(_user_id(client, alice))
    return alice


def _today():
    return datetime.now(report.site_tz()).date().isoformat()


def _seed(user_id, *, kind="seen", minutes_ago=5, task_id=None):
    """Put an observation in the buffer, dated relative to now."""
    attendance.note_seen(
        user_id,
        kind=kind,
        task_id=task_id,
        seen_at=datetime.now(timezone.utc) - timedelta(minutes=minutes_ago),
    )


# --- The admin gate ---------------------------------------------------------


@pytest.mark.parametrize("path", [
    "/api/attendance/days/2026-09-21",
    "/api/attendance/range?from=2026-09-21&to=2026-09-21",
    "/api/attendance/days/2026-09-21/users/1/sessions",
])
def test_a_non_admin_gets_404_not_403(client, bob, path):
    """404, matching the contract for a caller with no role at all: the
    existence of the dashboard is not disclosed to someone who may not use it."""
    res = client.get(path, headers=bob)
    assert res.status_code == 404, (
        f"{path} answered {res.status_code}; a non-admin must not be able to "
        "tell an attendance endpoint from a nonexistent one"
    )


def test_an_unauthenticated_caller_gets_401(client):
    res = client.get("/api/attendance/days/2026-09-21")
    assert res.status_code == 401


def test_an_admin_gets_through(client, admin):
    res = client.get(f"/api/attendance/days/{_today()}", headers=admin)
    assert res.status_code == 200
    body = res.json()
    assert body["timezone"] == config.ATTENDANCE_TZ
    assert body["instance_id"] == config.ATTENDANCE_INSTANCE_ID


def test_the_permission_check_runs_before_any_aggregation(client, bob, monkeypatch):
    """CLAUDE.md rule 1c ordering. A 403/404 must never arrive after the work
    it was meant to prevent — the aggregation must not run at all."""
    from api.routers import attendance as router

    def explode(*args, **kwargs):
        raise AssertionError(
            "aggregation ran for a non-admin; the permission check must come first"
        )

    monkeypatch.setattr(router, "_collect", explode)
    res = client.get(f"/api/attendance/days/{_today()}", headers=bob)
    assert res.status_code == 404


def test_admin_is_not_grantable_through_any_api_path(client, bob):
    """Invariant 7: the only writer of is_admin is a script.

    A user who could set it on themselves would make every other check here
    decorative.
    """
    user_id = _user_id(client, bob)
    for path, payload in [
        ("/api/auth/register", {"username": "esc", "password": "pw-12345", "is_admin": True}),
        (f"/api/attendance/users/{user_id}/admin", {"is_admin": True}),
    ]:
        client.post(path, json=payload, headers=bob)

    db = SessionLocal()
    try:
        assert db.get(models.User, user_id).is_admin is False
    finally:
        db.close()


# --- Reads do not write -----------------------------------------------------


def test_the_read_endpoints_issue_no_writes(client, admin):
    """CLAUDE.md rule 4. The dashboard read cannot flush the buffer, refresh
    the rollup or prune."""
    from database import engine

    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        head = statement.strip().split(None, 1)[0].upper()
        if head in ("INSERT", "UPDATE", "DELETE"):
            statements.append(statement.strip()[:120])

    event.listen(engine, "before_cursor_execute", record)
    try:
        assert client.get(f"/api/attendance/days/{_today()}", headers=admin).status_code == 200
        assert client.get(
            f"/api/attendance/range?from={_today()}&to={_today()}", headers=admin
        ).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert statements == [], f"a read endpoint wrote to the database: {statements}"


def test_reading_does_not_drain_the_buffer(client, admin):
    """The buffer is read, not flushed — flushing from a GET would be a write."""
    _seed(_user_id(client, admin))
    before = len(attendance.buffered_observations())
    client.get(f"/api/attendance/days/{_today()}", headers=admin)
    assert len(attendance.buffered_observations()) == before


# --- The live buffer merge --------------------------------------------------


def test_an_unflushed_observation_still_appears(client, admin):
    """An annotator working right now must not show a session that ended at
    the last flush — that reads as "they left" rather than "they are here"."""
    user_id = _user_id(client, admin)
    _seed(user_id, minutes_ago=3)
    assert attendance.buffered_observations(), "precondition: nothing buffered"

    res = client.get(f"/api/attendance/days/{_today()}", headers=admin)
    rows = res.json()["rows"]
    assert any(row["user_id"] == user_id for row in rows), (
        "a buffered observation did not reach the read; the dashboard would "
        "lag a flush interval behind reality"
    )


def test_an_open_session_is_reported_open(client, admin):
    user_id = _user_id(client, admin)
    _seed(user_id, minutes_ago=1)
    rows = client.get(f"/api/attendance/days/{_today()}", headers=admin).json()["rows"]
    mine = [r for r in rows if r["user_id"] == user_id]
    assert mine and mine[0]["last_seen_reason"] == "open"


# --- Range bounds -----------------------------------------------------------


def test_a_range_beyond_the_ceiling_is_refused(client, admin):
    """Bounded server-side so one request cannot scan the whole retention
    window."""
    res = client.get(
        "/api/attendance/range?from=2026-01-01&to=2026-12-31", headers=admin
    )
    assert res.status_code == 400
    assert "maximum" in res.json()["detail"].lower()


def test_a_reversed_range_is_refused(client, admin):
    res = client.get(
        "/api/attendance/range?from=2026-09-21&to=2026-09-01", headers=admin
    )
    assert res.status_code == 400


def test_a_malformed_date_is_refused(client, admin):
    res = client.get("/api/attendance/days/not-a-date", headers=admin)
    assert res.status_code == 400


def test_a_range_at_exactly_the_ceiling_is_allowed(client, admin):
    res = client.get(
        "/api/attendance/range?from=2026-09-01&to=2026-10-01", headers=admin
    )
    assert res.status_code == 200


# --- Self-scoping -----------------------------------------------------------


def test_me_is_reachable_by_a_non_admin(client, bob):
    """Everyone sees their own attendance; the admin gate is for other people's."""
    assert client.get("/api/attendance/me", headers=bob).status_code == 200


def test_me_returns_only_the_callers_rows(client, alice, bob):
    alice_id = _user_id(client, alice)
    bob_id = _user_id(client, bob)
    _seed(alice_id)
    _seed(bob_id)

    rows = client.get("/api/attendance/me", headers=bob).json()["rows"]
    assert rows, "precondition: bob should have a row"
    assert {r["user_id"] for r in rows} == {bob_id}
    assert alice_id not in {r["user_id"] for r in rows}


def test_me_accepts_no_user_parameter(client, alice, bob):
    """Safe by construction, not by a check.

    An endpoint that accepts an id and checks it is a check that can be
    forgotten. Passing another user's id must change nothing.
    """
    alice_id = _user_id(client, alice)
    bob_id = _user_id(client, bob)
    _seed(alice_id)
    _seed(bob_id)

    for query in (f"?user_id={alice_id}", f"?user={alice_id}", f"?username=alice"):
        rows = client.get(f"/api/attendance/me{query}", headers=bob).json()["rows"]
        assert {r["user_id"] for r in rows} <= {bob_id}, (
            f"{query} leaked another user's attendance"
        )


def test_me_respects_the_range_ceiling(client, bob):
    res = client.get(
        "/api/attendance/me?from=2026-01-01&to=2026-12-31", headers=bob
    )
    assert res.status_code == 400


# --- Sessions endpoint ------------------------------------------------------


def test_sessions_endpoint_returns_the_breakdown(client, admin):
    user_id = _user_id(client, admin)
    _seed(user_id, minutes_ago=30)
    _seed(user_id, kind=attendance.KIND_LOGOUT, minutes_ago=25)

    res = client.get(
        f"/api/attendance/days/{_today()}/users/{user_id}/sessions", headers=admin
    )
    assert res.status_code == 200
    sessions = res.json()["sessions"]
    assert sessions
    assert sessions[0]["end_reason"] == "logout"


# --- The grant script -------------------------------------------------------


def test_grant_admin_is_idempotent_and_reversible(client, bob):
    """Q13: re-runnable, for any number of admins, and revocable — an admin
    role that can only ever be granted is a one-way door."""
    import scripts.grant_admin as grant_admin

    user_id = _user_id(client, bob)
    username = client.get("/api/auth/me", headers=bob).json()["username"]

    def is_admin_now():
        db = SessionLocal()
        try:
            return bool(db.get(models.User, user_id).is_admin)
        finally:
            db.close()

    argv = sys.argv
    try:
        sys.argv = ["grant_admin.py", username]
        assert grant_admin.main() == 0
        assert is_admin_now() is True

        # Granting twice changes nothing and does not error.
        assert grant_admin.main() == 0
        assert is_admin_now() is True

        sys.argv = ["grant_admin.py", username, "--revoke"]
        assert grant_admin.main() == 0
        assert is_admin_now() is False
    finally:
        sys.argv = argv


def test_grant_admin_rejects_an_unknown_user():
    import scripts.grant_admin as grant_admin

    argv = sys.argv
    try:
        sys.argv = ["grant_admin.py", "nobody-with-this-name"]
        assert grant_admin.main() == 1
    finally:
        sys.argv = argv


def test_several_admins_can_coexist(client, alice, bob, carol):
    """Not a one-shot bootstrap (Q13)."""
    ids = [_user_id(client, h) for h in (alice, bob, carol)]
    for user_id in ids:
        _make_admin(user_id)

    db = SessionLocal()
    try:
        count = (
            db.query(models.User)
            .filter(models.User.id.in_(ids), models.User.is_admin.is_(True))
            .count()
        )
        assert count == 3
    finally:
        db.close()



# --- Declared breaks (R6) ---------------------------------------------------


def _csrf_headers(client, headers):
    """Bearer clients are CSRF-exempt, so these ride the Authorization header."""
    return headers


def test_break_start_and_end_round_trip(client, bob):
    user_id = _user_id(client, bob)

    started = client.post("/api/attendance/break/start", headers=bob)
    assert started.status_code == 200, started.text
    assert started.json()["already_open"] is False

    ended = client.post("/api/attendance/break/end", headers=bob)
    assert ended.status_code == 200
    body = ended.json()
    assert body["was_open"] is True
    assert body["seconds"] >= 0

    kinds = [
        row["kind"] for row in attendance.buffered_observations(user_ids={user_id})
    ]
    assert attendance.KIND_BREAK_START in kinds
    assert attendance.KIND_BREAK_END in kinds


def test_starting_a_break_twice_does_not_open_a_second(client, bob):
    """Two overlapping breaks cannot be corrected afterwards — the table is
    append-only (Q24) — so the endpoint is idempotent rather than an error."""
    user_id = _user_id(client, bob)

    first = client.post("/api/attendance/break/start", headers=bob)
    second = client.post("/api/attendance/break/start", headers=bob)

    assert second.status_code == 200
    assert second.json()["already_open"] is True
    assert second.json()["started_at"] == first.json()["started_at"]

    starts = [
        row for row in attendance.buffered_observations(user_ids={user_id})
        if row["kind"] == attendance.KIND_BREAK_START
    ]
    assert len(starts) == 1, "a second overlapping break was opened"


def test_ending_a_break_that_was_never_started_is_a_noop_not_an_error(client, bob):
    """The pagehide beacon and the End Break button can both fire for one
    break; the second must not fail."""
    res = client.post("/api/attendance/break/end", headers=bob)
    assert res.status_code == 200
    assert res.json()["was_open"] is False
    assert res.json()["seconds"] == 0


def test_ending_twice_is_safe(client, bob):
    client.post("/api/attendance/break/start", headers=bob)
    assert client.post("/api/attendance/break/end", headers=bob).status_code == 200
    second = client.post("/api/attendance/break/end", headers=bob)
    assert second.status_code == 200
    assert second.json()["was_open"] is False


def test_a_break_can_be_started_again_after_ending(client, bob):
    client.post("/api/attendance/break/start", headers=bob)
    client.post("/api/attendance/break/end", headers=bob)
    again = client.post("/api/attendance/break/start", headers=bob)
    assert again.json()["already_open"] is False


def test_open_break_reports_an_unfinished_break(client, bob):
    """So a reloaded page restores its overlay rather than stranding the user
    with an open break and no way to end it."""
    assert client.get("/api/attendance/break/open", headers=bob).json() is None

    client.post("/api/attendance/break/start", headers=bob)
    body = client.get("/api/attendance/break/open", headers=bob).json()
    assert body is not None
    assert body["started_at"]
    assert body["seconds"] >= 0

    client.post("/api/attendance/break/end", headers=bob)
    assert client.get("/api/attendance/break/open", headers=bob).json() is None


def test_breaks_are_self_scoped_and_take_no_user_parameter(client, alice, bob):
    """Safe by construction: an endpoint that cannot name another user cannot
    open or close a break on their behalf."""
    alice_id = _user_id(client, alice)
    bob_id = _user_id(client, bob)

    client.post(
        f"/api/attendance/break/start?user_id={alice_id}", headers=bob
    )

    buffered = attendance.buffered_observations()
    starts = [
        row for row in buffered if row["kind"] == attendance.KIND_BREAK_START
    ]
    assert starts, "precondition: a break should have been recorded"
    assert all(row["user_id"] == bob_id for row in starts), (
        "a break was recorded against another user"
    )
    assert alice_id not in {row["user_id"] for row in starts}


def test_break_endpoints_require_authentication(client):
    assert client.post("/api/attendance/break/start").status_code == 401
    assert client.post("/api/attendance/break/end").status_code == 401
    assert client.get("/api/attendance/break/open").status_code == 401


def test_a_break_is_not_admin_gated(client, bob):
    """Every annotator declares their own breaks; the admin gate is for seeing
    other people's attendance."""
    assert client.post("/api/attendance/break/start", headers=bob).status_code == 200


def test_a_declared_break_reaches_the_dashboard(client, admin):
    """End to end: declaring a break shows up as break time in the register."""
    user_id = _user_id(client, admin)
    _seed(user_id, minutes_ago=30)
    client.post("/api/attendance/break/start", headers=admin)
    client.post("/api/attendance/break/end", headers=admin)

    rows = client.get(f"/api/attendance/days/{_today()}", headers=admin).json()["rows"]
    mine = [r for r in rows if r["user_id"] == user_id]
    assert mine, "the admin's own day should be present"
    assert mine[0]["break_seconds"] >= 0
    assert mine[0]["manual_break_seconds"] == 0, (
        "a declared break must not be counted as manually entered"
    )

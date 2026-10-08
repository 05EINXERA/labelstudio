"""GET /api/tasks/{id}/assignment-history (.devnotes/features/task-assignment-history/)."""
import datetime

import models
import api.routers.tasks as tasks_router
from database import SessionLocal

from tests.test_task_assignment import _me, _project, _task, _team_with_grant


def _assign(client, headers, task_id, **body):
    res = client.patch(f"/api/tasks/{task_id}/assignment", json=body, headers=headers)
    assert res.status_code == 200, res.text


def _history(client, headers, task_id):
    return client.get(f"/api/tasks/{task_id}/assignment-history", headers=headers)


def _count_events():
    db = SessionLocal()
    try:
        return db.query(models.TaskAssignmentEvent).count()
    finally:
        db.close()


def test_owner_reads_history_oldest_first(client, alice, bob, carol):
    pid = _project(client, alice)
    team = _team_with_grant(client, alice, pid, "Alpha", members=[bob, carol])
    tid = _task(client, alice, pid)
    bob_name = _me(client, bob)["username"]
    carol_name = _me(client, carol)["username"]
    _assign(client, alice, tid, assigned_team_id=team["id"], assignee_user_id=_me(client, bob)["id"])
    _assign(client, alice, tid, assignee_user_id=_me(client, carol)["id"])

    res = _history(client, alice, tid)

    assert res.status_code == 200, res.text
    first, second = res.json()
    assert first["from"] == {"team": None, "team_id": None, "user": None, "user_id": None}
    assert first["to"]["user"] == bob_name and first["to"]["team"] == "Alpha"
    assert second["from"]["user"] == bob_name and second["to"]["user"] == carol_name
    assert first["id"] < second["id"]
    assert first["source"] == "assign"
    assert first["changed_by_username"] == _me(client, alice)["username"]


def test_unassigned_task_has_empty_history(client, alice):
    """H-15."""
    pid = _project(client, alice)
    tid = _task(client, alice, pid)

    res = _history(client, alice, tid)

    assert res.status_code == 200 and res.json() == []


def test_stranger_gets_404_and_annotator_gets_403(client, alice, bob, carol):
    """H-19: 404 with no role, 403 naming the role with a low one."""
    pid = _project(client, alice)
    _team_with_grant(client, alice, pid, "Alpha", role="annotator", members=[bob])
    tid = _task(client, alice, pid)

    assert _history(client, carol, tid).status_code == 404
    res = _history(client, bob, tid)
    assert res.status_code == 403
    assert "manager" in res.json()["detail"].lower()


def test_unauthenticated_is_rejected(client, alice):
    pid = _project(client, alice)
    tid = _task(client, alice, pid)

    assert client.get(f"/api/tasks/{tid}/assignment-history").status_code in (401, 403)


def test_unknown_task_is_404(client, alice):
    assert _history(client, alice, 99999999).status_code == 404


def test_get_writes_nothing(client, alice):
    """CLAUDE.md rule 4."""
    pid = _project(client, alice)
    team = _team_with_grant(client, alice, pid, "Alpha")
    tid = _task(client, alice, pid)
    _assign(client, alice, tid, assigned_team_id=team["id"])
    before = _count_events()

    for _ in range(3):
        _history(client, alice, tid)

    assert _count_events() == before


def test_timestamps_are_explicit_utc(client, alice):
    """F6 / H-18: a naive SQLite value must still reach the browser as UTC."""
    pid = _project(client, alice)
    team = _team_with_grant(client, alice, pid, "Alpha")
    tid = _task(client, alice, pid)
    _assign(client, alice, tid, assigned_team_id=team["id"])

    stamp = _history(client, alice, tid).json()[0]["created_at"]

    assert stamp.endswith("Z") or stamp.endswith("+00:00"), stamp


def test_as_utc_handles_naive_and_aware():
    naive = datetime.datetime(2026, 9, 20, 12, 0, 0)
    aware = datetime.datetime(2026, 9, 20, 14, 0, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=2)))

    assert tasks_router._as_utc(naive).utcoffset() == datetime.timedelta(0)
    assert tasks_router._as_utc(naive).hour == 12
    assert tasks_router._as_utc(aware).hour == 12


def test_backfilled_row_is_reported_as_the_initial_state(client, alice):
    """H-16: a migration-seeded row has no actor and no 'from'."""
    pid = _project(client, alice)
    team = _team_with_grant(client, alice, pid, "Alpha")
    tid = _task(client, alice, pid)
    db = SessionLocal()
    try:
        db.add(models.TaskAssignmentEvent(
            task_id=tid, source="backfill", team_to_id=team["id"], team_to_name="Alpha",
        ))
        db.commit()
    finally:
        db.close()

    (row,) = _history(client, alice, tid).json()

    assert row["source"] == "backfill" and row["changed_by_username"] is None
    assert row["from"]["team"] is None and row["to"]["team"] == "Alpha"


def test_history_is_capped_to_the_newest_rows_in_ascending_order(client, alice, monkeypatch):
    """H-25."""
    monkeypatch.setattr(tasks_router, "ASSIGNMENT_HISTORY_LIMIT", 3)
    pid = _project(client, alice)
    tid = _task(client, alice, pid)
    db = SessionLocal()
    try:
        for n in range(5):
            db.add(models.TaskAssignmentEvent(task_id=tid, source="assign", team_to_name=f"T{n}"))
        db.commit()
    finally:
        db.close()

    rows = _history(client, alice, tid).json()

    assert [r["to"]["team"] for r in rows] == ["T2", "T3", "T4"]

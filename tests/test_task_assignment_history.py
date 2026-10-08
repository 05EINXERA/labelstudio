"""Recording of task-assignment changes (.devnotes/features/task-assignment-history/).

Covers every writer of the assignment columns — W1..W6 in 01_AUDIT.md — and the
edge cases H-01..H-06, H-08..H-11, H-14 in 03_EDGE_CASES.md.
"""
import models
from database import SessionLocal

from tests.test_task_assignment import _me, _project, _task, _team_with_grant


def _events(task_id):
    db = SessionLocal()
    try:
        rows = (
            db.query(models.TaskAssignmentEvent)
            .filter(models.TaskAssignmentEvent.task_id == task_id)
            .order_by(models.TaskAssignmentEvent.id)
            .all()
        )
        for r in rows:
            db.expunge(r)
        return rows
    finally:
        db.close()


def _assign(client, headers, task_id, **body):
    res = client.patch(f"/api/tasks/{task_id}/assignment", json=body, headers=headers)
    assert res.status_code == 200, res.text
    return res


def test_single_assign_records_an_event(client, alice, bob):
    pid = _project(client, alice)
    team = _team_with_grant(client, alice, pid, "Alpha", members=[bob])
    tid = _task(client, alice, pid)
    bob_id = _me(client, bob)["id"]
    alice_id = _me(client, alice)["id"]

    _assign(client, alice, tid, assigned_team_id=team["id"], assignee_user_id=bob_id)

    (e,) = _events(tid)
    assert e.source == "assign" and e.changed_by_id == alice_id
    assert (e.team_from_id, e.user_from_id) == (None, None)
    assert (e.team_to_id, e.user_to_id) == (team["id"], bob_id)
    assert e.team_to_name == "Alpha" and e.user_to_name == _me(client, bob)["username"]
    assert e.created_at is not None


def test_reassign_records_from_and_to(client, alice, bob, carol):
    pid = _project(client, alice)
    team = _team_with_grant(client, alice, pid, "Alpha", members=[bob, carol])
    tid = _task(client, alice, pid)
    bob_id, carol_id = _me(client, bob)["id"], _me(client, carol)["id"]

    _assign(client, alice, tid, assigned_team_id=team["id"], assignee_user_id=bob_id)
    _assign(client, alice, tid, assignee_user_id=carol_id)

    first, second = _events(tid)
    assert (second.user_from_id, second.user_to_id) == (bob_id, carol_id)
    assert second.team_from_id == second.team_to_id == team["id"]
    assert second.user_from_name == _me(client, bob)["username"]


def test_unassign_records_a_clear(client, alice):
    pid = _project(client, alice)
    team = _team_with_grant(client, alice, pid, "Alpha")
    tid = _task(client, alice, pid)

    _assign(client, alice, tid, assigned_team_id=team["id"])
    _assign(client, alice, tid, assigned_team_id=None)

    _, e = _events(tid)
    assert (e.team_from_id, e.team_to_id) == (team["id"], None)
    assert e.team_from_name == "Alpha" and e.team_to_name is None


def test_noop_assign_records_nothing(client, alice):
    """H-01: Apply with unchanged values must not add a row."""
    pid = _project(client, alice)
    team = _team_with_grant(client, alice, pid, "Alpha")
    tid = _task(client, alice, pid)

    _assign(client, alice, tid, assigned_team_id=team["id"])
    _assign(client, alice, tid, assigned_team_id=team["id"])
    _assign(client, alice, tid)  # nothing sent at all

    assert len(_events(tid)) == 1


def test_rejected_assignment_records_nothing(client, alice):
    """H-05: a 422 (team has no grant) leaves no history behind."""
    pid = _project(client, alice)
    other = client.post("/api/teams", json={"name": "NoGrant"}, headers=alice).json()
    tid = _task(client, alice, pid)

    res = client.patch(
        f"/api/tasks/{tid}/assignment", json={"assigned_team_id": other["id"]}, headers=alice
    )

    assert res.status_code == 422
    assert _events(tid) == []


def test_bulk_assign_records_only_tasks_that_changed(client, alice):
    """H-02."""
    pid = _project(client, alice)
    team = _team_with_grant(client, alice, pid, "Alpha")
    already, fresh = _task(client, alice, pid), _task(client, alice, pid)
    _assign(client, alice, already, assigned_team_id=team["id"])

    res = client.post(
        "/api/tasks/bulk-assign",
        json={"ids": [already, fresh], "assigned_team_id": team["id"]},
        headers=alice,
    )

    assert res.status_code == 200
    assert len(_events(already)) == 1  # only the earlier manual one
    (e,) = _events(fresh)
    assert e.source == "bulk_assign" and e.team_to_id == team["id"]


def test_bulk_assign_team_only_keeps_each_persons_assignment(client, alice, bob):
    """H-03: an unsent field is per-task, not overwritten."""
    pid = _project(client, alice)
    a = _team_with_grant(client, alice, pid, "Alpha", members=[bob])
    b = _team_with_grant(client, alice, pid, "Beta")
    tid = _task(client, alice, pid)
    bob_id = _me(client, bob)["id"]
    _assign(client, alice, tid, assigned_team_id=a["id"], assignee_user_id=bob_id)

    client.post(
        "/api/tasks/bulk-assign",
        json={"ids": [tid], "assigned_team_id": b["id"]},
        headers=alice,
    )

    _, e = _events(tid)
    assert (e.team_from_id, e.team_to_id) == (a["id"], b["id"])
    assert e.user_from_id == e.user_to_id == bob_id


def test_bulk_assign_skips_tasks_the_caller_cannot_manage(client, alice, bob):
    """H-04."""
    mine = _project(client, alice, "Mine")
    theirs = _project(client, bob, "Theirs")
    team = _team_with_grant(client, alice, mine, "Alpha")
    t1, t2 = _task(client, alice, mine), _task(client, bob, theirs)

    client.post(
        "/api/tasks/bulk-assign",
        json={"ids": [t1, t2], "assigned_team_id": team["id"]},
        headers=alice,
    )

    assert len(_events(t1)) == 1
    assert _events(t2) == []


def test_removing_a_member_records_a_system_clear(client, alice, bob):
    """H-09: the history must agree with the table when a person is removed."""
    pid = _project(client, alice)
    team = _team_with_grant(client, alice, pid, "Alpha", members=[bob])
    tid = _task(client, alice, pid)
    bob_id, alice_id = _me(client, bob)["id"], _me(client, alice)["id"]
    _assign(client, alice, tid, assigned_team_id=team["id"], assignee_user_id=bob_id)

    res = client.delete(f"/api/teams/{team['id']}/members/{bob_id}", headers=alice)
    assert res.status_code == 200, res.text

    _, e = _events(tid)
    assert e.source == "member_removed" and e.changed_by_id == alice_id
    assert (e.team_from_id, e.user_from_id) == (team["id"], bob_id)
    assert (e.team_to_id, e.user_to_id) == (None, None)


def test_leaving_a_team_records_a_system_clear(client, alice, bob):
    pid = _project(client, alice)
    team = _team_with_grant(client, alice, pid, "Alpha", members=[bob])
    tid = _task(client, alice, pid)
    bob_id = _me(client, bob)["id"]
    _assign(client, alice, tid, assigned_team_id=team["id"], assignee_user_id=bob_id)

    res = client.delete(f"/api/teams/{team['id']}/members/me", headers=bob)
    assert res.status_code == 200, res.text

    _, e = _events(tid)
    assert e.source == "member_left" and e.changed_by_id == bob_id
    assert (e.team_to_id, e.user_to_id) == (None, None)


def test_revoking_a_grant_records_the_team_clear(client, alice):
    """H-10: team cleared, person kept."""
    pid = _project(client, alice)
    team = _team_with_grant(client, alice, pid, "Alpha")
    tid = _task(client, alice, pid)
    _assign(client, alice, tid, assigned_team_id=team["id"])

    res = client.delete(f"/api/projects/{pid}/grants/{team['id']}", headers=alice)
    assert res.status_code in (200, 204), res.text

    _, e = _events(tid)
    assert e.source == "grant_revoked"
    assert (e.team_from_id, e.team_to_id) == (team["id"], None)


def test_deleting_a_team_records_it_and_keeps_history_readable(client, alice):
    """H-08: the team's name survives in the log after the team is gone."""
    pid = _project(client, alice)
    team = _team_with_grant(client, alice, pid, "Alpha")
    tid = _task(client, alice, pid)
    _assign(client, alice, tid, assigned_team_id=team["id"])

    res = client.delete(
        f"/api/teams/{team['id']}?confirm={team['slug']}", headers=alice
    )
    assert res.status_code in (200, 204), res.text

    first, second = _events(tid)
    assert second.source == "team_deleted" and second.team_to_id is None
    assert first.team_to_name == "Alpha"


def test_deleting_a_task_removes_its_events(client, alice):
    """H-11."""
    pid = _project(client, alice)
    team = _team_with_grant(client, alice, pid, "Alpha")
    tid = _task(client, alice, pid)
    _assign(client, alice, tid, assigned_team_id=team["id"])

    res = client.delete(f"/api/tasks/{tid}", headers=alice)
    assert res.status_code == 200, res.text

    assert _events(tid) == []


def test_events_are_ordered_by_id(client, alice, bob, carol):
    """H-14: rows order by (created_at, id); the chain is whatever each writer
    saw. Pinned so nobody 'fixes' it into a lock by accident."""
    pid = _project(client, alice)
    team = _team_with_grant(client, alice, pid, "Alpha", members=[bob, carol])
    tid = _task(client, alice, pid)
    _assign(client, alice, tid, assigned_team_id=team["id"], assignee_user_id=_me(client, bob)["id"])
    _assign(client, alice, tid, assignee_user_id=_me(client, carol)["id"])

    ids = [e.id for e in _events(tid)]
    assert ids == sorted(ids)

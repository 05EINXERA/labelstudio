"""Task assignment and `restrict_to_assigned_team`.

Spec: .devnotes/teams/03_API.md § 4.3, 01_DESIGN.md §§ 3.2–3.4.
Edge cases: E-09, E-10, E-24.

Two separate things are under test and they are deliberately different:
**project grants** decide what you can reach at all, while **task assignment**
distributes work inside a project both teams already reach. Assignment only
restricts writes when the project opts in.
"""
import models
from database import SessionLocal


def _me(client, headers):
    return client.get("/api/auth/me", headers=headers).json()


def _project(client, headers, name="P"):
    return client.post(
        "/api/projects",
        json={"name": name, "slug": name.lower(), "creator": "ignored"},
        headers=headers,
    ).json()["id"]


def _task(client, headers, project_id):
    return client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": "a.jpg", "status": "New"},
        headers=headers,
    ).json()["id"]


def _team_with_grant(client, owner, project_id, name, role="annotator", members=()):
    team = client.post("/api/teams", json={"name": name}, headers=owner).json()
    for headers in members:
        client.post(
            f"/api/teams/{team['id']}/members",
            json={"username": _me(client, headers)["username"], "role": "member"},
            headers=owner,
        )
    client.post(
        f"/api/projects/{project_id}/grants",
        json={"team_id": team["id"], "role": role},
        headers=owner,
    )
    return team


def _set_restrict(project_id, value=True):
    """Flip the opt-in flag directly: there is no API for it until Phase 4."""
    db = SessionLocal()
    try:
        project = db.get(models.Project, project_id)
        project.restrict_to_assigned_team = value
        db.commit()
    finally:
        db.close()


# --- who may assign ----------------------------------------------------------


def test_assign_team_requires_manager(client, alice, bob):
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Alpha", members=[bob])
    task_id = _task(client, alice, project_id)

    res = client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"]},
        headers=bob,
    )

    assert res.status_code == 403


def test_owner_can_assign(client, alice):
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Alpha")
    task_id = _task(client, alice, project_id)

    res = client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"]},
        headers=alice,
    )

    assert res.status_code == 200
    assert res.json()["assigned_team_id"] == team["id"]


# --- validation --------------------------------------------------------------


def test_assign_team_without_grant_422(client, alice):
    """E-09: a task assigned to a team that cannot see the project is invisible
    work — the worst kind of silent failure."""
    project_id = _project(client, alice)
    ungranted = client.post("/api/teams", json={"name": "Ungranted"}, headers=alice).json()
    task_id = _task(client, alice, project_id)

    res = client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": ungranted["id"]},
        headers=alice,
    )

    assert res.status_code == 422
    assert "does not have access" in res.json()["detail"]


def test_assign_user_outside_team_warns_not_rejects(client, alice, bob):
    """E-10: individual assignment is advisory by design. A reviewer from
    another team taking one task is legitimate; rejecting it would make the
    advisory field behave like an enforced one."""
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Alpha")
    task_id = _task(client, alice, project_id)
    bob_id = _me(client, bob)["id"]

    res = client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"], "assignee_user_id": bob_id},
        headers=alice,
    )

    assert res.status_code == 200
    assert res.json()["assignee_user_id"] == bob_id
    assert any("not a member" in w for w in res.json()["warnings"])


def test_assign_user_inside_team_has_no_warning(client, alice, bob):
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Alpha", members=[bob])
    task_id = _task(client, alice, project_id)

    res = client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"], "assignee_user_id": _me(client, bob)["id"]},
        headers=alice,
    )

    assert res.json()["warnings"] == []


def test_null_unassigns_and_omitted_leaves_alone(client, alice, bob):
    """An explicit null returns the task to the shared pool; an omitted field is
    untouched. Pydantic cannot tell those apart without `model_fields_set`."""
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Alpha", members=[bob])
    task_id = _task(client, alice, project_id)
    bob_id = _me(client, bob)["id"]
    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"], "assignee_user_id": bob_id},
        headers=alice,
    )

    # Omitting assignee_user_id must not wipe it.
    partial = client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"]},
        headers=alice,
    )
    assert partial.json()["assignee_user_id"] == bob_id

    cleared = client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": None, "assignee_user_id": None},
        headers=alice,
    )
    assert cleared.json()["assigned_team_id"] is None
    assert cleared.json()["assignee_user_id"] is None


# --- restrict_to_assigned_team ----------------------------------------------


def test_unassigned_task_writable_by_any_annotator(client, alice, bob):
    """§ 3.3: a task with no team is the shared pool, even with the flag on.
    Every pre-teams task is in this state, which is what makes the migration
    behaviour-neutral."""
    project_id = _project(client, alice)
    _team_with_grant(client, alice, project_id, "Alpha", members=[bob])
    task_id = _task(client, alice, project_id)
    _set_restrict(project_id, True)

    res = client.post(
        "/api/tasks", json={"id": task_id, "status": "In Progress"}, headers=bob
    )

    assert res.status_code == 200


def test_restrict_flag_off_allows_all(client, alice, bob):
    """Default false = today's behaviour: assignment is purely advisory."""
    project_id = _project(client, alice)
    alpha = _team_with_grant(client, alice, project_id, "Alpha")
    _team_with_grant(client, alice, project_id, "Beta", members=[bob])
    task_id = _task(client, alice, project_id)
    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": alpha["id"]},
        headers=alice,
    )

    res = client.post(
        "/api/tasks", json={"id": task_id, "status": "In Progress"}, headers=bob
    )

    assert res.status_code == 200


def test_restrict_flag_blocks_other_team(client, alice, bob):
    """E-24: with the flag on, someone outside the assigned team is refused —
    and told which team holds it, so the message is actionable."""
    project_id = _project(client, alice)
    alpha = _team_with_grant(client, alice, project_id, "Alpha")
    _team_with_grant(client, alice, project_id, "Beta", members=[bob])
    task_id = _task(client, alice, project_id)
    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": alpha["id"]},
        headers=alice,
    )
    _set_restrict(project_id, True)

    res = client.post(
        "/api/tasks", json={"id": task_id, "status": "In Progress"}, headers=bob
    )

    assert res.status_code == 403
    assert "Alpha" in res.json()["detail"]


def test_restrict_flag_allows_the_assigned_team(client, alice, bob):
    project_id = _project(client, alice)
    alpha = _team_with_grant(client, alice, project_id, "Alpha", members=[bob])
    task_id = _task(client, alice, project_id)
    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": alpha["id"]},
        headers=alice,
    )
    _set_restrict(project_id, True)

    res = client.post(
        "/api/tasks", json={"id": task_id, "status": "In Progress"}, headers=bob
    )

    assert res.status_code == 200


def test_permission_403_is_not_a_409(client, alice, bob):
    """A permission failure must never surface as a conflict.

    The checks run before conflict detection precisely so a blocked user gets
    an actionable "you lack the role" rather than a baffling "someone else
    edited this" (03_API.md § 4.2).
    """
    project_id = _project(client, alice)
    alpha = _team_with_grant(client, alice, project_id, "Alpha")
    _team_with_grant(client, alice, project_id, "Beta", members=[bob])
    task_id = _task(client, alice, project_id)
    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": alpha["id"]},
        headers=alice,
    )
    _set_restrict(project_id, True)
    # A deliberately stale token, which would 409 if the order were wrong.
    client.post(
        "/api/tasks",
        json={"id": task_id, "annotations": "[]", "client_id": "tab-A"},
        headers=alice,
    )

    res = client.post(
        "/api/tasks",
        json={
            "id": task_id,
            "annotations": "[]",
            "updated_at": "2020-01-01T00:00:00+00:00",
            "client_id": "tab-B",
        },
        headers=bob,
    )

    assert res.status_code == 403, "a permission error must not be reported as a conflict"


# --- bulk --------------------------------------------------------------------


def test_bulk_assign_reports_skipped(client, alice, bob):
    """Filter-don't-fail: one unreachable id must not lose the whole batch."""
    mine = _project(client, alice, "Mine")
    theirs = _project(client, bob, "Theirs")
    team = _team_with_grant(client, alice, mine, "Alpha")
    ids = [_task(client, alice, mine) for _ in range(2)]
    ids.append(_task(client, bob, theirs))

    res = client.post(
        "/api/tasks/bulk-assign",
        json={"ids": ids, "assigned_team_id": team["id"]},
        headers=alice,
    )

    assert res.status_code == 200
    assert res.json()["updated"] == 2
    assert res.json()["skipped"] == 1


def test_bulk_assign_validates_the_grant(client, alice):
    """E-09 applies to the bulk path too."""
    project_id = _project(client, alice)
    ungranted = client.post("/api/teams", json={"name": "Nope"}, headers=alice).json()
    ids = [_task(client, alice, project_id)]

    res = client.post(
        "/api/tasks/bulk-assign",
        json={"ids": ids, "assigned_team_id": ungranted["id"]},
        headers=alice,
    )

    assert res.status_code == 422


def test_bulk_assign_empty_ids_is_400(client, alice):
    res = client.post(
        "/api/tasks/bulk-assign", json={"ids": [], "assigned_team_id": None}, headers=alice
    )

    assert res.status_code == 400

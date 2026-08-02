"""Project grants — `/api/projects/{id}/grants`.

Spec: .devnotes/teams/03_API.md § 3. Edge cases: E-08, E-12, E-16.

A grant is the access boundary — what makes a project reachable by anyone other
than its owner. These tests are as much about what a grant does *not* allow as
what it does.
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


def _team(client, headers, name="Alpha"):
    return client.post("/api/teams", json={"name": name}, headers=headers).json()


def _add(client, headers, team_id, username, role="member"):
    return client.post(
        f"/api/teams/{team_id}/members",
        json={"username": username, "role": role},
        headers=headers,
    )


def _grant(client, headers, project_id, team_id, role="annotator"):
    return client.post(
        f"/api/projects/{project_id}/grants",
        json={"team_id": team_id, "role": role},
        headers=headers,
    )


# --- who may grant -----------------------------------------------------------


def test_grant_requires_project_owner(client, alice, bob):
    project_id = _project(client, alice)
    team = _team(client, alice)

    assert _grant(client, alice, project_id, team["id"]).status_code == 200
    # Bob has no role at all: 404, indistinguishable from a nonexistent project.
    assert _grant(client, bob, project_id, team["id"]).status_code == 404


def test_manager_cannot_grant(client, alice, bob):
    """Granting is owner-only, not manager.

    A project manager who could grant could grant their own team `manager`
    elsewhere, or add teams the owner never intended. Privilege-granting must
    not be delegable without an explicit ownership transfer (§ 3).
    """
    project_id = _project(client, alice)
    team = _team(client, alice)
    _add(client, alice, team["id"], _me(client, bob)["username"])
    _grant(client, alice, project_id, team["id"], role="manager")

    res = _grant(client, bob, project_id, team["id"], role="manager")

    assert res.status_code == 403


def test_grant_to_team_you_are_not_in_404(client, alice, bob):
    """E-16: otherwise a project owner could probe which team ids exist by
    watching this endpoint's status codes."""
    project_id = _project(client, alice)
    bobs_team = _team(client, bob, "Bobs Team")

    res = _grant(client, alice, project_id, bobs_team["id"])

    assert res.status_code == 404
    assert res.json()["detail"] == "Team not found"


def test_grant_role_cannot_be_owner(client, alice):
    """Ownership is `Project.owner_id`, full stop. A grant that could say
    "owner" would give a project two owners with no tiebreak."""
    project_id = _project(client, alice)
    team = _team(client, alice)

    res = _grant(client, alice, project_id, team["id"], role="owner")

    assert res.status_code == 422


# --- upsert ------------------------------------------------------------------


def test_regrant_updates_role_not_duplicates(client, alice):
    """One row per (project, team). A second row would keep a revoked higher
    role alive through the resolver's max-over-grants (02_SCHEMA.md § 4)."""
    project_id = _project(client, alice)
    team = _team(client, alice)

    _grant(client, alice, project_id, team["id"], role="manager")
    _grant(client, alice, project_id, team["id"], role="viewer")

    listed = client.get(f"/api/projects/{project_id}/grants", headers=alice).json()

    assert len(listed) == 1
    assert listed[0]["role"] == "viewer"

    db = SessionLocal()
    try:
        rows = (
            db.query(models.ProjectGrant)
            .filter(
                models.ProjectGrant.project_id == project_id,
                models.ProjectGrant.team_id == team["id"],
            )
            .count()
        )
        assert rows == 1
    finally:
        db.close()


def test_patch_grant_changes_role(client, alice):
    project_id = _project(client, alice)
    team = _team(client, alice)
    _grant(client, alice, project_id, team["id"], role="viewer")

    res = client.patch(
        f"/api/projects/{project_id}/grants/{team['id']}",
        json={"role": "reviewer"},
        headers=alice,
    )

    assert res.status_code == 200
    assert res.json()["role"] == "reviewer"


# --- what a grant actually confers -------------------------------------------


def test_grant_makes_the_project_visible(client, alice, bob):
    project_id = _project(client, alice)
    team = _team(client, alice)
    _add(client, alice, team["id"], _me(client, bob)["username"])

    before = client.get(f"/api/projects/{project_id}", headers=bob).status_code
    _grant(client, alice, project_id, team["id"], role="annotator")
    after = client.get(f"/api/projects/{project_id}", headers=bob)

    assert before == 404
    assert after.status_code == 200
    assert after.json()["my_role"] == "annotator"
    assert after.json()["is_owner"] is False


def test_granted_project_appears_in_the_list(client, alice, bob):
    project_id = _project(client, alice)
    team = _team(client, alice)
    _add(client, alice, team["id"], _me(client, bob)["username"])
    _grant(client, alice, project_id, team["id"], role="viewer")

    listed = client.get("/api/projects", headers=bob).json()

    ids = {p["id"]: p for p in listed}
    assert project_id in ids
    assert ids[project_id]["my_role"] == "viewer"
    assert ids[project_id]["is_owner"] is False


def test_viewer_cannot_write(client, alice, bob):
    """The whole point of the viewer role: read without write."""
    project_id = _project(client, alice)
    team = _team(client, alice)
    _add(client, alice, team["id"], _me(client, bob)["username"])
    _grant(client, alice, project_id, team["id"], role="viewer")
    task_id = client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": "a.jpg", "status": "New"},
        headers=alice,
    ).json()["id"]

    readable = client.get(f"/api/tasks/{task_id}", headers=bob)
    writable = client.post(
        "/api/tasks", json={"id": task_id, "status": "In Progress"}, headers=bob
    )

    assert readable.status_code == 200
    assert writable.status_code == 403


def test_owner_not_demoted_by_a_lower_grant(client, alice):
    """E-13 through the API: the owner short-circuit runs first."""
    project_id = _project(client, alice)
    team = _team(client, alice)
    _grant(client, alice, project_id, team["id"], role="viewer")

    res = client.get(f"/api/projects/{project_id}", headers=alice)

    assert res.json()["my_role"] == "owner"
    assert res.json()["is_owner"] is True


# --- revoke ------------------------------------------------------------------


def test_revoke_removes_access(client, alice, bob):
    project_id = _project(client, alice)
    team = _team(client, alice)
    _add(client, alice, team["id"], _me(client, bob)["username"])
    _grant(client, alice, project_id, team["id"], role="annotator")

    assert client.get(f"/api/projects/{project_id}", headers=bob).status_code == 200

    revoked = client.delete(
        f"/api/projects/{project_id}/grants/{team['id']}", headers=alice
    )

    assert revoked.status_code == 200
    # Bites on the very next request: the resolver has no cross-request cache.
    assert client.get(f"/api/projects/{project_id}", headers=bob).status_code == 404


def test_revoke_nulls_task_assignment(client, alice):
    """E-08: leaving tasks assigned to a team that can no longer see the project
    is invisible work. The tasks themselves are never touched."""
    project_id = _project(client, alice)
    team = _team(client, alice)
    _grant(client, alice, project_id, team["id"], role="annotator")
    task_id = client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": "a.jpg", "status": "New"},
        headers=alice,
    ).json()["id"]
    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"]},
        headers=alice,
    )

    res = client.delete(
        f"/api/projects/{project_id}/grants/{team['id']}", headers=alice
    )

    assert res.json()["tasks_unassigned"] == 1

    db = SessionLocal()
    try:
        task = db.get(models.Task, task_id)
        assert task is not None, "revoking a grant must never delete tasks"
        assert task.assigned_team_id is None
    finally:
        db.close()


def test_revoke_unknown_grant_404(client, alice):
    project_id = _project(client, alice)
    team = _team(client, alice)

    res = client.delete(
        f"/api/projects/{project_id}/grants/{team['id']}", headers=alice
    )

    assert res.status_code == 404


def test_delete_project_removes_grants(client, alice):
    """E-12: explicitly, not via ondelete=CASCADE, which SQLite only honours
    with PRAGMA foreign_keys=ON."""
    project_id = _project(client, alice)
    team = _team(client, alice)
    _grant(client, alice, project_id, team["id"])

    assert client.delete(f"/api/projects/{project_id}", headers=alice).status_code == 200

    db = SessionLocal()
    try:
        remaining = (
            db.query(models.ProjectGrant)
            .filter(models.ProjectGrant.project_id == project_id)
            .count()
        )
        assert remaining == 0
    finally:
        db.close()


def test_list_grants_visible_to_any_member(client, alice, bob):
    """People working on a project can see who else is on it."""
    project_id = _project(client, alice)
    team = _team(client, alice)
    _add(client, alice, team["id"], _me(client, bob)["username"])
    _grant(client, alice, project_id, team["id"], role="viewer")

    res = client.get(f"/api/projects/{project_id}/grants", headers=bob)

    assert res.status_code == 200
    assert res.json()[0]["team_id"] == team["id"]


def test_effective_role_is_max_across_teams(client, alice, bob):
    """E-07 end to end: joining a second team must not reduce access."""
    project_id = _project(client, alice)
    low = _team(client, alice, "Low")
    high = _team(client, alice, "High")
    bob_name = _me(client, bob)["username"]
    _add(client, alice, low["id"], bob_name)
    _add(client, alice, high["id"], bob_name)
    _grant(client, alice, project_id, low["id"], role="annotator")
    _grant(client, alice, project_id, high["id"], role="reviewer")

    res = client.get(f"/api/projects/{project_id}", headers=bob)

    assert res.json()["my_role"] == "reviewer"

"""Class (label) writes vs. task assignment.

Reported from the LAN deployment. An annotator who is NOT assigned a task opens
it, uses the objects sidepanel's "Edit object class" pencil, types a new class
name and picks a colour. Their annotation write is correctly refused (the save
indicator sticks on "Retrying one unsaved change"), but the *class* they typed
is created project-wide anyway. When the assigned annotator later opens the
task, the annotation that used to belong to a real class renders as the default
"Object".

What these tests establish: **the server is already correct.** For an
unassigned annotator BOTH writes are refused — `POST /api/labels` by its
MANAGER minimum, and `POST /api/tasks` by the assignment check. Nothing
reaches the database.

The reported flaw is therefore entirely client-side, and is a *rendering and
local-state* bug rather than an authorization hole:

  * `ensureLabel()` (frontend/js/components/workspace.js) pushes the new class
    into `state.labels` **before** issuing the request, and its `.catch()` only
    logs. So the 403 never removes it — the editing user keeps seeing a class
    that does not exist on the server, for the rest of the session.
  * The annotation's `labelId` is repointed at that local-only class id, and
    that mutation also survives the refused save.
  * `labelById()` (frontend/js/state.js) falls back to `{name: "object"}` for
    an unknown id. The *assigned* user, whose `state.labels` never contained
    the phantom class, therefore renders any annotation pointing at it as
    "Object".

The last point is the only way the assigned user is affected, and it requires
the annotation edit to have reached the server by some other path. These tests
bound the server contract so the client fix can be reasoned about safely.
"""
import json

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


def _labels(client, headers, project_id):
    return client.get(f"/api/labels?projectId={project_id}", headers=headers).json()


def test_annotator_cannot_create_a_class(client, alice, bob):
    """The label endpoint requires MANAGER, so an annotator is refused.

    This is the half that already works: the sidepanel's ensureLabel() call is
    rejected for an annotator, so no stray class is created.
    """
    project_id = _project(client, alice)
    _team_with_grant(client, alice, project_id, "Alpha", members=[bob])

    res = client.post("/api/labels", json={
        "id": "lbl-annotator-attempt", "name": "sneaky", "color": "#ff0000",
        "projectId": project_id,
    }, headers=bob)
    assert res.status_code == 403

    names = [l["name"] for l in _labels(client, alice, project_id)]
    assert "sneaky" not in names


def test_annotator_cannot_write_a_task_assigned_to_someone_else(client, alice, bob, carol):
    """The task write IS refused for an unassigned annotator.

    Reviewers and above are deliberately never partitioned by assignment
    (api/permissions.py can_write_task), so the annotator is the role that
    actually gets blocked — and therefore the role in the reported scenario.
    """
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Annotators", members=[bob, carol])
    task_id = _task(client, alice, project_id)

    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"], "assignee_user_id": _me(client, carol)["id"]},
        headers=alice,
    )

    task = client.get(f"/api/tasks/{task_id}", headers=bob).json()
    res = client.post("/api/tasks", json={
        "id": task_id, "annotations": json.dumps([{"id": "a1", "labelId": "lbl-x"}]),
        "updated_at": task["updated_at"], "client_id": "tab-B",
    }, headers=bob)
    assert res.status_code == 403
    assert "assigned to" in res.json()["detail"]


def test_manager_creates_a_class_while_barred_from_the_task(client, alice, bob, carol):
    """THE BUG: the class write succeeds while the annotation write is refused.

    A manager passes the label endpoint's MANAGER minimum, so the class they
    typed into the sidepanel is created project-wide. The same edit's task save
    is a separate request — and here it is allowed too, because a manager is
    never partitioned by assignment. That combination is fine.

    A manager clears both bars, so nothing is inconsistent for them.
    """
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Annotators", members=[carol])
    _team_with_grant(client, alice, project_id, "Managers", role="manager", members=[bob])
    task_id = _task(client, alice, project_id)

    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"], "assignee_user_id": _me(client, carol)["id"]},
        headers=alice,
    )

    res = client.post("/api/labels", json={
        "id": "lbl-mgr", "name": "manager-class", "color": "#00ff00",
        "projectId": project_id,
    }, headers=bob)
    assert res.status_code == 200

    names = [l["name"] for l in _labels(client, alice, project_id)]
    assert "manager-class" in names


def test_both_writes_refused_for_unassigned_annotator(client, alice, bob, carol):
    """THE FIX'S CONTRACT, annotator case.

    The annotator is the role the report describes. Both halves of the
    sidepanel edit must be refused: the class must not be created, and the
    annotation must not be written. If only the first were refused the user
    would see "Retrying one unsaved change" while a stray class appeared for
    everyone — which is the reported symptom.
    """
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Annotators", members=[bob, carol])
    task_id = _task(client, alice, project_id)

    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"], "assignee_user_id": _me(client, carol)["id"]},
        headers=alice,
    )

    label_res = client.post("/api/labels", json={
        "id": "lbl-unassigned", "name": "unassigned-class", "color": "#0000ff",
        "projectId": project_id,
    }, headers=bob)
    assert label_res.status_code == 403

    task = client.get(f"/api/tasks/{task_id}", headers=bob).json()
    task_res = client.post("/api/tasks", json={
        "id": task_id, "annotations": json.dumps([{"id": "a1", "labelId": "lbl-unassigned"}]),
        "updated_at": task["updated_at"], "client_id": "tab-B",
    }, headers=bob)
    assert task_res.status_code == 403

    names = [l["name"] for l in _labels(client, alice, project_id)]
    assert "unassigned-class" not in names


def test_reviewer_may_write_any_task_by_design(client, alice, bob, carol):
    """Reviewers are intentionally exempt from assignment partitioning.

    Pinned so the exemption is a recorded decision rather than an accident: a
    reviewer cannot give meaningful feedback on work they cannot interact with
    (api/permissions.py). This is why the reported flaw needs the *annotator*
    path, not the reviewer path.
    """
    project_id = _project(client, alice)
    _team_with_grant(client, alice, project_id, "Reviewers", role="reviewer", members=[bob])
    team = _team_with_grant(client, alice, project_id, "Annotators", members=[carol])
    task_id = _task(client, alice, project_id)

    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"], "assignee_user_id": _me(client, carol)["id"]},
        headers=alice,
    )

    task = client.get(f"/api/tasks/{task_id}", headers=bob).json()
    res = client.post("/api/tasks", json={
        "id": task_id, "annotations": json.dumps([{"id": "a1"}]),
        "updated_at": task["updated_at"], "client_id": "tab-B",
    }, headers=bob)
    assert res.status_code == 200

    # But a reviewer is still below MANAGER, so they cannot mint a class.
    assert client.post("/api/labels", json={
        "id": "lbl-rev", "name": "reviewer-class", "color": "#0000ff",
        "projectId": project_id,
    }, headers=bob).status_code == 403


def test_owner_creates_class_while_task_assigned_elsewhere(client, alice, carol):
    """The owner case from the report.

    The owner is never partitioned by assignment, so both writes succeed for
    them. This is intended behaviour and is included to bound the bug: the
    reported "class not created" symptom cannot come from an owner acting on
    their own project.
    """
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Annotators", members=[carol])
    task_id = _task(client, alice, project_id)

    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"], "assignee_user_id": _me(client, carol)["id"]},
        headers=alice,
    )

    assert client.post("/api/labels", json={
        "id": "lbl-owner", "name": "owner-class", "color": "#123456",
        "projectId": project_id,
    }, headers=alice).status_code == 200

    task = client.get(f"/api/tasks/{task_id}", headers=alice).json()
    res = client.post("/api/tasks", json={
        "id": task_id, "annotations": json.dumps([{"id": "a1", "labelId": "lbl-owner"}]),
        "updated_at": task["updated_at"], "client_id": "tab-A",
    }, headers=alice)
    assert res.status_code == 200

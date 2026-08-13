"""Task assignment and `restrict_to_assigned_team`.

Spec: .devnotes/teams/03_API.md § 4.3, 01_DESIGN.md §§ 3.2–3.4.
Edge cases: E-09, E-10, E-24.

Two separate things are under test and they are deliberately different:
**project grants** decide what you can reach at all, while **task assignment**
distributes work inside a project both teams already reach.

Assignment restricts writes at two different strengths:

- **Team assignment** is opt-in per project (`restrict_to_assigned_team`,
  default off), so migrating changes nothing.
- **Individual assignment is always enforced** — naming a person is a stricter,
  per-task statement that does not wait for a project flag. This reverses
  01_DESIGN.md § 3.4's original "advisory" decision; see PLAN.md § 8.

In both cases a project manager or owner can still write, so a handover is a
reassignment rather than a lockout.
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
    """E-10: assigning someone outside the team is a warning, not a rejection.

    Note this is about *who may be named*, which is still permissive — a
    reviewer from another team taking one task is legitimate. What the task
    then permits is a separate question, and since the deviation recorded in
    PLAN.md § 8 that part **is** enforced: see the tests at the bottom of this
    file.
    """
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


def test_unassigned_task_not_writable_by_annotator(client, alice, bob):
    """Superseded 2026-08-03 (PLAN.md § 8, `95234a8`): a task with no team and
    no individual assignee is no longer the shared pool. An annotate-level
    member gets a 403 until a manager/owner hands them (or their team) the
    task."""
    project_id = _project(client, alice)
    _team_with_grant(client, alice, project_id, "Alpha", members=[bob])
    task_id = _task(client, alice, project_id)
    _set_restrict(project_id, True)

    res = client.post(
        "/api/tasks", json={"id": task_id, "status": "In Progress"}, headers=bob
    )

    assert res.status_code == 403


def test_restrict_flag_no_longer_gates_team_enforcement(client, alice, bob):
    """Superseded 2026-08-03 (PLAN.md § 8, `95234a8`): team-assignment
    enforcement is unconditional now, so leaving `restrict_to_assigned_team`
    at its default `False` does not make assignment advisory anymore. bob is
    in "Beta", the task is assigned to "Alpha" — he must be refused."""
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

    assert res.status_code == 403


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


# --- individual assignment is enforced --------------------------------------
#
# 01_DESIGN.md § 3.4 originally made individual assignment advisory. That was
# reconsidered (PLAN.md § 8): the point of handing one annotator a task is that
# the others leave it alone, and a filter they can switch off does not deliver
# it. These tests pin the new rule.


def test_assigned_user_can_write_their_own_task(client, alice, bob):
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Alpha", members=[bob])
    task_id = _task(client, alice, project_id)
    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"], "assignee_user_id": _me(client, bob)["id"]},
        headers=alice,
    )

    res = client.post(
        "/api/tasks", json={"id": task_id, "status": "In Progress"}, headers=bob
    )

    assert res.status_code == 200


def test_other_annotator_blocked_from_an_assigned_task(client, alice, bob, carol):
    """The whole point: someone else's task is off-limits."""
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Alpha", members=[bob, carol])
    task_id = _task(client, alice, project_id)
    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"], "assignee_user_id": _me(client, bob)["id"]},
        headers=alice,
    )

    res = client.post(
        "/api/tasks", json={"id": task_id, "status": "In Progress"}, headers=carol
    )

    assert res.status_code == 403
    # The message names who holds it, so the reader knows who to ask.
    assert _me(client, bob)["username"] in res.json()["detail"]


def test_enforcement_needs_no_restrict_flag(client, alice, bob, carol):
    """Naming an individual stands on its own.

    `restrict_to_assigned_team` governs *team* partitioning; a per-person
    assignment is a stricter, per-task statement and does not wait for the
    project to opt in.
    """
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Alpha", members=[bob, carol])
    task_id = _task(client, alice, project_id)
    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"], "assignee_user_id": _me(client, bob)["id"]},
        headers=alice,
    )
    _set_restrict(project_id, False)

    res = client.post(
        "/api/tasks", json={"id": task_id, "status": "In Progress"}, headers=carol
    )

    assert res.status_code == 403


def test_manager_can_always_write_an_assigned_task(client, alice, bob):
    """The handover escape hatch: a sick day must not need a schema change."""
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Alpha", members=[bob])
    task_id = _task(client, alice, project_id)
    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"], "assignee_user_id": _me(client, bob)["id"]},
        headers=alice,
    )

    # alice is the project owner and was never the assignee.
    res = client.post(
        "/api/tasks", json={"id": task_id, "status": "In Progress"}, headers=alice
    )

    assert res.status_code == 200


def test_unassigned_task_stays_open_to_the_team(client, alice, bob):
    """Clearing the assignee returns the task to the team pool."""
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Alpha", members=[bob])
    task_id = _task(client, alice, project_id)
    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"], "assignee_user_id": None},
        headers=alice,
    )

    res = client.post(
        "/api/tasks", json={"id": task_id, "status": "In Progress"}, headers=bob
    )

    assert res.status_code == 200


# --- the task list carries the assignment fields ----------------------------


def test_task_list_returns_assignment_fields(client, alice, bob):
    """Regression: both fields were missing from GET /api/tasks entirely, so the
    Tasks view's Team column could never render anything but "Unassigned"."""
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Alpha", members=[bob])
    task_id = _task(client, alice, project_id)
    bob_id = _me(client, bob)["id"]
    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"], "assignee_user_id": bob_id},
        headers=alice,
    )

    listed = client.get(f"/api/tasks?projectId={project_id}", headers=alice).json()
    row = next(t for t in listed if t["id"] == task_id)

    assert row["assigned_team_id"] == team["id"]
    assert row["assignee_user_id"] == bob_id

    # The annotation-free variant the gallery uses must carry them too.
    lean = client.get(
        f"/api/tasks?projectId={project_id}&include_annotations=false", headers=alice
    ).json()
    lean_row = next(t for t in lean if t["id"] == task_id)
    assert lean_row["assigned_team_id"] == team["id"]
    assert lean_row["assignee_user_id"] == bob_id


# --- assignable members -----------------------------------------------------


def test_assignable_members_lists_granted_teams_members(client, alice, bob):
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Alpha", members=[bob])

    res = client.get(f"/api/projects/{project_id}/assignable-members", headers=alice)

    assert res.status_code == 200
    names = {m["username"] for m in res.json()}
    assert _me(client, bob)["username"] in names
    assert all(m["team_id"] == team["id"] for m in res.json())


def test_assignable_members_excludes_ungranted_teams(client, alice, bob, carol):
    """Only teams with a grant contribute — otherwise this would leak the
    roster of every team the caller happens to own."""
    project_id = _project(client, alice)
    _team_with_grant(client, alice, project_id, "Granted", members=[bob])
    ungranted = client.post("/api/teams", json={"name": "Ungranted"}, headers=alice).json()
    client.post(
        f"/api/teams/{ungranted['id']}/members",
        json={"username": _me(client, carol)["username"], "role": "member"},
        headers=alice,
    )

    names = {m["username"] for m in client.get(
        f"/api/projects/{project_id}/assignable-members", headers=alice
    ).json()}

    assert _me(client, bob)["username"] in names
    assert _me(client, carol)["username"] not in names


def test_assignable_members_deduplicates_across_teams(client, alice, bob):
    """Someone in two granted teams is one person, listed once."""
    project_id = _project(client, alice)
    _team_with_grant(client, alice, project_id, "First", members=[bob])
    _team_with_grant(client, alice, project_id, "Second", members=[bob])

    rows = client.get(
        f"/api/projects/{project_id}/assignable-members", headers=alice
    ).json()

    bob_name = _me(client, bob)["username"]
    assert len([m for m in rows if m["username"] == bob_name]) == 1


def test_assignable_members_requires_project_access(client, alice, bob):
    project_id = _project(client, alice)

    res = client.get(f"/api/projects/{project_id}/assignable-members", headers=bob)

    assert res.status_code == 404


# --- assignee filter: multi-id and the no-match sentinel ---------------------
#
# `GET /api/tasks?assignee=` backs both the dropdown (one id, or "mine" /
# "unassigned") and the assignee *name* search, which resolves the typed name
# against the roster on the client and sends the resulting ids. See
# frontend/js/pages/project/assignee-search.js.


def _assign(client, owner, task_id, team_id, user_id=None):
    payload = {"assigned_team_id": team_id}
    if user_id is not None:
        payload["assignee_user_id"] = user_id
    return client.patch(
        f"/api/tasks/{task_id}/assignment", json=payload, headers=owner
    )


def _named_task(client, owner, project_id, name):
    return client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": name, "status": "New"},
        headers=owner,
    ).json()["id"]


def _ids(client, headers, project_id, **params):
    qs = "".join(f"&{k}={v}" for k, v in params.items())
    body = client.get(
        f"/api/tasks?projectId={project_id}&include_annotations=false{qs}",
        headers=headers,
    ).json()
    items = body["items"] if isinstance(body, dict) else body
    return {t["id"] for t in items}


def test_assignee_filter_accepts_multiple_ids(client, alice, bob, carol):
    """The name search sends every id whose username matched, so the filter
    must return their union — not just the first."""
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Alpha", members=[bob, carol])
    bob_id, carol_id = _me(client, bob)["id"], _me(client, carol)["id"]

    bob_task = _named_task(client, alice, project_id, "b.jpg")
    carol_task = _named_task(client, alice, project_id, "c.jpg")
    other_task = _named_task(client, alice, project_id, "d.jpg")
    _assign(client, alice, bob_task, team["id"], bob_id)
    _assign(client, alice, carol_task, team["id"], carol_id)

    both = _ids(client, alice, project_id, assignee=f"user-{bob_id},user-{carol_id}")

    assert both == {bob_task, carol_task}
    assert other_task not in both


def test_assignee_filter_single_id_still_works(client, alice, bob):
    """The dropdown's own one-id value takes the same path and is unchanged."""
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Alpha", members=[bob])
    bob_id = _me(client, bob)["id"]

    mine = _named_task(client, alice, project_id, "b.jpg")
    other = _named_task(client, alice, project_id, "d.jpg")
    _assign(client, alice, mine, team["id"], bob_id)

    found = _ids(client, alice, project_id, assignee=f"user-{bob_id}")

    assert found == {mine}
    assert other not in found


def test_assignee_none_sentinel_returns_nothing(client, alice, bob):
    """A name matching nobody must yield an empty page. Dropping the filter
    instead would show every task — the opposite of what was asked for."""
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Alpha", members=[bob])
    task_id = _named_task(client, alice, project_id, "b.jpg")
    _assign(client, alice, task_id, team["id"], _me(client, bob)["id"])

    assert _ids(client, alice, project_id, assignee="none") == set()
    # Guard the premise: without the filter the task is plainly there.
    assert _ids(client, alice, project_id) == {task_id}


def test_assignee_filter_composes_with_status_and_sort(client, alice, bob, carol):
    """Search, the status select and the sort all narrow one query; a filter
    that only worked alone would be useless in the real toolbar."""
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Alpha", members=[bob, carol])
    bob_id, carol_id = _me(client, bob)["id"], _me(client, carol)["id"]

    keep = _named_task(client, alice, project_id, "b.jpg")
    wrong_status = _named_task(client, alice, project_id, "c.jpg")
    _assign(client, alice, keep, team["id"], bob_id)
    _assign(client, alice, wrong_status, team["id"], carol_id)
    client.patch(
        f"/api/tasks/{wrong_status}",
        json={"status": "Done", "client_id": "t", "updated_at": None},
        headers=alice,
    )

    both = f"user-{bob_id},user-{carol_id}"
    found = _ids(
        client, alice, project_id, assignee=both, status="New", sort="description",
        order="desc",
    )

    assert found == {keep}


def test_assignee_filter_rejects_a_bad_id_in_the_list(client, alice):
    project_id = _project(client, alice)

    res = client.get(
        f"/api/tasks?projectId={project_id}&assignee=user-1,notanumber", headers=alice
    )

    assert res.status_code == 422


def test_assignee_filter_rejects_an_overlong_id_list(client, alice):
    """A hand-crafted URL must not be able to build an unbounded IN clause."""
    project_id = _project(client, alice)
    too_many = ",".join(f"user-{i}" for i in range(201))

    res = client.get(
        f"/api/tasks?projectId={project_id}&assignee={too_many}", headers=alice
    )

    assert res.status_code == 422


def test_assignee_mine_and_unassigned_unaffected(client, alice, bob):
    """The sentinels the dropdown already emitted keep their meaning."""
    project_id = _project(client, alice)
    team = _team_with_grant(client, alice, project_id, "Alpha", members=[bob])
    assigned = _named_task(client, alice, project_id, "b.jpg")
    free = _named_task(client, alice, project_id, "d.jpg")
    _assign(client, alice, assigned, team["id"], _me(client, alice)["id"])

    assert _ids(client, alice, project_id, assignee="mine") == {assigned}
    assert _ids(client, alice, project_id, assignee="unassigned") == {free}

import pytest

def _new_project(client, auth):
    res = client.post("/api/projects", json={"name": "test", "slug": "test", "creator": "ignored"}, headers=auth)
    return res.json()["id"]

def _new_task(client, auth, pid, assignee=None):
    res = client.post(
        "/api/tasks",
        params={"projectId": pid},
        json={"description": "a.png"},
        headers=auth,
    )
    tid = res.json()["id"]
    if assignee:
        client.patch(f"/api/tasks/{tid}?projectId={pid}", json={"assignee": assignee}, headers=auth)
    return tid

def test_annotator_can_read_any_task_in_an_accessible_project(client, alice, bob):
    """Reading a teammate's task is allowed; only *editing* is restricted.

    This test previously asserted 403 on GET /api/tasks/{id} for a task
    assigned to someone else. That expectation predates the per-task
    annotation loading model: the gallery list is fetched annotation-free and
    the workspace hydrates each task on open via this endpoint (CLAUDE.md rule
    17), so `get_task` deliberately passes `require_edit=False` to
    `_get_owned_task`. Restoring a read-side 403 would break opening any task
    an annotator is not personally assigned.

    The write-side guard is unchanged and still enforced: `require_edit`
    defaults to True, so save/patch/delete of a task assigned to another
    annotator still raises 403 (see _get_owned_task).
    """
    pid = _new_project(client, alice)
    alice_annotator = {**alice, "X-Annotator-Name": "Alice"}
    bob_annotator = {**alice, "X-Annotator-Name": "Bob"}

    client.post("/api/team/ping", headers=alice_annotator)
    client.post("/api/team/ping", headers=bob_annotator)

    t1 = _new_task(client, alice, pid, "Alice")
    t2 = _new_task(client, alice, pid, "Bob")
    t3 = _new_task(client, alice, pid, None)

    # Alice (admin) can access everything
    assert client.get(f"/api/tasks/{t1}", headers=alice).status_code == 200
    assert client.get(f"/api/tasks/{t2}", headers=alice).status_code == 200
    assert client.get(f"/api/tasks/{t3}", headers=alice).status_code == 200

    # Alice (annotator) can access her task and unassigned
    assert client.get(f"/api/tasks/{t1}", headers=alice_annotator).status_code == 200
    assert client.get(f"/api/tasks/{t3}", headers=alice_annotator).status_code == 200

    # ...and a teammate's task, read-only.
    assert client.get(f"/api/tasks/{t2}", headers=alice_annotator).status_code == 200
    assert client.get(f"/api/tasks/{t1}", headers=bob_annotator).status_code == 200

    # Both see every task in the list.
    tasks_alice = client.get(f"/api/tasks?projectId={pid}", headers=alice_annotator).json()["items"]
    assert {t["id"] for t in tasks_alice} == {t1, t2, t3}

    tasks_bob = client.get(f"/api/tasks?projectId={pid}", headers=bob_annotator).json()["items"]
    assert {t["id"] for t in tasks_bob} == {t1, t2, t3}




def test_assignee_name_resolves_across_case_and_whitespace(client, alice, bob):
    """A typed annotator name must resolve to its TeamMember despite case/space.

    The annotator identity is a display name the user types into Settings and
    the browser forwards as X-Annotator-Name; the authority checks downstream
    are exact string compares against tasks.assignee. An assignee who typed
    "sanjita", or left a trailing space, for a task assigned to "Sanjita" was
    refused on their own task, while the project owner -- authorized on
    users.id rather than on a name -- could still save it. Both variants exist
    in the real team_members table.

    The save is made by `bob`, a *different account* from the project owner:
    _is_task_editor admits an owner regardless of the assignee check, so an
    owner-authenticated save passes even with the bug present and proves
    nothing. bob reaches the project through his annotator name alone.
    """
    pid = _new_project(client, {**alice, "X-Annotator-Name": "Owner"})
    client.post("/api/team/ping", headers={**alice, "X-Annotator-Name": "Owner"})
    client.post("/api/team/ping", headers={**bob, "X-Annotator-Name": "Realuser"})
    tid = _new_task(client, {**alice, "X-Annotator-Name": "Owner"}, pid, assignee="Realuser")

    for typed in ("Realuser", "realuser", "  Realuser  ", "REALUSER"):
        res = client.post(
            "/api/tasks",
            params={"projectId": pid},
            json={"id": tid, "annotations": "[]", "client_id": "c1"},
            headers={**bob, "X-Annotator-Name": typed},
        )
        assert res.status_code == 200, (
            f"assignee typing {typed!r} was locked out of their own task "
            f"({res.status_code})"
        )


def test_unresolvable_annotator_cannot_edit_assigned_task(client, alice, bob):
    """An unrecognized annotator name must never gain edit rights on a task.

    _get_owned_task used to spell its guard `annotator and task.assignee and
    ...`, so a caller whose X-Annotator-Name matched no TeamMember row skipped
    the assignee check entirely. Project access is name-derived too, so such a
    caller is already turned away with 404 by the accessible-projects filter;
    this pins that belt-and-braces behaviour so a future change to project
    visibility cannot quietly turn the skipped check into an edit path.
    """
    pid = _new_project(client, {**alice, "X-Annotator-Name": "Owner"})
    client.post("/api/team/ping", headers={**alice, "X-Annotator-Name": "Owner"})
    client.post("/api/team/ping", headers={**bob, "X-Annotator-Name": "Realuser"})
    tid = _new_task(client, {**alice, "X-Annotator-Name": "Owner"}, pid, assignee="Realuser")

    res = client.post(
        "/api/tasks",
        params={"projectId": pid},
        json={"id": tid, "annotations": "[]", "client_id": "c1"},
        headers={**bob, "X-Annotator-Name": "no-such-member-xyz"},
    )
    assert res.status_code in (403, 404), (
        "an unresolvable annotator name must not be able to edit an assigned task"
    )

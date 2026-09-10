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

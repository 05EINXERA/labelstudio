"""Cross-user isolation for project-scoped resources (tracker P0.6).

Before Phase 0, `creator` was a client-supplied string and every project-scoped
route trusted it, so any authenticated user could read or mutate any project's
tasks and labels by guessing an id. These tests pin the fix.

404 (not 403) is asserted deliberately: the API must not confirm that another
user's project id exists.
"""
import pytest


def _new_project(client, auth, name="proj"):
    res = client.post("/api/projects", json={"name": name, "slug": name, "creator": "ignored"}, headers=auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


# --- Projects -------------------------------------------------------------

def test_project_list_is_scoped_to_owner(client, alice, bob):
    _new_project(client, alice, "alice-only")
    listed = client.get("/api/projects", headers=bob).json()
    assert all(p["name"] != "alice-only" for p in listed)


def test_creator_query_param_cannot_widen_scope(client, alice, bob):
    """The old `?creator=` escape hatch must no longer grant access."""
    _new_project(client, alice, "alice-secret")
    listed = client.get("/api/projects?creator=alice", headers=bob).json()
    assert all(p["name"] != "alice-secret" for p in listed)


def test_owner_is_taken_from_token_not_body(client, alice, bob):
    """A forged `creator` in the body must not assign the project to someone else."""
    pid = _new_project(client, bob, "bobs")
    assert client.get(f"/api/projects/{pid}", headers=alice).status_code == 404
    assert client.get(f"/api/projects/{pid}", headers=bob).status_code == 200


@pytest.mark.parametrize("method,suffix", [
    ("get", ""),
    ("get", "/metrics"),
    ("delete", ""),
])
def test_project_routes_404_for_non_owner(client, alice, bob, method, suffix):
    pid = _new_project(client, alice)
    res = getattr(client, method)(f"/api/projects/{pid}{suffix}", headers=bob)
    assert res.status_code == 404


def test_patch_project_404_for_non_owner(client, alice, bob):
    pid = _new_project(client, alice)
    res = client.patch(f"/api/projects/{pid}", json={"name": "hijacked"}, headers=bob)
    assert res.status_code == 404
    assert client.get(f"/api/projects/{pid}", headers=alice).json()["name"] != "hijacked"


def test_legacy_update_route_404_for_non_owner(client, alice, bob):
    pid = _new_project(client, alice)
    res = client.post("/api/projects/update", json={"id": pid, "name": "hijacked"}, headers=bob)
    assert res.status_code == 404


def test_upload_404_for_non_owner(client, alice, bob):
    pid = _new_project(client, alice)
    res = client.post(
        f"/api/projects/{pid}/upload",
        files=[("file", ("x.png", b"\x89PNG\r\n\x1a\n", "image/png"))],
        headers=bob,
    )
    assert res.status_code == 404


# --- Labels ---------------------------------------------------------------

def test_labels_404_for_non_owner(client, alice, bob):
    pid = _new_project(client, alice)
    client.post("/api/labels", json={"id": "l1", "name": "cat", "color": "#fff", "projectId": pid}, headers=alice)

    assert client.get(f"/api/labels?projectId={pid}", headers=bob).status_code == 404
    assert client.post(
        "/api/labels",
        json={"id": "l2", "name": "dog", "color": "#000", "projectId": pid},
        headers=bob,
    ).status_code == 404
    assert client.delete(f"/api/labels/l1?projectId={pid}", headers=bob).status_code == 404

    # Alice's label survived every attempt.
    assert len(client.get(f"/api/labels?projectId={pid}", headers=alice).json()) == 1


# --- Tasks ----------------------------------------------------------------

def test_tasks_list_404_for_non_owner_project(client, alice, bob):
    pid = _new_project(client, alice)
    assert client.get(f"/api/tasks?projectId={pid}", headers=bob).status_code == 404


def test_unscoped_task_list_only_returns_own_tasks(client, alice, bob):
    pid = _new_project(client, alice)
    client.post("/api/tasks", json={"description": "alice-task"}, params={"projectId": pid}, headers=alice)

    bob_tasks = client.get("/api/tasks", headers=bob).json()
    assert all(t["description"] != "alice-task" for t in bob_tasks)

    alice_tasks = client.get("/api/tasks", headers=alice).json()
    assert any(t["description"] == "alice-task" for t in alice_tasks)


def test_task_update_and_delete_404_for_non_owner(client, alice, bob):
    pid = _new_project(client, alice)
    tid = client.post("/api/tasks", json={"description": "t"}, params={"projectId": pid}, headers=alice).json()["id"]

    assert client.post("/api/tasks", json={"id": tid, "description": "hijacked"}, headers=bob).status_code == 404
    assert client.delete(f"/api/tasks/{tid}", headers=bob).status_code == 404

    still = client.get(f"/api/tasks?projectId={pid}", headers=alice).json()
    assert still[0]["description"] == "t"


def test_patch_task_updates_and_requires_ownership(client, alice, bob):
    pid = _new_project(client, alice)
    tid = client.post("/api/tasks", json={"description": "t"}, params={"projectId": pid}, headers=alice).json()["id"]

    res = client.patch(f"/api/tasks/{tid}", json={"status": "Completed"}, headers=alice)
    assert res.status_code == 200, res.text
    assert client.get(f"/api/tasks?projectId={pid}", headers=alice).json()[0]["status"] == "Completed"

    assert client.patch(f"/api/tasks/{tid}", json={"status": "New"}, headers=bob).status_code == 404


def test_approved_status_settable_by_owner(client, alice):
    """Single-owner projects: the owner is implicitly the only approver."""
    pid = _new_project(client, alice)
    tid = client.post("/api/tasks", json={"description": "t"}, params={"projectId": pid}, headers=alice).json()["id"]
    res = client.patch(f"/api/tasks/{tid}", json={"status": "Approved"}, headers=alice)
    assert res.status_code == 200
    assert client.get(f"/api/tasks?projectId={pid}", headers=alice).json()[0]["status"] == "Approved"


def test_task_creation_requires_owned_project(client, alice, bob):
    pid = _new_project(client, alice)
    res = client.post("/api/tasks", json={"description": "x"}, params={"projectId": pid}, headers=bob)
    assert res.status_code == 404


def test_task_creation_without_project_id_is_rejected(client, alice):
    assert client.post("/api/tasks", json={"description": "orphan"}, headers=alice).status_code == 422


def test_bulk_routes_skip_unowned_ids(client, alice, bob):
    """Mixed payloads must mutate only the caller's own tasks."""
    a_pid = _new_project(client, alice, "a")
    b_pid = _new_project(client, bob, "b")
    a_tid = client.post("/api/tasks", json={"description": "a"}, params={"projectId": a_pid}, headers=alice).json()["id"]
    b_tid = client.post("/api/tasks", json={"description": "b"}, params={"projectId": b_pid}, headers=bob).json()["id"]

    res = client.post("/api/tasks/bulk-update", json={"ids": [a_tid, b_tid], "status": "Completed"}, headers=bob)
    assert res.status_code == 200
    assert res.json()["skipped"] == 1

    assert client.get(f"/api/tasks?projectId={a_pid}", headers=alice).json()[0]["status"] != "Completed"

    res = client.post("/api/tasks/bulk-delete", json={"ids": [a_tid, b_tid]}, headers=bob)
    assert res.json() == {"status": "ok", "deleted": 1, "skipped": 1}
    assert len(client.get(f"/api/tasks?projectId={a_pid}", headers=alice).json()) == 1


# --- Metrics --------------------------------------------------------------

def test_metrics_batch_is_scoped(client, alice, bob):
    a_pid = _new_project(client, alice, "a")
    assert str(a_pid) not in client.get("/api/projects/metrics/batch", headers=bob).json()
    assert str(a_pid) in client.get("/api/projects/metrics/batch", headers=alice).json()


def test_project_list_embeds_metrics(client, alice):
    """P1.1: the list page must not need a second metrics request."""
    pid = _new_project(client, alice)
    client.post("/api/labels", json={"id": "k1", "name": "cat", "color": "#fff", "projectId": pid}, headers=alice)
    t1 = client.post("/api/tasks", json={"description": "a"}, params={"projectId": pid}, headers=alice).json()["id"]
    client.post("/api/tasks", json={"description": "b"}, params={"projectId": pid}, headers=alice)
    client.post("/api/tasks", json={"id": t1, "status": "Completed"}, headers=alice)

    row = next(p for p in client.get("/api/projects", headers=alice).json() if p["id"] == pid)
    assert row["total"] == 2
    assert row["completed"] == 1
    assert row["progress"] == 50
    assert row["classes"] == 1


def test_list_and_single_metrics_agree(client, alice):
    """The shared aggregator must not drift between the two endpoints."""
    pid = _new_project(client, alice)
    client.post("/api/tasks", json={"description": "a"}, params={"projectId": pid}, headers=alice)

    row = next(p for p in client.get("/api/projects", headers=alice).json() if p["id"] == pid)
    single = client.get(f"/api/projects/{pid}/metrics", headers=alice).json()
    for key in ("total", "completed", "in_progress", "progress", "comments", "classes", "total_time"):
        assert row[key] == single[key], key


def test_metrics_reports_class_count(client, alice):
    pid = _new_project(client, alice)
    client.post("/api/labels", json={"id": "c1", "name": "cat", "color": "#fff", "projectId": pid}, headers=alice)
    client.post("/api/labels", json={"id": "c2", "name": "dog", "color": "#000", "projectId": pid}, headers=alice)

    m = client.get(f"/api/projects/{pid}/metrics", headers=alice).json()
    assert m["classes"] == 2
    assert m["in_progress"] == 0

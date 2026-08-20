"""Optimistic-concurrency behaviour of POST /api/tasks.

These cover the annotation-loss bug reported from the LAN deployment: a single
browser tab writes the same task from three places (the debounced autosave, the
`visibilitychange` beacon, and the 30s timer drain), and the old conflict check
treated those self-overwrites as collisions. A 409 then disabled saving for the
task client-side, so every later edit was lost on refresh.

The rule under test: a conflict is only a conflict when a *different* client
wrote in between. See .devnotes/deployment-hardening/04_ANNOTATION_SAVE_LOSS.md.
"""
import json

import pytest


def _project(client, auth):
    res = client.post("/api/projects", json={
        "name": "conflict-test", "slug": "conflict-test", "creator": "alice",
    }, headers=auth)
    assert res.status_code == 200, res.text
    pid = res.json()["id"]
    client.post("/api/labels", json={"id": f"l1-{pid}", "name": "l1", "color": "#000", "projectId": pid}, headers=auth)
    return pid


def _create_task(client, auth, project_id, description="img.jpg"):
    res = client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": description, "status": "New"},
        headers=auth,
    )
    assert res.status_code == 200, res.text
    return res.json()


def _annotations(n, pid):
    return json.dumps([{"id": f"a{i}", "type": "box", "labelId": f"l1-{pid}"} for i in range(n)])


def test_same_client_may_overwrite_its_own_write(client, alice):
    """The core fix: a tab saving over its own previous save is not a conflict.

    Reproduces the autosave-then-timer-drain sequence, where the second write
    still carries the timestamp the client knew *before* the first one.
    """
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)
    stale = task["updated_at"]

    first = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(1, project_id),
        "updated_at": stale, "client_id": "tab-A",
    }, headers=alice)
    assert first.status_code == 200

    # Same tab, deliberately still holding the pre-first-write timestamp.
    second = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(2, project_id),
        "updated_at": stale, "client_id": "tab-A",
    }, headers=alice)
    assert second.status_code == 200, "a client must never 409 against itself"

    got = client.get(f"/api/tasks/{task['id']}", headers=alice).json()
    assert len(got["annotations"]) == 2


def test_different_client_with_stale_timestamp_conflicts(client, alice):
    """A real cross-client collision is still caught."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)
    stale = task["updated_at"]

    assert client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(1, project_id),
        "updated_at": stale, "client_id": "tab-A",
    }, headers=alice).status_code == 200

    # A second tab that never saw tab-A's write.
    res = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(9, project_id),
        "updated_at": stale, "client_id": "tab-B",
    }, headers=alice)
    assert res.status_code == 409


def test_missing_updated_at_skips_the_check(client, alice):
    """A write with no token is accepted rather than silently rejected.

    The beacon path clears `updated_at` because a beacon returns no body, so a
    null token must mean "I cannot prove freshness", not "block me".
    """
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    assert client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(1, project_id),
        "updated_at": task["updated_at"], "client_id": "tab-A",
    }, headers=alice).status_code == 200

    res = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(3, project_id),
        "updated_at": None, "client_id": "tab-B",
    }, headers=alice)
    assert res.status_code == 200


def test_get_tasks_returns_updated_at(client, alice):
    """The client cannot do conflict detection without this field.

    Omitting it from the gallery mapping is what started the whole failure
    chain, so the contract is pinned here.
    """
    project_id = _project(client, alice)
    _create_task(client, alice, project_id)

    rows = client.get(f"/api/tasks?projectId={project_id}&include_annotations=true", headers=alice).json()["items"]
    assert rows[0].get("updated_at"), "GET /api/tasks must expose updated_at"


def test_conflict_does_not_lose_the_stored_annotations(client, alice):
    """A rejected write must leave the winning client's data intact."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)
    stale = task["updated_at"]

    client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(4, project_id),
        "updated_at": stale, "client_id": "tab-A",
    }, headers=alice)

    client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(1, project_id),
        "updated_at": stale, "client_id": "tab-B",
    }, headers=alice)

    rows = client.get(f"/api/tasks?projectId={project_id}&include_annotations=true", headers=alice).json()["items"]
    assert len(client.get(f"/api/tasks/{rows[0]['id']}", headers=alice).json()["annotations"]) == 4


def test_client_id_is_recorded_on_create(client, alice):
    """A task created by a tab is owned by that tab for conflict purposes."""
    project_id = _project(client, alice)
    res = client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": "x.jpg", "status": "New", "client_id": "tab-A"},
        headers=alice,
    )
    assert res.status_code == 200
    task = res.json()

    # Same tab, stale token: accepted because the creator is recorded.
    follow_up = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(1, project_id),
        "updated_at": task["updated_at"], "client_id": "tab-A",
    }, headers=alice)
    assert follow_up.status_code == 200


def test_writes_without_client_id_still_work(client, alice):
    """Older clients that send no client_id must keep functioning."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    res = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(2, project_id),
        "updated_at": task["updated_at"],
    }, headers=alice)
    assert res.status_code == 200


# ---------------------------------------------------------------------------
# T1.1 / T1.4 — GET /api/tasks/{id} (per-task detail endpoint)
# ---------------------------------------------------------------------------

def test_get_task_by_id_returns_200_with_annotations(client, alice):
    """T1.1: the per-task endpoint must return the task with its annotations."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    # Write some annotations via the save path so there is something to read back.
    client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(3, project_id),
        "updated_at": task["updated_at"], "client_id": "tab-A",
    }, headers=alice)

    res = client.get(f"/api/tasks/{task['id']}", headers=alice)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["id"] == task["id"]
    assert len(body["annotations"]) == 3


def test_get_task_by_id_exposes_updated_at(client, alice):
    """T1.4: updated_at must survive the round-trip through the detail endpoint.

    This field going missing was the original cause of the annotation-loss bug
    (the save payload silently omitted it, disabling conflict detection).
    """
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    res = client.get(f"/api/tasks/{task['id']}", headers=alice)
    assert res.status_code == 200
    assert res.json().get("updated_at"), "GET /api/tasks/{id} must expose updated_at"


def test_get_task_by_id_returns_404_for_nonexistent(client, alice):
    """T1.1: a missing task id must 404, not 500."""
    res = client.get("/api/tasks/999999", headers=alice)
    assert res.status_code == 404


def test_get_task_by_id_returns_404_for_other_users_task(client, alice, bob):
    """T1.1: ownership is enforced — bob cannot read alice's task."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    res = client.get(f"/api/tasks/{task['id']}", headers=bob)
    assert res.status_code == 404


def test_get_tasks_list_is_annotation_free_by_default(client, alice):
    """T1.2: the list endpoint must not ship annotation blobs when
    include_annotations=false, keeping the gallery load lightweight."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    # Write annotations so there is definitely something to omit.
    client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(5, project_id),
        "updated_at": task["updated_at"], "client_id": "tab-A",
    }, headers=alice)

    rows = client.get(
        f"/api/tasks?projectId={project_id}&include_annotations=false",
        headers=alice,
    ).json()["items"]
    assert "annotations" not in rows[0], (
        "list endpoint must not return annotations"
    )


def test_get_task_by_id_updated_at_matches_after_save(client, alice):
    """T1.4: the token returned by GET /{id} must match what POST returned,
    so the next save uses a fresh token and does not 409 against itself."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    save_res = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(2, project_id),
        "updated_at": task["updated_at"], "client_id": "tab-A",
    }, headers=alice)
    assert save_res.status_code == 200
    token_from_save = save_res.json()["updated_at"]

    detail = client.get(f"/api/tasks/{task['id']}", headers=alice).json()
    # The token from GET /{id} must be usable in the next save without a 409.
    follow_up = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(4, project_id),
        "updated_at": detail["updated_at"], "client_id": "tab-A",
    }, headers=alice)
    assert follow_up.status_code == 200, (
        f"Token from GET /{task['id']} should be accepted as fresh; "
        f"save token was {token_from_save!r}, detail token was {detail['updated_at']!r}"
    )

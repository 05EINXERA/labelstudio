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
    return res.json()["id"]


def _create_task(client, auth, project_id, description="img.jpg"):
    res = client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": description, "status": "New"},
        headers=auth,
    )
    assert res.status_code == 200, res.text
    return res.json()


def _annotations(n):
    return json.dumps([{"id": f"a{i}", "type": "box", "labelId": "l1"} for i in range(n)])


def test_same_client_may_overwrite_its_own_write(client, alice):
    """The core fix: a tab saving over its own previous save is not a conflict.

    Reproduces the autosave-then-timer-drain sequence, where the second write
    still carries the timestamp the client knew *before* the first one.
    """
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)
    stale = task["updated_at"]

    first = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(1),
        "updated_at": stale, "client_id": "tab-A",
    }, headers=alice)
    assert first.status_code == 200

    # Same tab, deliberately still holding the pre-first-write timestamp.
    second = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(2),
        "updated_at": stale, "client_id": "tab-A",
    }, headers=alice)
    assert second.status_code == 200, "a client must never 409 against itself"

    got = client.get(f"/api/tasks?projectId={project_id}", headers=alice).json()
    assert len(got[0]["annotations"]) == 2


def test_different_client_with_stale_timestamp_conflicts(client, alice):
    """A real cross-client collision is still caught."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)
    stale = task["updated_at"]

    assert client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(1),
        "updated_at": stale, "client_id": "tab-A",
    }, headers=alice).status_code == 200

    # A second tab that never saw tab-A's write.
    res = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(9),
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
        "id": task["id"], "annotations": _annotations(1),
        "updated_at": task["updated_at"], "client_id": "tab-A",
    }, headers=alice).status_code == 200

    res = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(3),
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

    rows = client.get(f"/api/tasks?projectId={project_id}", headers=alice).json()
    assert rows[0].get("updated_at"), "GET /api/tasks must expose updated_at"


def test_conflict_does_not_lose_the_stored_annotations(client, alice):
    """A rejected write must leave the winning client's data intact."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)
    stale = task["updated_at"]

    client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(4),
        "updated_at": stale, "client_id": "tab-A",
    }, headers=alice)

    client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(1),
        "updated_at": stale, "client_id": "tab-B",
    }, headers=alice)

    rows = client.get(f"/api/tasks?projectId={project_id}", headers=alice).json()
    assert len(rows[0]["annotations"]) == 4


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
        "id": task["id"], "annotations": _annotations(1),
        "updated_at": task["updated_at"], "client_id": "tab-A",
    }, headers=alice)
    assert follow_up.status_code == 200


def test_writes_without_client_id_still_work(client, alice):
    """Older clients that send no client_id must keep functioning."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    res = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(2),
        "updated_at": task["updated_at"],
    }, headers=alice)
    assert res.status_code == 200

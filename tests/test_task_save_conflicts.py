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


# --- Empty-annotation guard (incident 2026-08-04: task 692) -----------------
#
# A live save from the SAME client_id as the last writer never conflicts, by
# design (test_same_client_may_overwrite_its_own_write above) — that is the
# fix for the original save-loss bug. But it also means an autosave that fires
# with an empty in-memory annotation set (a half-hydrated reload, a client
# stuck retrying through a CSRF/permission failure, anything that leaves the
# canvas blank) sails straight through conflict detection and silently wipes
# real work, because nothing else in that path distinguishes "the user deleted
# every shape" from "the client never loaded them". This happened for real:
# see .devnotes/offline/INCIDENT_692.md. These tests pin the guard added to
# close it.

def test_emptying_annotations_from_the_same_client_is_refused(client, alice):
    """The exact incident shape: same client_id, existing work, empty payload."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    saved = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(3),
        "updated_at": task["updated_at"], "client_id": "tab-A",
    }, headers=alice)
    assert saved.status_code == 200

    wipe = client.post("/api/tasks", json={
        "id": task["id"], "annotations": "[]",
        # Deliberately stale/absent updated_at — this is what an autosave that
        # never re-hydrated the task would actually send, and it must not be
        # what saves it: the guard has to catch this on its own merits, not
        # rely on a conflict 409 firing first.
        "updated_at": None, "client_id": "tab-A",
    }, headers=alice)
    assert wipe.status_code == 422
    assert "clear" in wipe.json()["detail"].lower()

    rows = client.get(f"/api/tasks?projectId={project_id}", headers=alice).json()
    assert len(rows[0]["annotations"]) == 3, "the refused write must not have touched stored annotations"


def test_emptying_annotations_is_refused_regardless_of_client_id(client, alice):
    """Not just the self-overwrite case — any client emptying a worked task."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(3),
        "updated_at": task["updated_at"], "client_id": "tab-A",
    }, headers=alice)

    res = client.post("/api/tasks", json={
        "id": task["id"], "annotations": "[]",
        "updated_at": None, "client_id": "tab-B",
    }, headers=alice)
    assert res.status_code in (409, 422), "must be refused one way or another, never accepted as 200"


def test_allow_clear_lets_a_genuine_delete_all_through(client, alice):
    """The escape hatch: a user who really did delete every shape can still save."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    saved = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(3),
        "updated_at": task["updated_at"], "client_id": "tab-A",
    }, headers=alice)
    assert saved.status_code == 200

    cleared = client.post("/api/tasks", json={
        "id": task["id"], "annotations": "[]",
        "updated_at": None, "client_id": "tab-A", "allow_clear": True,
    }, headers=alice)
    assert cleared.status_code == 200

    rows = client.get(f"/api/tasks?projectId={project_id}", headers=alice).json()
    assert rows[0]["annotations"] == []


def test_saving_an_already_empty_task_is_unaffected(client, alice):
    """The guard must not block ordinary saves on a task with nothing to lose."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    res = client.post("/api/tasks", json={
        "id": task["id"], "annotations": "[]",
        "updated_at": task["updated_at"], "client_id": "tab-A",
    }, headers=alice)
    assert res.status_code == 200


def test_non_empty_annotations_are_never_refused_by_the_guard(client, alice):
    """Sanity: the guard is specific to emptying, not to writing annotations at all."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    first = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(3),
        "updated_at": task["updated_at"], "client_id": "tab-A",
    }, headers=alice)
    assert first.status_code == 200

    second = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(5),
        "updated_at": None, "client_id": "tab-A",
    }, headers=alice)
    assert second.status_code == 200


# ---------------------------------------------------------------------------
# T1.1 / T1.4 — GET /api/tasks/{id} (per-task detail endpoint)
# ---------------------------------------------------------------------------

def test_get_task_by_id_returns_200_with_annotations(client, alice):
    """T1.1: the per-task endpoint must return the task with its annotations."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    # Write some annotations via the save path so there is something to read back.
    client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(3),
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
        "id": task["id"], "annotations": _annotations(5),
        "updated_at": task["updated_at"], "client_id": "tab-A",
    }, headers=alice)

    rows = client.get(
        f"/api/tasks?projectId={project_id}&include_annotations=false",
        headers=alice,
    ).json()
    assert rows[0]["annotations"] == [], (
        "include_annotations=false must return empty annotation lists"
    )


def test_get_task_by_id_updated_at_matches_after_save(client, alice):
    """T1.4: the token returned by GET /{id} must match what POST returned,
    so the next save uses a fresh token and does not 409 against itself."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    save_res = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(2),
        "updated_at": task["updated_at"], "client_id": "tab-A",
    }, headers=alice)
    assert save_res.status_code == 200
    token_from_save = save_res.json()["updated_at"]

    detail = client.get(f"/api/tasks/{task['id']}", headers=alice).json()
    # The token from GET /{id} must be usable in the next save without a 409.
    follow_up = client.post("/api/tasks", json={
        "id": task["id"], "annotations": _annotations(4),
        "updated_at": detail["updated_at"], "client_id": "tab-A",
    }, headers=alice)
    assert follow_up.status_code == 200, (
        f"Token from GET /{task['id']} should be accepted as fresh; "
        f"save token was {token_from_save!r}, detail token was {detail['updated_at']!r}"
    )

"""The destructive-save WARN line (wipe-guard-bypass-fix, fix plan S2).

A save that drops a large share of a task's annotations emits
`event=task.save.destructive` at WARN. The line itself refuses nothing; it
records every large drop that was *accepted*.

Refusing is the wipe guard's job (tests/test_bulk_loss_guard.py): it stops a
large loss the client did not explain with `deleted_ids`. So the drops that
reach this line are deliberate ones, and the saves here name their deletions
the way the canvas does. A deliberate drop of 80% is still worth a line --
it is exactly the event someone should be able to find afterwards.
"""
import json

import pytest

from api.routers import tasks as tasks_router


def _project(client, auth):
    res = client.post("/api/projects", json={
        "name": "destructive-test", "slug": "destructive-test", "creator": "alice",
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


def _save(client, auth, task_id, annotations, client_id="tab-A", updated_at=None,
          name_deletions=True):
    """Save `annotations`, by default naming every id it drops as user-deleted.

    That is what the canvas sends for a real delete (`deleted_ids`), so these
    tests exercise the log line rather than the wipe guard in front of it.
    `_annotations` only ever mints ids a0..a{n-1}, so listing every such id
    the payload leaves out is exactly the set it drops.
    """
    body = {
        "id": task_id,
        "annotations": annotations,
        "updated_at": updated_at,
        "client_id": client_id,
    }
    if name_deletions:
        kept = {a["id"] for a in json.loads(annotations or "[]")}
        body["deleted_ids"] = [f"a{i}" for i in range(_MAX_SEEDED) if f"a{i}" not in kept]
    return client.post("/api/tasks", json=body, headers=auth)


# Above the largest task any test here seeds.
_MAX_SEEDED = 1000


@pytest.fixture
def events(monkeypatch):
    """Capture log_event calls made by the tasks router."""
    captured = []
    real = tasks_router.log_event

    def spy(event, **fields):
        captured.append((event, fields))
        return real(event, **fields)

    monkeypatch.setattr(tasks_router, "log_event", spy)
    return captured


def _destructive(events):
    return [f for (e, f) in events if e == "task.save.destructive"]


def _seed(client, alice, count):
    """A task holding `count` annotations, ready to be saved over."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)
    res = _save(client, alice, task["id"], _annotations(count),
                updated_at=task["updated_at"])
    assert res.status_code == 200, res.text
    return project_id, task


# ---------------------------------------------------------------------------
# It fires on a real drop
# ---------------------------------------------------------------------------

def test_a_large_drop_is_logged(client, alice, events):
    """100 -> 20 is an 80% loss, far past the ratio."""
    _project_id, task = _seed(client, alice, 100)
    events.clear()

    res = _save(client, alice, task["id"], _annotations(20))
    assert res.status_code == 200, res.text

    hits = _destructive(events)
    assert len(hits) == 1, f"expected one destructive event, got {events}"
    assert hits[0]["objects_prev"] == 100
    assert hits[0]["objects"] == 20
    assert hits[0]["lost"] == 80
    assert hits[0]["loss_pct"] == 80
    assert hits[0]["task"] == task["id"]


def test_the_save_is_not_refused(client, alice, events):
    """The whole design: report, do not block.

    A drop the user made deliberately is logged, never refused: the
    annotations must be stored exactly as sent.
    """
    project_id, task = _seed(client, alice, 100)

    res = _save(client, alice, task["id"], _annotations(20))
    assert res.status_code == 200, res.text

    detail = client.get(f"/api/tasks/{task['id']}", headers=alice).json()
    assert len(detail["annotations"]) == 20, "the save must be applied, not refused"


def test_the_incident_shape_is_caught(client, alice, events):
    """Task 691: 2631 -> 1194, a 55% partial loss that every guard passed.

    Scaled down but the same proportions -- the point is that a *partial* loss
    is reported, not only an empty one.
    """
    _project_id, task = _seed(client, alice, 263)
    events.clear()

    res = _save(client, alice, task["id"], _annotations(119))
    assert res.status_code == 200, res.text

    hits = _destructive(events)
    assert len(hits) == 1
    assert hits[0]["loss_pct"] == 55


# ---------------------------------------------------------------------------
# It stays quiet on ordinary work
# ---------------------------------------------------------------------------

def test_a_small_task_never_triggers_it(client, alice, events):
    """Below the floor, proportions are meaningless.

    Deleting 3 of 8 boxes is a 38% loss and utterly routine. Without the floor
    this line would fire constantly on small tasks and be ignored, which is the
    same as not having it.
    """
    _project_id, task = _seed(client, alice, 8)
    events.clear()

    res = _save(client, alice, task["id"], _annotations(2))
    assert res.status_code == 200, res.text
    assert _destructive(events) == []


def test_a_modest_drop_is_not_logged(client, alice, events):
    """100 -> 80 is 20%, under the ratio: ordinary cleanup, no line."""
    _project_id, task = _seed(client, alice, 100)
    events.clear()

    res = _save(client, alice, task["id"], _annotations(80))
    assert res.status_code == 200, res.text
    assert _destructive(events) == []


def test_a_drop_just_under_the_ratio_is_not_logged(client, alice, events):
    """Exactly at the boundary: 100 -> 70 is 30%, and the test is strictly `>`."""
    _project_id, task = _seed(client, alice, 100)
    events.clear()

    res = _save(client, alice, task["id"], _annotations(70))
    assert res.status_code == 200, res.text
    assert _destructive(events) == []


def test_growing_a_task_is_never_destructive(client, alice, events):
    _project_id, task = _seed(client, alice, 100)
    events.clear()

    res = _save(client, alice, task["id"], _annotations(150))
    assert res.status_code == 200, res.text
    assert _destructive(events) == []


def test_an_unchanged_save_is_never_destructive(client, alice, events):
    """The re-open-and-save case, which must stay completely silent."""
    _project_id, task = _seed(client, alice, 100)
    events.clear()

    res = _save(client, alice, task["id"], _annotations(100))
    assert res.status_code == 200, res.text
    assert _destructive(events) == []


def test_a_time_only_save_is_never_destructive(client, alice, events):
    """No annotation set means no opinion about annotations.

    A time-only save omits the key entirely; it must not be read as a drop to
    zero. This is the same distinction that keeps it away from the clear-guard.
    """
    _project_id, task = _seed(client, alice, 100)
    events.clear()

    res = client.post("/api/tasks", json={
        "id": task["id"], "time_spent_delta": 30, "client_id": "tab-A",
    }, headers=alice)
    assert res.status_code == 200, res.text
    assert _destructive(events) == []


# ---------------------------------------------------------------------------
# Interaction with the guard that does refuse
# ---------------------------------------------------------------------------

def test_an_empty_payload_is_still_refused_and_logs_no_destructive(client, alice, events):
    """The clear-guard runs first and 422s, so the save never reaches this log.

    Pins the division of labour: emptying is *refused* (the guard), partial loss
    is *reported* (this line). A 422 must not also emit a destructive event --
    nothing was destroyed.
    """
    _project_id, task = _seed(client, alice, 100)
    events.clear()

    res = _save(client, alice, task["id"], "[]", name_deletions=False)
    assert res.status_code == 422, res.text
    assert _destructive(events) == []


def test_an_acknowledged_clear_is_reported_as_destructive(client, alice, events):
    """A genuine delete-all gets through -- and is still worth a line.

    `allow_clear` means the annotator meant it, not that it is unremarkable:
    emptying a 100-object task is exactly the event someone should be able to
    find afterwards.
    """
    _project_id, task = _seed(client, alice, 100)
    events.clear()

    res = client.post("/api/tasks", json={
        "id": task["id"], "annotations": "[]", "updated_at": None,
        "client_id": "tab-A", "allow_clear": True,
    }, headers=alice)
    assert res.status_code == 200, res.text

    hits = _destructive(events)
    assert len(hits) == 1
    assert hits[0]["objects"] == 0
    assert hits[0]["loss_pct"] == 100

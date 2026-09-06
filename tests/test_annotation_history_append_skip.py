"""The append skip: history rows are not written for saves that add only.

Companion to test_annotation_history.py, which covers the unconditional
behaviour. Everything here runs with ANNOTATION_HISTORY_APPEND_SKIP forced on,
because the flag ships **off** — the default path must keep recording every
save, and test_annotation_history.py is what proves that.

The property under test is asymmetric and worth stating plainly: skipping a row
for an additive save costs nothing, but skipping one for a destructive save
destroys the only remaining copy of the superseded work. So the destructive
cases here matter more than the additive ones.
"""
import json

import pytest

import models
from api.routers import tasks as tasks_module
from database import SessionLocal


@pytest.fixture
def append_skip(monkeypatch):
    """Force the skip on.

    There is no heartbeat to hold off any more. It existed to bound how long
    the skip could run without a snapshot, because deciding to skip cost
    ~191 ms of `is_pure_append` blob diffing and so had to be worth amortising.
    `_write_may_destroy` answers the same question from an index, so there is
    no cost to bound.
    """
    monkeypatch.setattr(tasks_module, "ANNOTATION_HISTORY_APPEND_SKIP", True)


def _project(client, auth):
    res = client.post("/api/projects", json={
        "name": "skip", "slug": "skip", "creator": "alice",
    }, headers=auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _create_task(client, auth, project_id):
    res = client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": "img.jpg", "status": "New"},
        headers=auth,
    )
    assert res.status_code == 200, res.text
    return res.json()


def _poly(points):
    return json.dumps([
        {"id": "poly-1", "type": "polygon", "labelId": "l1", "points": points}
    ])


def _pts(n, offset=0):
    return [{"x": float(i + offset), "y": float(i + offset)} for i in range(n)]


def _save(client, auth, task_id, blob, updated_at, client_id="tab-A"):
    return client.post("/api/tasks", json={
        "id": task_id, "annotations": blob,
        "updated_at": updated_at, "client_id": client_id,
    }, headers=auth)


def _rows(task_id):
    db = SessionLocal()
    try:
        return (
            db.query(models.TaskAnnotationHistory)
            .filter(models.TaskAnnotationHistory.task_id == task_id)
            .order_by(models.TaskAnnotationHistory.id)
            .all()
        )
    finally:
        db.close()


def _seed(client, auth):
    """A task holding a polygon, with one history row already present.

    The first save after creation always records (there is no prior row to
    reach back to), so seeding twice puts us in the steady state the skip is
    actually about.
    """
    pid = _project(client, auth)
    task = _create_task(client, auth, pid)
    r = _save(client, auth, task["id"], _poly(_pts(10)), task["updated_at"])
    assert r.status_code == 200, r.text
    r = _save(client, auth, task["id"], _poly(_pts(20)), r.json()["updated_at"])
    assert r.status_code == 200, r.text
    return task["id"], r.json()["updated_at"]


def test_appending_vertices_writes_no_history_row(client, alice, append_skip):
    task_id, updated = _seed(client, alice)
    before = len(_rows(task_id))

    for n in (21, 22, 23, 24, 25):
        res = _save(client, alice, task_id, _poly(_pts(n)), updated)
        assert res.status_code == 200, res.text
        updated = res.json()["updated_at"]

    assert len(_rows(task_id)) == before, (
        "purely additive saves must not consume the retention window"
    )


def test_vertex_inserted_mid_polygon_is_still_additive(client, alice, append_skip):
    """The production shape: a point lands in the middle, not at the tail."""
    task_id, updated = _seed(client, alice)
    before = len(_rows(task_id))

    points = _pts(20)
    points.insert(7, {"x": 99.5, "y": 99.5})
    res = _save(client, alice, task_id, _poly(points), updated)
    assert res.status_code == 200, res.text

    assert len(_rows(task_id)) == before


def test_removing_a_vertex_still_records(client, alice, append_skip):
    """Vertex-level loss the object count cannot see. Must be preserved."""
    task_id, updated = _seed(client, alice)
    before = len(_rows(task_id))

    res = _save(client, alice, task_id, _poly(_pts(3)), updated)
    assert res.status_code == 200, res.text

    rows = _rows(task_id)
    assert len(rows) == before + 1, "a shrinking polygon must be recorded"
    assert json.loads(rows[-1].annotations)[0]["points"] == _pts(20)


def test_moving_a_vertex_still_records(client, alice, append_skip):
    task_id, updated = _seed(client, alice)
    before = len(_rows(task_id))

    moved = _pts(20)
    moved[5] = {"x": -1.0, "y": -1.0}
    res = _save(client, alice, task_id, _poly(moved), updated)
    assert res.status_code == 200, res.text

    assert len(_rows(task_id)) == before + 1


def test_deleting_an_object_still_records(client, alice, append_skip):
    task_id, updated = _seed(client, alice)

    two = json.dumps([
        {"id": "poly-1", "type": "polygon", "labelId": "l1", "points": _pts(20)},
        {"id": "box-9", "type": "box", "labelId": "l2"},
    ])
    res = _save(client, alice, task_id, two, updated)
    assert res.status_code == 200, res.text
    updated = res.json()["updated_at"]
    before = len(_rows(task_id))

    res = _save(client, alice, task_id, _poly(_pts(20)), updated)
    assert res.status_code == 200, res.text

    rows = _rows(task_id)
    assert len(rows) == before + 1, "dropping an object must be recorded"
    assert len(json.loads(rows[-1].annotations)) == 2


def test_adding_a_new_object_is_additive(client, alice, append_skip):
    task_id, updated = _seed(client, alice)
    before = len(_rows(task_id))

    two = json.dumps([
        {"id": "poly-1", "type": "polygon", "labelId": "l1", "points": _pts(20)},
        {"id": "box-9", "type": "box", "labelId": "l2"},
    ])
    res = _save(client, alice, task_id, two, updated)
    assert res.status_code == 200, res.text

    assert len(_rows(task_id)) == before


def test_first_additive_save_records_nothing_either(client, alice, append_skip):
    """An additive save supersedes nothing, first or hundredth.

    This used to assert the opposite: with no prior row the first supersede was
    kept regardless. That rule came from the heartbeat, which existed because
    deciding to skip cost ~191 ms of `is_pure_append` blob diffing and so had
    to buy something. `_write_may_destroy` answers from an index, so the
    special case has no reason to exist and additive now means additive
    consistently.

    Nothing is lost by it: the save destroyed nothing, so there is nothing the
    dropped row could have restored.
    """
    pid = _project(client, alice)
    task = _create_task(client, alice, pid)

    res = _save(client, alice, task["id"], _poly(_pts(5)), task["updated_at"])
    assert res.status_code == 200, res.text
    res = _save(client, alice, task["id"], _poly(_pts(6)), res.json()["updated_at"])
    assert res.status_code == 200, res.text

    assert _rows(task["id"]) == []


def test_flag_off_records_additive_saves(client, alice, monkeypatch):
    """The shipped default must behave exactly as before."""
    monkeypatch.setattr(tasks_module, "ANNOTATION_HISTORY_APPEND_SKIP", False)
    task_id, updated = _seed(client, alice)
    before = len(_rows(task_id))

    res = _save(client, alice, task_id, _poly(_pts(30)), updated)
    assert res.status_code == 200, res.text

    assert len(_rows(task_id)) == before + 1


def test_annotations_still_saved_when_history_is_skipped(client, alice, append_skip):
    """The skip must affect history only — never the task's own blob."""
    task_id, updated = _seed(client, alice)

    res = _save(client, alice, task_id, _poly(_pts(40)), updated)
    assert res.status_code == 200, res.text

    detail = client.get(f"/api/tasks/{task_id}", headers=alice).json()
    # The detail endpoint returns annotations already parsed (TaskDetail), not
    # as the raw JSON string the save path accepts.
    stored = detail["annotations"]
    if isinstance(stored, str):
        stored = json.loads(stored)
    assert len(stored[0]["points"]) == 40

"""Which requests load a task's annotation rows, and how many queries that costs.

`Task.annotation_rows` used to be `lazy="selectin"`: every `db.get(Task, id)` --
which is what `require_task` is, and so what every heartbeat, claim, release,
lock-status and timer ping does -- pulled every shape of the task out of
Postgres and built an ORM object per row, to touch one dict key. On the live
profile that was 13% of all server CPU and a ~3 s heartbeat
(.devnotes/fix-performance-upgrade/01_PROFILE_ANALYSIS.md).

The relationship is now lazy, and the paths that read many tasks' shapes ask for
them explicitly. These tests pin both halves:

* a request that does not read annotations issues **no** query against the
  `annotations` table, whatever the task's size;
* the gallery and the export still load N tasks' shapes in **one** query, not N
  (the trap `lazy="select"` sets for a path that forgets the opt-in).
"""
import json

import pytest
from sqlalchemy import event

from database import engine

CLIENT = "tab-load-strategy"


class _AnnotationQueries:
    """Counts statements that read the `annotations` table while active."""

    def __init__(self):
        self.statements = []

    def _record(self, conn, cursor, statement, params, context, executemany):
        text = statement.lower()
        if "from annotations" in text and text.lstrip().startswith("select"):
            self.statements.append(statement)

    def __enter__(self):
        event.listen(engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc):
        event.remove(engine, "before_cursor_execute", self._record)

    @property
    def n(self):
        return len(self.statements)


def _project(client, auth, name):
    res = client.post("/api/projects", json={
        "name": name, "slug": name, "creator": "alice"}, headers=auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _task_with_shapes(client, auth, pid, n_shapes, name="img.jpg"):
    res = client.post(
        f"/api/tasks?projectId={pid}",
        json={"description": name, "status": "New", "client_id": CLIENT},
        headers=auth,
    )
    assert res.status_code == 200, res.text
    tid = res.json()["id"]
    shapes = [{"id": f"{tid}-{i}", "type": "polygon",
               "points": [{"x": i, "y": 1.0}, {"x": i + 1, "y": 2.0},
                          {"x": i + 2, "y": 3.0}]} for i in range(n_shapes)]
    res = client.post("/api/tasks", json={
        "id": tid, "client_id": CLIENT, "annotations": json.dumps(shapes)},
        headers=auth)
    assert res.status_code == 200, res.text
    return tid


@pytest.mark.parametrize("n_shapes", [1, 150])
def test_heartbeat_reads_no_annotations_whatever_the_task_size(client, alice, n_shapes):
    pid = _project(client, alice, f"hb{n_shapes}")
    tid = _task_with_shapes(client, alice, pid, n_shapes)
    with _AnnotationQueries() as q:
        res = client.post(f"/api/tasks/{tid}/heartbeat?client_id={CLIENT}", headers=alice)
    assert res.status_code == 200, res.text
    assert q.n == 0, q.statements


def test_claim_release_and_lock_status_read_no_annotations(client, alice):
    pid = _project(client, alice, "locks")
    tid = _task_with_shapes(client, alice, pid, 40)
    with _AnnotationQueries() as q:
        assert client.post(f"/api/tasks/{tid}/claim?client_id={CLIENT}", headers=alice).status_code == 200
        assert client.get(f"/api/tasks/{tid}/lock-status", headers=alice).status_code == 200
        assert client.delete(f"/api/tasks/{tid}/claim?client_id={CLIENT}", headers=alice).status_code == 200
    assert q.n == 0, q.statements


def test_timer_ping_with_a_task_reads_no_annotations(client, alice):
    pid = _project(client, alice, "timer")
    tid = _task_with_shapes(client, alice, pid, 40)
    with _AnnotationQueries() as q:
        res = client.post("/api/team/time",
                          json={"name": "alice", "time_logged": 5, "task_id": tid},
                          headers=alice)
    assert res.status_code == 200, res.text
    assert q.n == 0, q.statements


def test_opening_one_task_loads_its_shapes_in_one_query(client, alice):
    pid = _project(client, alice, "open")
    tid = _task_with_shapes(client, alice, pid, 60)
    with _AnnotationQueries() as q:
        res = client.get(f"/api/tasks/{tid}", headers=alice)
    assert res.status_code == 200, res.text
    assert len(res.json()["annotations"]) == 60
    assert q.n == 1, q.statements


def test_gallery_with_annotations_batches_all_tasks_into_one_query(client, alice):
    """The trap lazy loading sets: without the explicit opt-in this is N+1."""
    pid = _project(client, alice, "gallery")
    for i in range(6):
        _task_with_shapes(client, alice, pid, 5, name=f"g{i}.jpg")
    with _AnnotationQueries() as q:
        res = client.get(f"/api/tasks?projectId={pid}&include_annotations=true", headers=alice)
    assert res.status_code == 200, res.text
    rows = res.json()
    rows = rows["items"] if isinstance(rows, dict) else rows
    assert len(rows) == 6 and all(len(r["annotations"]) == 5 for r in rows)
    assert q.n == 1, q.statements


def test_gallery_without_annotations_reads_none(client, alice):
    pid = _project(client, alice, "gallery-light")
    for i in range(3):
        _task_with_shapes(client, alice, pid, 5, name=f"l{i}.jpg")
    with _AnnotationQueries() as q:
        res = client.get(f"/api/tasks?projectId={pid}&include_annotations=false", headers=alice)
    assert res.status_code == 200, res.text
    # Counts come from one narrow GROUP BY, never from loading shapes.
    assert all("group by" in s.lower() for s in q.statements), q.statements


@pytest.mark.parametrize("n_tasks", [2, 7])
def test_export_loads_every_tasks_shapes_in_one_query(client, alice, n_tasks):
    pid = _project(client, alice, f"exp{n_tasks}")
    for i in range(n_tasks):
        _task_with_shapes(client, alice, pid, 4, name=f"e{i}.jpg")
    with _AnnotationQueries() as q:
        res = client.post("/api/exports",
                          json={"projectId": pid, "format": "json"}, headers=alice)
        assert res.status_code == 200, res.text
        job = client.get(f"/api/exports/{res.json()['job_id']}", headers=alice).json()
        assert job["status"] == "completed", job
    # Constant in the number of tasks: the batch, not one query per task.
    assert q.n == 1, q.statements


def test_a_save_still_reads_the_rows_it_diffs_against_exactly_once(client, alice):
    pid = _project(client, alice, "save")
    tid = _task_with_shapes(client, alice, pid, 30)
    shapes = [{"id": f"{tid}-{i}", "type": "polygon",
               "points": [{"x": i, "y": 1.0}, {"x": i + 1, "y": 2.0},
                          {"x": i + 2, "y": 3.0}]} for i in range(30)]
    shapes[3]["points"][0]["x"] += 5
    with _AnnotationQueries() as q:
        res = client.post("/api/tasks", json={
            "id": tid, "client_id": CLIENT, "annotations": json.dumps(shapes)},
            headers=alice)
    assert res.status_code == 200, res.text
    row_loads = [s for s in q.statements if "count(" not in s.lower()]
    assert len(row_loads) == 1, q.statements

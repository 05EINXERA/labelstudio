"""Per-task annotation history — the recovery net under the save guards.

Annotations are one JSON blob replaced wholesale on every save, so a single
empty or stale write destroys a task and the previous value is gone once the
commit returns. That has been fixed three times on three different paths
(INCIDENT_692; task 707's stale undo stack; the 2026-08-11 pre-hydration
Ctrl+S). Every fix was a guard, and a guard only covers the path someone
thought of — these rows make the outcome recoverable whichever path caused it.

Spec: .devnotes/task-history/01_DESIGN.md.

The two properties that matter most here, and are easiest to regress:

  * **A history failure must never fail the save.** The write runs in a
    SAVEPOINT precisely so a broken/missing table cannot poison the caller's
    transaction. Getting this wrong makes the safety net the cause of the loss
    it exists to prevent.
  * **Identical resaves must not create rows.** One tab writes the same task
    from the debounced autosave, the visibilitychange beacon and the 30s timer
    drain. Without dedup a task accumulates its whole retention window of
    identical rows within seconds and the real previous value is pushed out.
"""
import json

import pytest

import models
from api.routers.tasks import ANNOTATION_HISTORY_KEEP
from database import SessionLocal


def _project(client, auth):
    res = client.post("/api/projects", json={
        "name": "hist", "slug": "hist", "creator": "alice",
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


def _annotations(n):
    return json.dumps([{"id": f"a{i}", "type": "box", "labelId": "l1"} for i in range(n)])


def _save(client, auth, task_id, n, updated_at, client_id="tab-A", **extra):
    payload = {
        "id": task_id, "annotations": _annotations(n),
        "updated_at": updated_at, "client_id": client_id,
    }
    payload.update(extra)
    return client.post("/api/tasks", json=payload, headers=auth)


def _history(task_id):
    """Rows for a task, oldest first."""
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


def test_first_save_records_nothing(client, alice):
    """A task with no prior work has nothing to preserve."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    assert _save(client, alice, task["id"], 3, task["updated_at"]).status_code == 200
    assert _history(task["id"]) == []


def test_replacing_write_preserves_the_previous_blob(client, alice):
    """The row holds what the task had BEFORE the write, not after."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    res = _save(client, alice, task["id"], 3, task["updated_at"])
    res = _save(client, alice, task["id"], 7, res.json()["updated_at"])
    assert res.status_code == 200

    rows = _history(task["id"])
    assert len(rows) == 1
    assert rows[0].annotation_count == 3, "must store the superseded value"
    assert rows[0].replaced_with_count == 7
    assert json.loads(rows[0].annotations) == json.loads(_annotations(3))


def test_identical_resave_creates_no_row(client, alice):
    """Autosave + beacon + timer drain all write the same blob repeatedly.

    Without this the retention window fills with identical rows in seconds and
    the genuinely previous value is evicted.
    """
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    res = _save(client, alice, task["id"], 4, task["updated_at"])
    res = _save(client, alice, task["id"], 4, res.json()["updated_at"])
    res = _save(client, alice, task["id"], 4, res.json()["updated_at"])
    assert res.status_code == 200

    assert _history(task["id"]) == []


def test_time_only_save_creates_no_row(client, alice):
    """A save omitting `annotations` leaves the stored set alone.

    It replaces nothing, so it supersedes nothing. (Rule: the guard and the
    history must agree that a missing key means "don't touch".)
    """
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    res = _save(client, alice, task["id"], 5, task["updated_at"])
    updated = res.json()["updated_at"]

    res = client.post("/api/tasks", json={
        "id": task["id"], "time_spent": 30,
        "updated_at": updated, "client_id": "tab-A",
    }, headers=alice)
    assert res.status_code == 200

    assert _history(task["id"]) == []


def test_a_wipe_is_recorded_with_both_counts(client, alice):
    """The event this whole feature exists for: non-empty -> empty.

    `annotation_count > 0` with `replaced_with_count == 0` is exactly what
    scripts/find_annotation_loss.py scans for.
    """
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    res = _save(client, alice, task["id"], 12, task["updated_at"])
    res = client.post("/api/tasks", json={
        "id": task["id"], "annotations": "[]", "allow_clear": True,
        "updated_at": res.json()["updated_at"], "client_id": "tab-A",
    }, headers=alice)
    assert res.status_code == 200

    rows = _history(task["id"])
    assert len(rows) == 1
    assert (rows[0].annotation_count, rows[0].replaced_with_count) == (12, 0)
    # The wiped work is recoverable verbatim.
    assert len(json.loads(rows[0].annotations)) == 12


def test_refused_clear_records_nothing(client, alice):
    """A 422'd write replaced nothing, so it superseded nothing.

    History must not log attempts — only actual replacements. Otherwise the
    retention window is consumed by writes that never happened.
    """
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    res = _save(client, alice, task["id"], 9, task["updated_at"])
    updated = res.json()["updated_at"]

    # No allow_clear: the server's clear-guard refuses this.
    res = client.post("/api/tasks", json={
        "id": task["id"], "annotations": "[]",
        "updated_at": updated, "client_id": "tab-A",
    }, headers=alice)
    assert res.status_code == 422

    assert _history(task["id"]) == []


def test_retention_keeps_only_the_newest_n(client, alice):
    """Bounded at ANNOTATION_HISTORY_KEEP per task, newest kept."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    updated = task["updated_at"]
    counts = [1, 2, 3, 4, 5, 6, 7, 8, 9]
    for n in counts:
        res = _save(client, alice, task["id"], n, updated)
        assert res.status_code == 200, res.text
        updated = res.json()["updated_at"]

    rows = _history(task["id"])
    assert len(rows) == ANNOTATION_HISTORY_KEEP

    # The retained rows are the most recent ones, in order, and contiguous:
    # each row's replaced_with_count is the next row's annotation_count.
    preserved = [r.annotation_count for r in rows]
    assert preserved == counts[-ANNOTATION_HISTORY_KEEP - 1:-1]
    for earlier, later in zip(rows, rows[1:]):
        assert earlier.replaced_with_count == later.annotation_count


def test_history_records_who_and_which_client(client, alice):
    """Attribution is the point of the loss scan: name the writer."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    res = _save(client, alice, task["id"], 2, task["updated_at"])
    _save(client, alice, task["id"], 6, res.json()["updated_at"], client_id="tab-XYZ")

    rows = _history(task["id"])
    assert len(rows) == 1
    assert rows[0].client_id == "tab-XYZ"
    assert rows[0].replaced_by_user_id is not None


def test_history_is_scoped_per_task(client, alice):
    """One task's writes never evict or appear in another's."""
    project_id = _project(client, alice)
    t1 = _create_task(client, alice, project_id)
    t2 = _create_task(client, alice, project_id)

    r1 = _save(client, alice, t1["id"], 3, t1["updated_at"])
    _save(client, alice, t1["id"], 4, r1.json()["updated_at"])

    r2 = _save(client, alice, t2["id"], 8, t2["updated_at"])
    _save(client, alice, t2["id"], 9, r2.json()["updated_at"])

    assert [r.annotation_count for r in _history(t1["id"])] == [3]
    assert [r.annotation_count for r in _history(t2["id"])] == [8]


def test_deleting_a_task_cascades_its_history(client, alice):
    """CASCADE, matching task_reviews: history describes a task that is gone."""
    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)

    res = _save(client, alice, task["id"], 3, task["updated_at"])
    _save(client, alice, task["id"], 5, res.json()["updated_at"])
    assert len(_history(task["id"])) == 1

    assert client.delete(f"/api/tasks/{task['id']}", headers=alice).status_code in (200, 204)
    assert _history(task["id"]) == []


def test_a_broken_history_write_never_fails_the_save(client, alice, monkeypatch):
    """The property that makes this feature safe to ship.

    The history INSERT runs in a SAVEPOINT so a failure — a missing table on a
    box where the migration has not been applied, a constraint surprise —
    discards only the history attempt. Without it the failed flush marks the
    session as needing a rollback and the annotation write dies too, which
    would make the safety net the cause of the data loss it exists to prevent.

    This is not hypothetical: it is exactly what happened while developing this
    against an un-migrated database.
    """
    import api.routers.tasks as tasks_module

    project_id = _project(client, alice)
    task = _create_task(client, alice, project_id)
    res = _save(client, alice, task["id"], 4, task["updated_at"])
    updated = res.json()["updated_at"]

    def exploding_history(*args, **kwargs):
        raise RuntimeError("simulated history failure")

    monkeypatch.setattr(tasks_module, "_count_annotations", exploding_history)

    res = _save(client, alice, task["id"], 11, updated)
    assert res.status_code == 200, "the save must survive a broken history write"

    detail = client.get(f"/api/tasks/{task['id']}", headers=alice).json()
    assert len(detail["annotations"]) == 11, "the annotations must still be written"

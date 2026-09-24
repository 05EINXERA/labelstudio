"""Deleting every shape must stick on a task that predates the row storage.

`annotation_dicts()` reads a task's annotation rows and falls back to the
legacy `Task.annotations` blob when there are none. The blob stopped being
written at the cutover, so on an older task it still holds the pre-cutover
set. Emptying such a task removed its rows, the next read fell through to that
stale blob, and the old annotations came back on reload — then the next
autosave wrote them back as rows (dev task 1370, 2026-09-24). Partial deletes
were unaffected, because any remaining row keeps the reader off the blob.

The fix: a sync that leaves zero rows also empties the blob.
"""
import json

from database import SessionLocal
import models


def _project(client, auth):
    res = client.post("/api/projects", json={
        "name": "legacy-blob", "slug": "legacy-blob", "creator": "alice",
    }, headers=auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _shapes(ids):
    return [{"id": i, "type": "box"} for i in ids]


def _pre_cutover_task(client, auth, blob_ids, row_ids):
    """A task whose legacy blob holds `blob_ids` and whose rows hold `row_ids`.

    That is the state of every task created before the cutover and edited
    after it: the rows moved on, the blob kept its original set.
    """
    project_id = _project(client, auth)
    task = client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": "P1370.jpg", "status": "New"},
        headers=auth,
    ).json()
    if row_ids:
        res = client.post("/api/tasks", json={
            "id": task["id"], "annotations": json.dumps(_shapes(row_ids)),
            "updated_at": task["updated_at"], "client_id": "tab-A",
        }, headers=auth)
        assert res.status_code == 200, res.text
    with SessionLocal() as db:
        db.get(models.Task, task["id"]).annotations = json.dumps(_shapes(blob_ids))
        db.commit()
    return task["id"]


def _save(client, auth, task_id, shapes, deleted_ids):
    return client.post("/api/tasks", json={
        "id": task_id, "annotations": json.dumps(shapes), "updated_at": None,
        "client_id": "tab-A", "deleted_ids": deleted_ids,
    }, headers=auth)


def _shown(client, auth, task_id):
    return [a["id"] for a in client.get(f"/api/tasks/{task_id}", headers=auth).json()["annotations"]]


def test_deleting_everything_does_not_bring_the_old_set_back(client, alice):
    """The 1370 report: rows 84 -> 0, blob still holds the pre-cutover 68."""
    old = [f"old{i}" for i in range(68)]
    current = [f"cur{i}" for i in range(84)]
    tid = _pre_cutover_task(client, alice, blob_ids=old, row_ids=current)

    res = _save(client, alice, tid, [], deleted_ids=current)
    assert res.status_code == 200, res.text

    assert _shown(client, alice, tid) == [], "the reload must show the empty task, not the stale blob"


def test_partial_then_full_delete_also_sticks(client, alice):
    """As reported: partial deletes save fine; the last one to zero did not."""
    old = [f"old{i}" for i in range(68)]
    current = [f"cur{i}" for i in range(84)]
    tid = _pre_cutover_task(client, alice, blob_ids=old, row_ids=current)

    assert _save(client, alice, tid, _shapes(current[:51]), deleted_ids=current[51:]).status_code == 200
    assert _shown(client, alice, tid) == current[:51]
    assert _save(client, alice, tid, _shapes(current[:1]), deleted_ids=current[1:51]).status_code == 200
    assert _save(client, alice, tid, [], deleted_ids=current[:1]).status_code == 200

    assert _shown(client, alice, tid) == []


def test_the_empty_task_stays_empty_across_later_saves(client, alice):
    """The loop: reload, then any save. Nothing may reappear."""
    tid = _pre_cutover_task(client, alice, blob_ids=["old1", "old2"], row_ids=["cur1"])
    assert _save(client, alice, tid, [], deleted_ids=["cur1"]).status_code == 200

    # A time-only save and a no-op empty save, as a reloaded tab would send.
    assert client.post("/api/tasks", json={
        "id": tid, "time_spent_delta": 5, "client_id": "tab-B",
    }, headers=alice).status_code == 200
    assert _shown(client, alice, tid) == []
    assert client.get(f"/api/tasks?projectId={_project_id_of(tid)}", headers=alice).json()[0]["annotations"] == []


def test_the_blob_is_emptied_not_just_ignored(client, alice):
    """The rollback copy must agree with the rows: an empty task is empty."""
    tid = _pre_cutover_task(client, alice, blob_ids=["old1"], row_ids=["cur1"])
    assert _save(client, alice, tid, [], deleted_ids=["cur1"]).status_code == 200

    with SessionLocal() as db:
        assert db.get(models.Task, tid).annotations == "[]"


def test_a_task_that_never_got_rows_still_reads_its_blob(client, alice):
    """The fallback's real job — an unconverted task — is untouched."""
    tid = _pre_cutover_task(client, alice, blob_ids=["old1", "old2"], row_ids=[])

    assert _shown(client, alice, tid) == ["old1", "old2"]


def test_a_partial_delete_leaves_the_blob_alone(client, alice):
    """Rows remain, so the reader never reaches the blob; no reason to write it."""
    tid = _pre_cutover_task(client, alice, blob_ids=["old1"], row_ids=["cur1", "cur2"])
    assert _save(client, alice, tid, _shapes(["cur1"]), deleted_ids=["cur2"]).status_code == 200

    with SessionLocal() as db:
        assert db.get(models.Task, tid).annotations == json.dumps(_shapes(["old1"]))
    assert _shown(client, alice, tid) == ["cur1"]


def _project_id_of(task_id):
    with SessionLocal() as db:
        return db.get(models.Task, task_id).project_id


def test_an_unintended_empty_save_is_still_refused_and_touches_nothing(client, alice):
    """The blob is cleared inside the row sync, which runs only after the wipe
    guard has passed. A faulted empty save (no deleted_ids) is refused first,
    so neither the rows nor the rollback copy change."""
    old = [f"old{i}" for i in range(68)]
    current = [f"cur{i}" for i in range(84)]
    tid = _pre_cutover_task(client, alice, blob_ids=old, row_ids=current)

    res = client.post("/api/tasks", json={
        "id": tid, "annotations": "[]", "updated_at": None, "client_id": "tab-A",
    }, headers=alice)

    assert res.status_code == 422, res.text
    assert _shown(client, alice, tid) == current
    with SessionLocal() as db:
        assert db.get(models.Task, tid).annotations == json.dumps(_shapes(old))


def test_an_unintended_near_empty_save_is_still_refused(client, alice):
    """The task 660 shape on a pre-cutover task: one stray box over 84."""
    tid = _pre_cutover_task(client, alice, blob_ids=["old1"], row_ids=[f"cur{i}" for i in range(84)])

    res = _save(client, alice, tid, _shapes(["stray"]), deleted_ids=[])

    assert res.status_code == 422, res.text
    assert len(_shown(client, alice, tid)) == 84

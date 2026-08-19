"""A write that changes nothing must be inert.

The bug (.devnotes/unwanted-time-change/01_DIAGNOSIS.md): a reviewer opens a
task, pans and zooms, and leaves. The session timer had auto-started on the
first canvas `pointerdown`, so the unload flush POSTs a time-only save. The
server bumped `Task.updated_at` for it — making a look-only visit
indistinguishable, to everyone else, from a fresh edit.

`updated_at` is not just a display column: it is the optimistic-concurrency
token (CLAUDE.md rule 11), so a no-op save also rotated that token for every
other client.

The rule under test: `updated_at` moves only when a field's incoming value
actually differs from what is stored. Everything that *is* a real change —
annotations, status, assignee, description, a non-zero time delta — must still
bump it, and the conflict model must be untouched
(tests/test_task_save_conflicts.py is the guard for that).
"""
import json

import pytest

import models
from database import SessionLocal


def _project(client, auth, slug="noedit"):
    res = client.post("/api/projects", json={
        "name": slug, "slug": slug, "creator": "alice",
    }, headers=auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _annotations(n):
    return json.dumps([
        {"id": f"a{i}", "type": "box", "labelId": "l1"} for i in range(n)
    ])


def _create_task(client, auth, project_id, annotations=None, status="In Progress"):
    body = {"description": "img.jpg", "status": status}
    if annotations is not None:
        body["annotations"] = annotations
    res = client.post(
        f"/api/tasks?projectId={project_id}", json=body, headers=auth
    )
    assert res.status_code == 200, res.text
    return res.json()


def _history_count(task_id):
    """History rows for a task, read straight from the DB.

    There is no read endpoint for these by design — restoring is a human
    decision driven by scripts/ (.devnotes/task-history/01_DESIGN.md § 3) — so
    the existing suite queries the table directly and so does this.
    """
    db = SessionLocal()
    try:
        return (
            db.query(models.TaskAnnotationHistory)
            .filter(models.TaskAnnotationHistory.task_id == task_id)
            .count()
        )
    finally:
        db.close()


def _detail(client, auth, task_id):
    res = client.get(f"/api/tasks/{task_id}", headers=auth)
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------------------
# The bug itself
# ---------------------------------------------------------------------------

def test_time_only_save_with_zero_delta_does_not_bump_updated_at(client, alice):
    """The look-only visit, reduced to its payload.

    This is exactly what `drainTaskTime` sends when the unload flush fires and
    nothing was edited: an id, a status equal to the stored one, and a time
    delta. No `annotations` key at all — the client has positive evidence that
    the work is untouched.
    """
    pid = _project(client, alice, "noedit-zero")
    task = _create_task(client, alice, pid, annotations=_annotations(3))
    tid, before = task["id"], task["updated_at"]

    res = client.post("/api/tasks", json={
        "id": tid,
        "time_spent_delta": 0,
        "status": "In Progress",
        "updated_at": before,
        "client_id": "tab-1",
    }, headers=alice)
    assert res.status_code == 200, res.text

    assert res.json()["updated_at"] == before, (
        "a save that changed nothing moved updated_at"
    )
    assert _detail(client, alice, tid)["time_spent"] == 0


def test_value_identical_save_is_inert(client, alice):
    """Resending the same annotations and status changes nothing.

    A cached bundle mid-rollout (rule 13 — no build step) still sends the full
    annotation set on a time-only drain. Byte-identical content is not an edit,
    whichever client sent it.
    """
    pid = _project(client, alice, "noedit-identical")
    blob = _annotations(2)
    task = _create_task(client, alice, pid, annotations=blob)
    tid, before = task["id"], task["updated_at"]

    res = client.post("/api/tasks", json={
        "id": tid,
        "annotations": blob,
        "status": "In Progress",
        "time_spent_delta": 0,
        "updated_at": before,
        "client_id": "tab-1",
    }, headers=alice)
    assert res.status_code == 200, res.text
    assert res.json()["updated_at"] == before

    # The stored work is of course still there.
    # TaskDetail returns annotations already parsed, not as a JSON string.
    assert _detail(client, alice, tid)["annotations"] == json.loads(blob)


def test_inert_save_writes_no_history_row(client, alice):
    """A no-op must not consume one of the 50 history slots.

    ANNOTATION_HISTORY_KEEP is sized for real saves; letting look-only visits
    evict entries would shrink the recoverable window for no reason.
    """
    pid = _project(client, alice, "noedit-history")
    blob = _annotations(2)
    task = _create_task(client, alice, pid, annotations=blob)
    tid, before = task["id"], task["updated_at"]

    baseline = _history_count(tid)

    client.post("/api/tasks", json={
        "id": tid, "annotations": blob, "status": "In Progress",
        "time_spent_delta": 0, "updated_at": before, "client_id": "tab-1",
    }, headers=alice)

    assert _history_count(tid) == baseline, "an inert save recorded a history row"


def test_inert_save_leaves_the_conflict_token_usable(client, alice):
    """An inert save must not cost another client its right to write.

    Before the fix a look-only visit rotated `updated_at`, so the token every
    other tab held went stale and their next real save 409'd against a write
    that had changed nothing. Now the stored value is returned untouched, so a
    tab holding the current token still matches exactly and is let through by
    the `tokens_match` branch — even though the inert save claimed
    `last_client_id`.

    This is the whole reason the timestamp gate matters beyond cosmetics.
    """
    pid = _project(client, alice, "noedit-token")
    task = _create_task(client, alice, pid, annotations=_annotations(1))
    tid = task["id"]

    # tab-2 reads the task and holds its token.
    token = _detail(client, alice, tid)["updated_at"]

    # tab-1 opens it, pans and zooms, and leaves. Nothing changed.
    inert = client.post("/api/tasks", json={
        "id": tid, "time_spent_delta": 0, "status": "In Progress",
        "updated_at": token, "client_id": "tab-1",
    }, headers=alice)
    assert inert.status_code == 200, inert.text

    # tab-2's token is still current, so its real edit is accepted despite
    # tab-1 being the recorded last writer.
    res = client.post("/api/tasks", json={
        "id": tid, "annotations": _annotations(4), "status": "In Progress",
        "updated_at": token, "client_id": "tab-2",
    }, headers=alice)
    assert res.status_code == 200, res.text
    assert len(_detail(client, alice, tid)["annotations"]) == 4


def test_genuine_conflict_after_inert_save_is_still_detected(client, alice):
    """The gate must not become a way to sneak a stale write past 409.

    tab-1 makes a *real* edit; tab-2 is still holding the pre-edit token. That
    is a true conflict and must still be refused, inert saves in the mix or not.
    """
    pid = _project(client, alice, "noedit-token-conflict")
    task = _create_task(client, alice, pid, annotations=_annotations(1))
    tid = task["id"]
    stale = _detail(client, alice, tid)["updated_at"]

    client.post("/api/tasks", json={
        "id": tid, "time_spent_delta": 0, "status": "In Progress",
        "updated_at": stale, "client_id": "tab-1",
    }, headers=alice)
    real = client.post("/api/tasks", json={
        "id": tid, "annotations": _annotations(9), "status": "In Progress",
        "updated_at": stale, "client_id": "tab-1",
    }, headers=alice)
    assert real.status_code == 200, real.text

    res = client.post("/api/tasks", json={
        "id": tid, "annotations": _annotations(2), "status": "In Progress",
        "updated_at": stale, "client_id": "tab-2",
    }, headers=alice)
    assert res.status_code == 409, res.text
    # tab-1's work survives untouched.
    assert len(_detail(client, alice, tid)["annotations"]) == 9


# ---------------------------------------------------------------------------
# Everything that IS a change must still bump — the regression guards
# ---------------------------------------------------------------------------

def test_annotation_change_still_bumps_updated_at(client, alice):
    pid = _project(client, alice, "edit-annotations")
    task = _create_task(client, alice, pid, annotations=_annotations(1))
    tid, before = task["id"], task["updated_at"]

    res = client.post("/api/tasks", json={
        "id": tid, "annotations": _annotations(5), "status": "In Progress",
        "updated_at": before, "client_id": "tab-1",
    }, headers=alice)
    assert res.status_code == 200, res.text
    assert res.json()["updated_at"] != before

    assert len(_detail(client, alice, tid)["annotations"]) == 5


def test_annotation_change_still_records_history(client, alice):
    """Rule 11 guard: the history layer must not be starved by the new gate."""
    pid = _project(client, alice, "edit-history")
    task = _create_task(client, alice, pid, annotations=_annotations(2))
    tid, before = task["id"], task["updated_at"]

    baseline = _history_count(tid)

    client.post("/api/tasks", json={
        "id": tid, "annotations": _annotations(7), "status": "In Progress",
        "updated_at": before, "client_id": "tab-1",
    }, headers=alice)

    assert _history_count(tid) > baseline, "a real edit failed to record history"


def test_status_change_still_bumps_updated_at(client, alice):
    """Reviewers sort by this column; a sign-off must surface immediately."""
    pid = _project(client, alice, "edit-status")
    task = _create_task(client, alice, pid, annotations=_annotations(1))
    tid, before = task["id"], task["updated_at"]

    res = client.post("/api/tasks", json={
        "id": tid, "status": "Completed",
        "updated_at": before, "client_id": "tab-1",
    }, headers=alice)
    assert res.status_code == 200, res.text
    assert res.json()["updated_at"] != before
    assert _detail(client, alice, tid)["status"] == "Completed"


def test_approved_batch_status_still_bumps_updated_at(client, alice):
    """Any approved-group synonym is a review decision, not a no-op.

    Never tested by name where the group is meant (rule 11a) — this drives the
    whole group so a new batch status cannot silently become inert.
    """
    from schemas import APPROVED_STATUSES

    pid = _project(client, alice, "edit-approved")
    for i, status in enumerate(sorted(APPROVED_STATUSES)):
        task = _create_task(client, alice, pid, annotations=_annotations(1))
        tid, before = task["id"], task["updated_at"]

        res = client.post("/api/tasks", json={
            "id": tid, "status": status,
            "updated_at": before, "client_id": f"tab-{i}",
        }, headers=alice)
        assert res.status_code == 200, f"{status}: {res.text}"
        assert res.json()["updated_at"] != before, (
            f"approving under batch status {status!r} left updated_at stale"
        )


def test_nonzero_time_delta_still_bumps_updated_at(client, alice):
    """A non-zero delta is a real column write.

    After the frontend gate, a non-zero delta only reaches the server when work
    genuinely happened, so treating it as inert would leave a real annotation
    session's final drain with a stale timestamp.
    """
    pid = _project(client, alice, "edit-time")
    task = _create_task(client, alice, pid, annotations=_annotations(1))
    tid, before = task["id"], task["updated_at"]

    res = client.post("/api/tasks", json={
        "id": tid, "time_spent_delta": 42, "status": "In Progress",
        "updated_at": before, "client_id": "tab-1",
    }, headers=alice)
    assert res.status_code == 200, res.text
    assert res.json()["updated_at"] != before
    assert _detail(client, alice, tid)["time_spent"] == 42


def test_description_change_still_bumps_updated_at(client, alice):
    pid = _project(client, alice, "edit-description")
    task = _create_task(client, alice, pid, annotations=_annotations(1))
    tid, before = task["id"], task["updated_at"]

    res = client.post("/api/tasks", json={
        "id": tid, "description": "renamed.jpg",
        "updated_at": before, "client_id": "tab-1",
    }, headers=alice)
    assert res.status_code == 200, res.text
    assert res.json()["updated_at"] != before


def test_assignee_change_still_bumps_updated_at(client, alice):
    pid = _project(client, alice, "edit-assignee")
    task = _create_task(client, alice, pid, annotations=_annotations(1))
    tid, before = task["id"], task["updated_at"]

    res = client.post("/api/tasks", json={
        "id": tid, "assignee": "bob",
        "updated_at": before, "client_id": "tab-1",
    }, headers=alice)
    assert res.status_code == 200, res.text
    assert res.json()["updated_at"] != before


def test_clear_guard_still_refuses_an_empty_save(client, alice):
    """INCIDENT_692's guard sits ahead of the new gate and is unchanged.

    An empty blob against existing work is a *change*, so the gate never sees
    it — the guard must still answer 422.
    """
    pid = _project(client, alice, "noedit-clearguard")
    task = _create_task(client, alice, pid, annotations=_annotations(3))
    tid, before = task["id"], task["updated_at"]

    res = client.post("/api/tasks", json={
        "id": tid, "annotations": "[]", "status": "In Progress",
        "updated_at": before, "client_id": "tab-1",
    }, headers=alice)
    assert res.status_code == 422, res.text

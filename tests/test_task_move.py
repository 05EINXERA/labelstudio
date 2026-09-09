"""Moving tasks between projects — `POST /api/tasks/bulk-move`.

The property under test throughout is **integrity**: a move relocates one
`tasks` row and remaps its classes, and everything else about the task —
annotation rows and their ids, review history, assignment, elapsed time,
status, the image — comes through untouched, with nothing duplicated anywhere.

Plan: `.devnotes/move-task-feature/`. Edge cases cited as M-nn are in
`04_EDGE_CASES.md`.
"""
import json

import pytest

import models
from api.routers.tasks import MOVE_CLIENT_SENTINEL
from database import SessionLocal
from tests.conftest import unique_label_id


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# --- fixtures as HTTP calls, so the tests exercise the real permission path ---


def _me(client, headers):
    return client.get("/api/auth/me", headers=headers).json()


def _project(client, headers, name="P"):
    return client.post(
        "/api/projects",
        json={"name": name, "slug": name.lower(), "creator": "ignored"},
        headers=headers,
    ).json()["id"]


def _label(client, headers, project_id, name, color="#ff0000"):
    label_id = unique_label_id()
    res = client.post(
        "/api/labels",
        json={"id": label_id, "name": name, "color": color, "projectId": project_id},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return label_id


def _task(client, headers, project_id, description="a.jpg", status="New"):
    res = client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": description, "status": status},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _save(client, headers, task_id, annotations, client_id="tab-move"):
    """Save annotations the way the canvas does: read, then write with the token.

    The `updated_at` round-trip is not optional. A write that carries
    annotations without one is refused as a possible clobber of work the client
    never saw (`missing_updated_at`), which is exactly the guard this feature
    leans on in `test_move_resets_the_conflict_token_so_an_open_tab_reloads`.
    """
    detail = client.get(f"/api/tasks/{task_id}", headers=headers).json()
    res = client.post(
        "/api/tasks",
        json={
            "id": task_id,
            "annotations": json.dumps(annotations),
            "client_id": client_id,
            "updated_at": detail["updated_at"],
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res


def _shape(label_id, ident):
    return {
        "id": ident, "type": "rect", "labelId": label_id,
        "x": 1, "y": 2, "width": 3, "height": 4,
    }


def _move(client, headers, ids, target, **extra):
    payload = {"ids": ids, "target_project_id": target}
    payload.update(extra)
    return client.post("/api/tasks/bulk-move", json=payload, headers=headers)


def _team_with_grant(client, owner, member, project_id, role="annotator", name=None):
    team = client.post(
        "/api/teams", json={"name": name or f"T-{project_id}-{role}"}, headers=owner
    ).json()
    client.post(
        f"/api/teams/{team['id']}/members",
        json={"username": _me(client, member)["username"], "role": "member"},
        headers=owner,
    )
    res = client.post(
        f"/api/projects/{project_id}/grants",
        json={"team_id": team["id"], "role": role},
        headers=owner,
    )
    assert res.status_code in (200, 201), res.text
    return team


# --- integrity ---------------------------------------------------------------


def test_move_preserves_the_whole_task(client, alice, db):
    """The headline property: everything travels, nothing is duplicated."""
    source = _project(client, alice, "Source")
    target = _project(client, alice, "Target")
    car = _label(client, alice, source, "car")
    _label(client, alice, target, "car")   # same class, different row

    task_id = _task(client, alice, source, "P1000123.jpg")
    _save(client, alice, task_id, [_shape(car, "obj-1"), _shape(car, "obj-2")])
    # A little elapsed time, so the move has something to lose.
    client.post(
        "/api/tasks",
        json={"id": task_id, "time_delta": 90, "client_id": "tab-move"},
        headers=alice,
    )

    before = db.get(models.Task, task_id)
    db.refresh(before)
    kept = {
        "description": before.description,
        "image_path": before.image_path,
        "status": before.status,
        "time_spent": before.time_spent,
        "created_at": before.created_at,
    }
    ann_ids_before = {
        row.id for row in db.query(models.Annotation).filter(
            models.Annotation.task_id == task_id)
    }
    tasks_before = db.query(models.Task).count()
    anns_before = db.query(models.Annotation).count()

    res = _move(client, alice, [task_id], target)
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["moved"], body["skipped"]) == (1, 0)

    db.expire_all()
    after = db.get(models.Task, task_id)
    assert after.project_id == target
    for field, value in kept.items():
        assert getattr(after, field) == value, field

    # No rows created or destroyed anywhere — the task row moved, it was not
    # copied. This is the assertion that would catch a "copy then delete"
    # implementation, which would mint new annotation ids.
    assert db.query(models.Task).count() == tasks_before
    assert db.query(models.Annotation).count() == anns_before
    assert {
        row.id for row in db.query(models.Annotation).filter(
            models.Annotation.task_id == task_id)
    } == ann_ids_before


def test_moved_annotations_resolve_against_the_destination_classes(client, alice, db):
    """The failure this feature exists to prevent.

    Without reconciliation the FK stays satisfied while pointing at the source
    project's label, and the first autosave afterwards NULLs it.
    """
    source = _project(client, alice, "S")
    target = _project(client, alice, "T")
    src_car = _label(client, alice, source, "car")
    dst_car = _label(client, alice, target, "car")

    task_id = _task(client, alice, source)
    _save(client, alice, task_id, [_shape(src_car, "obj-1")])

    body = _move(client, alice, [task_id], target).json()
    assert body["labels_matched"] == 1
    assert body["labels_created"] == 0
    assert body["annotations_relabelled"] == 1

    db.expire_all()
    row = db.query(models.Annotation).filter(
        models.Annotation.task_id == task_id).one()
    assert row.label_id == dst_car

    # And the point of all of it: a save after the move keeps the class.
    detail = client.get(f"/api/tasks/{task_id}", headers=alice).json()
    _save(client, alice, task_id, detail["annotations"], client_id="tab-after-move")
    db.expire_all()
    row = db.query(models.Annotation).filter(
        models.Annotation.task_id == task_id).one()
    assert row.label_id == dst_car


def test_missing_class_is_created_in_the_destination(client, alice, db):
    source = _project(client, alice, "S")
    target = _project(client, alice, "T")
    forklift = _label(client, alice, source, "forklift", color="#00ff00")
    task_id = _task(client, alice, source)
    _save(client, alice, task_id, [_shape(forklift, "obj-1")])

    body = _move(client, alice, [task_id], target).json()
    assert body["labels_created"] == 1

    labels = client.get(f"/api/labels?projectId={target}", headers=alice).json()
    assert [(l["name"], l["color"]) for l in labels] == [("forklift", "#00ff00")]
    # The source keeps its own class row; the move is not a transfer of classes.
    assert any(
        l["id"] == forklift
        for l in client.get(f"/api/labels?projectId={source}", headers=alice).json()
    )


def test_relabel_does_not_touch_tasks_left_behind(client, alice, db):
    """The most dangerous statement in the feature, at the HTTP level."""
    source = _project(client, alice, "S")
    target = _project(client, alice, "T")
    car = _label(client, alice, source, "car")

    moving = _task(client, alice, source, "moving.jpg")
    staying = _task(client, alice, source, "staying.jpg")
    _save(client, alice, moving, [_shape(car, "m-1")])
    _save(client, alice, staying, [_shape(car, "s-1")])

    _move(client, alice, [moving], target)

    db.expire_all()
    stayed = db.query(models.Annotation).filter(
        models.Annotation.task_id == staying).one()
    assert stayed.label_id == car
    assert db.get(models.Task, staying).project_id == source


def test_match_only_orphans_and_reports(client, alice, db):
    """M-11: nothing is created, the original id survives in `extra`."""
    source = _project(client, alice, "S")
    target = _project(client, alice, "T")
    forklift = _label(client, alice, source, "forklift")
    task_id = _task(client, alice, source)
    _save(client, alice, task_id, [_shape(forklift, "obj-1")])

    body = _move(client, alice, [task_id], target,
                 class_strategy="match_only").json()
    assert body["labels_created"] == 0
    assert any("forklift" in w for w in body["warnings"])
    assert client.get(f"/api/labels?projectId={target}", headers=alice).json() == []

    db.expire_all()
    row = db.query(models.Annotation).filter(
        models.Annotation.task_id == task_id).one()
    assert row.label_id is None
    assert json.loads(row.extra)["_orphanedLabelId"] == forklift

    # The wire format is unchanged: the client still sees the original labelId.
    detail = client.get(f"/api/tasks/{task_id}", headers=alice).json()
    assert detail["annotations"][0]["labelId"] == forklift


# --- assignment --------------------------------------------------------------


def test_assignment_survives_the_move_and_is_warned_about(client, alice, bob, db):
    """M-17/M-18: the move never clears assignment — the workflow is move,
    then grant."""
    source = _project(client, alice, "S")
    target = _project(client, alice, "T")
    team = _team_with_grant(client, alice, bob, source, "annotator")
    bob_id = _me(client, bob)["id"]

    task_id = _task(client, alice, source)
    res = client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"], "assignee_user_id": bob_id},
        headers=alice,
    )
    assert res.status_code == 200, res.text

    body = _move(client, alice, [task_id], target).json()

    db.expire_all()
    moved = db.get(models.Task, task_id)
    assert moved.assigned_team_id == team["id"]
    assert moved.assignee_user_id == bob_id
    assert any("no access to the destination" in w for w in body["warnings"])

    # Bob cannot reach it yet...
    assert client.get(f"/api/tasks/{task_id}", headers=bob).status_code == 404

    # ...and granting his team access to the destination is all it takes.
    client.post(
        f"/api/projects/{target}/grants",
        json={"team_id": team["id"], "role": "annotator"},
        headers=alice,
    )
    assert client.get(f"/api/tasks/{task_id}", headers=bob).status_code == 200


def test_no_assignment_warning_when_the_team_already_has_access(client, alice, bob):
    source = _project(client, alice, "S")
    target = _project(client, alice, "T")
    team = _team_with_grant(client, alice, bob, source, "annotator")
    client.post(
        f"/api/projects/{target}/grants",
        json={"team_id": team["id"], "role": "annotator"},
        headers=alice,
    )
    task_id = _task(client, alice, source)
    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"]},
        headers=alice,
    )

    body = _move(client, alice, [task_id], target).json()
    assert not any("no access to the destination" in w for w in body["warnings"])


# --- status ------------------------------------------------------------------


def test_approved_task_stays_approved_and_both_projects_resync(client, alice, db):
    """M-15/M-16: a sign-off is never voided, and neither project badge lies."""
    source = _project(client, alice, "S")
    target = _project(client, alice, "T")
    approved = _task(client, alice, source, "done.jpg", status="Completed")
    pending = _task(client, alice, source, "todo.jpg", status="New")
    res = client.post(
        f"/api/tasks/{approved}/review",
        json={"action": "approved"},
        headers=alice,
    )
    assert res.status_code == 200, res.text
    reviews_before = db.query(models.TaskReview).filter(
        models.TaskReview.task_id == approved).count()
    assert reviews_before == 1

    _move(client, alice, [approved], target)

    db.expire_all()
    moved = db.get(models.Task, approved)
    assert moved.status == "Approved"
    # Review history hangs off task_id and travels with the task (M-26).
    assert db.query(models.TaskReview).filter(
        models.TaskReview.task_id == approved).count() == reviews_before

    # The target now holds one task, and it is approved.
    assert db.get(models.Project, target).status == "Completed"
    # The source is left with only the pending one, so it is no longer complete.
    assert db.get(models.Project, source).status != "Completed"
    assert db.get(models.Task, pending).project_id == source


# --- permissions -------------------------------------------------------------


def test_manager_on_the_source_cannot_move(client, alice, bob):
    """Owner-only on both ends (02_DESIGN.md § 5)."""
    source = _project(client, alice, "S")
    target = _project(client, alice, "T")
    _team_with_grant(client, alice, bob, source, "manager")
    _team_with_grant(client, alice, bob, target, "manager", name="T-mgr-target")
    task_id = _task(client, alice, source)

    res = _move(client, bob, [task_id], target)
    # Bob can see the target (manager grant) but not at Owner rank.
    assert res.status_code == 403
    assert "Owner" in res.json()["detail"]


def test_target_the_caller_has_no_role_on_is_404(client, alice, bob):
    """Anti-enumeration: an id you cannot see is indistinguishable from absent."""
    source = _project(client, bob, "S")
    other = _project(client, alice, "Alice's")
    task_id = _task(client, bob, source)

    res = _move(client, bob, [task_id], other)
    assert res.status_code == 404


def test_ids_the_caller_does_not_own_are_skipped_not_fatal(client, alice, bob, db):
    source = _project(client, alice, "S")
    target = _project(client, alice, "T")
    mine = _task(client, alice, source, "mine.jpg")
    theirs = _task(client, bob, _project(client, bob, "Bobs"), "theirs.jpg")

    body = _move(client, alice, [mine, theirs], target).json()
    assert (body["moved"], body["skipped"]) == (1, 1)

    db.expire_all()
    assert db.get(models.Task, mine).project_id == target
    assert db.get(models.Task, theirs).project_id != target


# --- concurrency and idempotency ---------------------------------------------


def test_a_locked_task_blocks_the_whole_batch(client, alice, db):
    """M-13: all-or-nothing. A refusal moves nothing at all."""
    source = _project(client, alice, "S")
    target = _project(client, alice, "T")
    locked = _task(client, alice, source, "open.jpg")
    other = _task(client, alice, source, "free.jpg")
    assert client.post(
        f"/api/tasks/{locked}/claim?client_id=someone-else", headers=alice
    ).status_code == 200

    res = _move(client, alice, [locked, other], target)
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert [b["task_id"] for b in detail["blocked"]] == [locked]

    db.expire_all()
    assert db.get(models.Task, locked).project_id == source
    assert db.get(models.Task, other).project_id == source, "nothing may move"

    # force overrides it.
    assert _move(client, alice, [locked, other], target,
                 force=True).status_code == 200
    db.expire_all()
    assert db.get(models.Task, locked).project_id == target


def test_move_resets_the_conflict_token_so_an_open_tab_reloads(client, alice, db):
    """M-14: the tab still holds the source project's label ids in memory.

    The move stamps a sentinel writer so that tab compares as a *different*
    client and takes a 409 instead of writing the stale ids back over the remap.

    NULL would not do: with no stored identity the check falls through to the
    timestamp-only branch, which tolerates CONFLICT_TOLERANCE_SECONDS and would
    therefore accept a save issued moments after the move. This test is what
    caught that.
    """
    source = _project(client, alice, "S")
    target = _project(client, alice, "T")
    car = _label(client, alice, source, "car")
    _label(client, alice, target, "car")
    task_id = _task(client, alice, source)
    _save(client, alice, task_id, [_shape(car, "obj-1")], client_id="tab-A")

    db.expire_all()
    stale_updated_at = db.get(models.Task, task_id).updated_at.isoformat()

    _move(client, alice, [task_id], target)

    db.expire_all()
    assert db.get(models.Task, task_id).last_client_id == MOVE_CLIENT_SENTINEL

    res = client.post(
        "/api/tasks",
        json={
            "id": task_id,
            "annotations": json.dumps([_shape(car, "obj-1")]),
            "client_id": "tab-A",
            "updated_at": stale_updated_at,
        },
        headers=alice,
    )
    assert res.status_code == 409


def test_re_posting_the_same_move_is_a_no_op(client, alice, db):
    """M-01/M-27: a retry after a dropped response must not be an error."""
    source = _project(client, alice, "S")
    target = _project(client, alice, "T")
    task_id = _task(client, alice, source)

    assert _move(client, alice, [task_id], target).json()["moved"] == 1
    again = _move(client, alice, [task_id], target).json()
    assert (again["moved"], again["skipped"]) == (0, 1)

    db.expire_all()
    assert db.get(models.Task, task_id).project_id == target


def test_moving_the_same_class_twice_creates_one_destination_label(client, alice):
    source = _project(client, alice, "S")
    target = _project(client, alice, "T")
    car = _label(client, alice, source, "car")
    first = _task(client, alice, source, "a.jpg")
    second = _task(client, alice, source, "b.jpg")
    _save(client, alice, first, [_shape(car, "a-1")])
    _save(client, alice, second, [_shape(car, "b-1")])

    assert _move(client, alice, [first], target).json()["labels_created"] == 1
    assert _move(client, alice, [second], target).json()["labels_created"] == 0

    labels = client.get(f"/api/labels?projectId={target}", headers=alice).json()
    assert [l["name"] for l in labels] == ["car"]


def test_duplicate_filename_is_warned_not_blocked(client, alice, db):
    """M-12: nothing is keyed on `description`."""
    source = _project(client, alice, "S")
    target = _project(client, alice, "T")
    moving = _task(client, alice, source, "clash.jpg")
    _task(client, alice, target, "clash.jpg")

    body = _move(client, alice, [moving], target).json()
    assert body["moved"] == 1
    assert any("already has a task named" in w for w in body["warnings"])


def test_empty_ids_is_rejected(client, alice):
    target = _project(client, alice, "T")
    assert _move(client, alice, [], target).status_code == 400


# --- move targets ------------------------------------------------------------


def test_move_targets_lists_only_owned_projects(client, alice, bob):
    source = _project(client, alice, "Source")
    owned = _project(client, alice, "Owned")
    granted = _project(client, bob, "Granted to alice")
    _team_with_grant(client, bob, alice, granted, "manager", name="T-granted")

    rows = client.get(f"/api/projects/{source}/move-targets", headers=alice).json()
    ids = [r["id"] for r in rows]

    assert owned in ids
    assert source not in ids, "the source is never its own destination"
    assert granted not in ids, "a grant is not ownership"


def test_move_targets_reports_task_counts(client, alice):
    source = _project(client, alice, "Source")
    target = _project(client, alice, "Target")
    _task(client, alice, target, "a.jpg")
    _task(client, alice, target, "b.jpg")

    rows = client.get(f"/api/projects/{source}/move-targets", headers=alice).json()
    row = next(r for r in rows if r["id"] == target)
    assert row["task_count"] == 2


def test_move_targets_requires_owner_on_the_source(client, alice, bob):
    source = _project(client, alice, "S")
    _team_with_grant(client, alice, bob, source, "manager")
    assert client.get(
        f"/api/projects/{source}/move-targets", headers=bob
    ).status_code == 403

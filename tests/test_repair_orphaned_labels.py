"""The orphaned-label repair script (`scripts/repair_orphaned_labels.py`).

An orphaned annotation — `label_id` NULL with the lost id in
`extra._orphanedLabelId` — renders as an unnamed "Object" and **cannot be
repaired by moving the task**: `formats.label_reconcile.used_label_ids` only
considers rows with a non-NULL `label_id`, so a move steps straight past them.
This script is the only recovery path, so what it refuses to touch matters as
much as what it fixes.

Background: `.devnotes/move-task-feature/07_DRAFT_STALENESS.md`.
"""
import json

import pytest

import models
from database import SessionLocal
from scripts.repair_orphaned_labels import (
    REPAIR_CLIENT_SENTINEL,
    _labels_by_project,
    _orphaned_id,
    _plan,
)
from tests.conftest import unique_label_id


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _project(db, name):
    p = models.Project(name=name, slug=name, type="detection", status="New")
    db.add(p)
    db.flush()
    return p


def _label(db, project_id, name, color="#ff0000"):
    l = models.Label(id=unique_label_id(), name=name, color=color, project_id=project_id)
    db.add(l)
    db.flush()
    return l


def _task(db, project_id, description="a.jpg"):
    t = models.Task(project_id=project_id, description=description,
                    image_path="uploads/x.jpg", status="New")
    db.add(t)
    db.flush()
    return t


def _orphan(db, task_id, lost_label_id, extra=None):
    """An annotation in the state the bug leaves behind."""
    payload = dict(extra or {})
    payload["_orphanedLabelId"] = lost_label_id
    row = models.Annotation(
        id=unique_label_id("ann"), task_id=task_id, label_id=None,
        type="polygon", seq=0, extra=json.dumps(payload),
    )
    db.add(row)
    db.flush()
    return row


def _run(db, tasks):
    """The script's planning pass over `tasks`."""
    rows_by_task = {}
    for t in tasks:
        rows = [
            r for r in db.query(models.Annotation).filter(
                models.Annotation.task_id == t.id,
                models.Annotation.label_id.is_(None),
            ) if _orphaned_id(r)
        ]
        if rows:
            rows_by_task[t.id] = rows
    return _plan(db, rows_by_task, _labels_by_project(db), {t.id: t for t in tasks})


# --- detection ---------------------------------------------------------------


def test_only_null_labelled_rows_carrying_provenance_are_orphans(db):
    src = _project(db, "src")
    lost = _label(db, src.id, "car")
    task = _task(db, src.id)

    orphan = _orphan(db, task.id, lost.id)
    healthy = models.Annotation(id=unique_label_id("ann"), task_id=task.id,
                                label_id=lost.id, type="rect", seq=1)
    no_extra = models.Annotation(id=unique_label_id("ann"), task_id=task.id,
                                 label_id=None, type="comment", seq=2)
    db.add_all([healthy, no_extra])
    db.flush()

    assert _orphaned_id(orphan) == lost.id
    # A labelled row is never an orphan, even if it somehow carries the key.
    assert _orphaned_id(healthy) is None
    # A comment carries no labelId and no provenance — not damage, just a comment.
    assert _orphaned_id(no_extra) is None


def test_unparseable_extra_is_not_mistaken_for_an_orphan(db):
    src = _project(db, "src")
    task = _task(db, src.id)
    row = models.Annotation(id=unique_label_id("ann"), task_id=task.id, label_id=None,
                            type="rect", seq=0, extra="{not json")
    db.add(row)
    db.flush()
    assert _orphaned_id(row) is None


# --- repair ------------------------------------------------------------------


def test_repairs_by_class_name_into_the_tasks_current_project(db):
    """The headline case: task 1229."""
    src = _project(db, "annotation")
    dst = _project(db, "moved")
    lost = _label(db, src.id, "Rust Area", color="#D95319")
    target = _label(db, dst.id, "Rust Area", color="#D95319")

    task = _task(db, dst.id)          # already moved; its rows were orphaned
    _orphan(db, task.id, lost.id)

    repairs, skips = _run(db, [task])
    assert skips == []
    assert len(repairs) == 1
    _row, _task_, old_id, name, resolved = repairs[0]
    assert (old_id, name, resolved.id) == (lost.id, "Rust Area", target.id)


def test_name_matching_is_case_insensitive(db):
    src = _project(db, "src")
    dst = _project(db, "dst")
    lost = _label(db, src.id, "Rust Area")
    target = _label(db, dst.id, "rust area")
    task = _task(db, dst.id)
    _orphan(db, task.id, lost.id)

    repairs, skips = _run(db, [task])
    assert not skips
    assert repairs[0][4].id == target.id


def test_a_healthy_annotation_is_never_touched(db):
    """The repair must be invisible to rows that were never damaged."""
    dst = _project(db, "dst")
    good = _label(db, dst.id, "car")
    task = _task(db, dst.id)
    row = models.Annotation(id=unique_label_id("ann"), task_id=task.id,
                            label_id=good.id, type="rect", seq=0)
    db.add(row)
    db.flush()

    repairs, skips = _run(db, [task])
    assert (repairs, skips) == ([], [])


# --- what it refuses to do ---------------------------------------------------


def test_a_class_deleted_outright_is_skipped_not_guessed(db):
    """Task 23's shape: the lost class no longer exists anywhere.

    There is no name to match on, so no repair is possible. Inventing one would
    silently relabel someone's work.
    """
    dst = _project(db, "dst")
    _label(db, dst.id, "car")
    task = _task(db, dst.id)
    _orphan(db, task.id, "a-class-that-was-deleted")

    repairs, skips = _run(db, [task])
    assert repairs == []
    assert len(skips) == 1
    assert "no longer exists" in skips[0][2]


def test_a_name_the_destination_lacks_is_skipped(db):
    src = _project(db, "src")
    dst = _project(db, "dst")
    lost = _label(db, src.id, "forklift")
    _label(db, dst.id, "car")          # a different class set entirely
    task = _task(db, dst.id)
    _orphan(db, task.id, lost.id)

    repairs, skips = _run(db, [task])
    assert repairs == []
    assert "no class named" in skips[0][2]


def test_an_ambiguous_name_is_skipped_rather_than_resolved_by_luck(db):
    """Two destination classes differing only by case cannot be chosen between.

    Picking either would be a coin flip decided by iteration order, on somebody
    else's annotations.
    """
    src = _project(db, "src")
    dst = _project(db, "dst")
    lost = _label(db, src.id, "Car")
    _label(db, dst.id, "Car")
    _label(db, dst.id, "car")
    task = _task(db, dst.id)
    _orphan(db, task.id, lost.id)

    repairs, skips = _run(db, [task])
    assert repairs == []
    assert "ambiguous" in skips[0][2]


def test_a_partly_repairable_task_reports_both_halves(db):
    """One bad row must not cost the good ones their repair, or vice versa."""
    src = _project(db, "src")
    dst = _project(db, "dst")
    lost = _label(db, src.id, "Rust Area")
    _label(db, dst.id, "Rust Area")
    task = _task(db, dst.id)
    _orphan(db, task.id, lost.id)
    _orphan(db, task.id, "deleted-long-ago")

    repairs, skips = _run(db, [task])
    assert len(repairs) == 1
    assert len(skips) == 1


def test_provenance_is_preserved_by_default(db):
    """`_orphanedLabelId` stays unless --clear-provenance is passed.

    The row's history is the only record that it was ever damaged, and it costs
    nothing to keep — `row_to_dict` only surfaces it when `labelId` is absent,
    so a repaired row never shows it to a client.
    """
    src = _project(db, "src")
    dst = _project(db, "dst")
    lost = _label(db, src.id, "Rust Area")
    _label(db, dst.id, "Rust Area")
    task = _task(db, dst.id)
    row = _orphan(db, task.id, lost.id, extra={"author": "kushal"})

    repairs, _ = _run(db, [task])
    # The planner does not mutate; applying is the script's --commit branch.
    assert repairs
    extra = json.loads(row.extra)
    assert extra["_orphanedLabelId"] == lost.id
    assert extra["author"] == "kushal", "unrelated extra keys must survive"


def test_the_repair_sentinel_cannot_collide_with_a_browser_client_id(db):
    """Tabs use a uuid4; the sentinel must never look like one."""
    assert REPAIR_CLIENT_SENTINEL == "server:repaired"
    assert ":" in REPAIR_CLIENT_SENTINEL
    assert len(REPAIR_CLIENT_SENTINEL) != 36

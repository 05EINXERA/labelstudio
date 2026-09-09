"""Class reconciliation for a cross-project move (`formats/label_reconcile.py`).

These are unit tests over a session — no HTTP — because the reconciliation is
the part of the move that can silently corrupt data, and it should be pinned
independently of the endpoint that calls it.

See `.devnotes/move-task-feature/02_DESIGN.md` § 3 and `04_EDGE_CASES.md`.
"""
import json

import pytest

import models
from database import SessionLocal
from formats.label_reconcile import (
    MATCH_ONLY,
    MATCH_OR_CREATE,
    apply_label_map,
    build_label_map,
    used_label_ids,
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
    project = models.Project(name=name, slug=name, type="detection", status="New")
    db.add(project)
    db.flush()
    return project


def _label(db, project_id, name, color="#ff0000"):
    label = models.Label(
        id=unique_label_id(), name=name, color=color, project_id=project_id
    )
    db.add(label)
    db.flush()
    return label


def _task(db, project_id, description="img.jpg"):
    task = models.Task(
        project_id=project_id, description=description,
        image_path="uploads/x.jpg", status="New",
    )
    db.add(task)
    db.flush()
    return task


def _annotation(db, task_id, label_id, ident=None, extra=None):
    row = models.Annotation(
        id=ident or unique_label_id("ann"),
        task_id=task_id,
        label_id=label_id,
        type="rect",
        x=1.0, y=2.0, width=3.0, height=4.0,
        seq=0,
        extra=json.dumps(extra) if extra else None,
    )
    db.add(row)
    db.flush()
    return row


def test_used_label_ids_covers_only_the_moving_tasks(db):
    """The class set considered is the one the moving annotations use.

    Not the source project's whole class set: moving three tasks must not drag
    forty unused classes into the destination.
    """
    source = _project(db, "src")
    used = _label(db, source.id, "car")
    _label(db, source.id, "unused-class")
    moving = _task(db, source.id)
    _annotation(db, moving.id, used.id)

    assert used_label_ids(db, [moving.id]) == [used.id]


def test_match_by_name_reuses_the_destination_label(db):
    """A class that exists in the destination is matched, never duplicated."""
    source = _project(db, "src")
    target = _project(db, "dst")
    src_car = _label(db, source.id, "car", color="#111111")
    dst_car = _label(db, target.id, "car", color="#222222")
    moving = _task(db, source.id)
    _annotation(db, moving.id, src_car.id)

    plan = build_label_map(db, [moving.id], target.id, MATCH_OR_CREATE)

    assert plan.mapping == {src_car.id: dst_car.id}
    assert (plan.matched, plan.created) == (1, 0)
    # The destination's colour wins: its annotators already read it that way.
    assert dst_car.color == "#222222"


def test_match_is_case_insensitive(db):
    """"Car" and "car" are the same class — the importer's rule (M-07)."""
    source = _project(db, "src")
    target = _project(db, "dst")
    src = _label(db, source.id, "Car")
    dst = _label(db, target.id, "car")
    moving = _task(db, source.id)
    _annotation(db, moving.id, src.id)

    plan = build_label_map(db, [moving.id], target.id, MATCH_OR_CREATE)

    assert plan.mapping == {src.id: dst.id}
    assert plan.created == 0


def test_match_or_create_creates_the_missing_class(db):
    """An unmatched class is created in the destination, keeping its colour."""
    source = _project(db, "src")
    target = _project(db, "dst")
    src = _label(db, source.id, "pedestrian", color="#00ff00")
    moving = _task(db, source.id)
    _annotation(db, moving.id, src.id)

    plan = build_label_map(db, [moving.id], target.id, MATCH_OR_CREATE)

    assert plan.created == 1
    new_id = plan.mapping[src.id]
    assert new_id is not None and new_id != src.id
    created = db.get(models.Label, new_id)
    assert (created.name, created.color, created.project_id) == (
        "pedestrian", "#00ff00", target.id,
    )
    # The source label is untouched — the class still exists where it was.
    assert db.get(models.Label, src.id) is not None


def test_two_source_classes_differing_by_case_merge_onto_one(db):
    """M-08: they collapse to a single destination label, and it is reported."""
    source = _project(db, "src")
    target = _project(db, "dst")
    a = _label(db, source.id, "Car")
    b = _label(db, source.id, "car")
    moving = _task(db, source.id)
    _annotation(db, moving.id, a.id)
    _annotation(db, moving.id, b.id)

    plan = build_label_map(db, [moving.id], target.id, MATCH_OR_CREATE)

    assert plan.created == 1
    assert plan.mapping[a.id] == plan.mapping[b.id]
    assert plan.merged_names


def test_relabel_is_scoped_to_the_moving_tasks(db):
    """The single most dangerous statement in the feature.

    A task staying behind in the source project, using the same class, must keep
    its original label id. An UPDATE keyed on `label_id` alone would rewrite it.
    """
    source = _project(db, "src")
    target = _project(db, "dst")
    car = _label(db, source.id, "car")

    moving = _task(db, source.id, "moving.jpg")
    staying = _task(db, source.id, "staying.jpg")
    moved_ann = _annotation(db, moving.id, car.id)
    stayed_ann = _annotation(db, staying.id, car.id)

    plan = build_label_map(db, [moving.id], target.id, MATCH_OR_CREATE)
    apply_label_map(db, [moving.id], plan)
    db.flush()
    db.refresh(moved_ann)
    db.refresh(stayed_ann)

    assert moved_ann.label_id == plan.mapping[car.id]
    assert stayed_ann.label_id == car.id


def test_match_only_orphans_the_unmatched_class(db):
    """M-11: label_id is NULLed, the original preserved in `extra`."""
    source = _project(db, "src")
    target = _project(db, "dst")
    src = _label(db, source.id, "forklift")
    moving = _task(db, source.id)
    ann = _annotation(db, moving.id, src.id)

    plan = build_label_map(db, [moving.id], target.id, MATCH_ONLY)
    assert plan.mapping == {src.id: None}
    assert plan.created == 0
    assert plan.unmatched_names == ["forklift"]

    apply_label_map(db, [moving.id], plan)
    db.flush()
    db.refresh(ann)

    assert ann.label_id is None
    assert json.loads(ann.extra)["_orphanedLabelId"] == src.id
    # No class row leaked into the destination.
    assert db.query(models.Label).filter(
        models.Label.project_id == target.id).count() == 0


def test_match_only_preserves_an_existing_orphan_record(db):
    """A row already carrying `_orphanedLabelId` keeps the older provenance."""
    source = _project(db, "src")
    target = _project(db, "dst")
    src = _label(db, source.id, "forklift")
    moving = _task(db, source.id)
    ann = _annotation(
        db, moving.id, src.id, extra={"_orphanedLabelId": "from-an-earlier-life"}
    )

    plan = build_label_map(db, [moving.id], target.id, MATCH_ONLY)
    apply_label_map(db, [moving.id], plan)
    db.flush()
    db.refresh(ann)

    assert json.loads(ann.extra)["_orphanedLabelId"] == "from-an-earlier-life"


def test_annotations_without_a_label_are_untouched(db):
    """M-10: comments and untyped shapes carry no labelId and travel as they are."""
    source = _project(db, "src")
    target = _project(db, "dst")
    moving = _task(db, source.id)
    comment = _annotation(db, moving.id, None)

    plan = build_label_map(db, [moving.id], target.id, MATCH_OR_CREATE)
    assert plan.mapping == {}
    assert apply_label_map(db, [moving.id], plan) == 0

    db.refresh(comment)
    assert comment.label_id is None
    assert comment.extra is None


def test_second_move_of_the_same_class_creates_nothing_new(db):
    """Once the class exists in the destination, later moves match it."""
    source = _project(db, "src")
    target = _project(db, "dst")
    car = _label(db, source.id, "car")
    first = _task(db, source.id, "a.jpg")
    second = _task(db, source.id, "b.jpg")
    _annotation(db, first.id, car.id)
    _annotation(db, second.id, car.id)

    first_plan = build_label_map(db, [first.id], target.id, MATCH_OR_CREATE)
    apply_label_map(db, [first.id], first_plan)
    db.flush()

    second_plan = build_label_map(db, [second.id], target.id, MATCH_OR_CREATE)

    assert second_plan.created == 0
    assert second_plan.matched == 1
    assert second_plan.mapping[car.id] == first_plan.mapping[car.id]


def test_unknown_strategy_is_rejected(db):
    source = _project(db, "src")
    target = _project(db, "dst")
    with pytest.raises(ValueError):
        build_label_map(db, [], target.id, "whatever")

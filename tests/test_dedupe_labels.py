"""The duplicate-class merge script (`scripts/dedupe_labels.py`).

It merges several `labels` rows that normalise to one name down to a single
row, moving their annotations rather than deleting them — which is the property
that most needs pinning, because the destructive sibling
(`purge_annotations_for_labels`) is one import away and deletes exactly what
this must preserve.

Background: `.devnotes/fix-class-creation/01_AUDIT.md`. The script is the
prerequisite for the unique index on (project_id, name), which cannot be
created while duplicates exist.
"""
import pytest

import models
from database import SessionLocal
from scripts.dedupe_labels import _groups, _plan, _affected_task_ids
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


def _ann(db, task_id, label_id, seq=0):
    row = models.Annotation(
        id=unique_label_id("ann"), task_id=task_id, label_id=label_id,
        type="polygon", seq=seq,
    )
    db.add(row)
    db.flush()
    return row


# --- grouping ---------------------------------------------------------------

def test_the_incident_shape_is_detected(db):
    """Six rows named "object" in one project, as project 410 had."""
    p = _project(db, "dedupe-incident")
    rows = [_label(db, p.id, "object") for _ in range(6)]

    groups = _groups(db, [p.id])
    assert list(groups) == [(p.id, "object")]
    assert len(groups[(p.id, "object")]) == 6
    assert {l.id for l in groups[(p.id, "object")]} == {l.id for l in rows}


def test_casing_and_underscore_variants_group_together(db):
    p = _project(db, "dedupe-variants")
    for name in ["Rust Area", "rust area", "RUST_AREA", "  Rust_Area  "]:
        _label(db, p.id, name)

    groups = _groups(db, [p.id])
    assert list(groups) == [(p.id, "rust area")]
    assert len(groups[(p.id, "rust area")]) == 4


def test_distinct_names_are_not_grouped(db):
    p = _project(db, "dedupe-distinct")
    _label(db, p.id, "car")
    _label(db, p.id, "truck")
    assert _groups(db, [p.id]) == {}


def test_the_same_name_in_two_projects_is_not_a_duplicate(db):
    """Labels are per project; merging across them would be data loss."""
    a = _project(db, "dedupe-pa")
    b = _project(db, "dedupe-pb")
    _label(db, a.id, "object")
    _label(db, b.id, "object")
    assert _groups(db, [a.id, b.id]) == {}


# --- winner selection -------------------------------------------------------

def test_the_most_referenced_row_wins(db):
    p = _project(db, "dedupe-winner")
    t = _task(db, p.id)
    few = _label(db, p.id, "object")
    many = _label(db, p.id, "Object")

    _ann(db, t.id, few.id, seq=0)
    for i in range(5):
        _ann(db, t.id, many.id, seq=i + 1)

    plan = _plan(db, _groups(db, [p.id]))
    assert len(plan) == 1
    assert plan[0]["winner"].id == many.id
    assert plan[0]["winner_usage"] == 5
    assert [l.id for l, _ in plan[0]["losers"]] == [few.id]
    assert plan[0]["moving"] == 1


def test_ties_are_broken_deterministically(db):
    """A dry run must predict exactly what --commit will do."""
    p = _project(db, "dedupe-tie")
    rows = [_label(db, p.id, "object") for _ in range(4)]

    first = _plan(db, _groups(db, [p.id]))[0]["winner"].id
    second = _plan(db, _groups(db, [p.id]))[0]["winner"].id
    assert first == second
    assert first == min(l.id for l in rows), "tie not broken by lowest id"


def test_all_unused_duplicates_still_resolve_to_one(db):
    """Project 410's likely case: six rows, none of them ever drawn with."""
    p = _project(db, "dedupe-unused")
    rows = [_label(db, p.id, "object") for _ in range(6)]

    plan = _plan(db, _groups(db, [p.id]))[0]
    assert plan["winner_usage"] == 0
    assert plan["moving"] == 0
    assert len(plan["losers"]) == 5
    assert plan["winner"].id in {l.id for l in rows}


def test_the_canonical_name_is_what_the_winner_will_be_renamed_to(db):
    p = _project(db, "dedupe-canon")
    _label(db, p.id, "Rust_Area")
    _label(db, p.id, "RUST AREA")

    plan = _plan(db, _groups(db, [p.id]))[0]
    assert plan["name"] == "rust area"


# --- what must never happen -------------------------------------------------

def test_every_annotation_is_accounted_for(db):
    """The merge must conserve annotations. It is not a purge."""
    p = _project(db, "dedupe-conserve")
    t = _task(db, p.id)
    a = _label(db, p.id, "object")
    b = _label(db, p.id, "Object")
    c = _label(db, p.id, "OBJECT")

    for i in range(3):
        _ann(db, t.id, a.id, seq=i)
    for i in range(2):
        _ann(db, t.id, b.id, seq=10 + i)
    _ann(db, t.id, c.id, seq=20)

    plan = _plan(db, _groups(db, [p.id]))[0]
    total = plan["winner_usage"] + plan["moving"]
    assert total == 6, "annotations would be lost by the merge"


def test_affected_tasks_span_every_task_using_a_loser(db):
    """Each such task needs its sentinel, or an open tab saves the old ids back."""
    p = _project(db, "dedupe-tasks")
    t1, t2, t3 = _task(db, p.id, "a.jpg"), _task(db, p.id, "b.jpg"), _task(db, p.id, "c.jpg")
    # `keep` must be the most-referenced row, or it is not the one that wins —
    # the script picks by annotation count, not by which variable we named it.
    keep = _label(db, p.id, "object")
    lose = _label(db, p.id, "Object")

    _ann(db, t3.id, keep.id, seq=0)
    _ann(db, t3.id, keep.id, seq=1)
    _ann(db, t3.id, keep.id, seq=2)   # t3 uses only the winner: untouched
    _ann(db, t1.id, lose.id)
    _ann(db, t2.id, lose.id)

    plan = _plan(db, _groups(db, [p.id]))[0]
    loser_ids = [l.id for l, _ in plan["losers"]]
    affected = _affected_task_ids(db, loser_ids)

    assert affected == {t1.id, t2.id}
    assert t3.id not in affected, "a task that does not change must not be stamped"


def test_labels_with_no_project_are_skipped(db):
    """`labels.project_id` is nullable and the dev database has such rows.

    Bucketing them would put every project-less label under one (None, name)
    key, so a merge would collapse classes from unrelated projects together.
    Caught by running the script against the dev database, not by review.
    """
    orphan_a = _label(db, None, "object")
    orphan_b = _label(db, None, "object")

    groups = _groups(db, None)
    assert (None, "object") not in groups
    for rows in groups.values():
        assert orphan_a.id not in {l.id for l in rows}
        assert orphan_b.id not in {l.id for l in rows}


def test_a_project_with_no_duplicates_plans_nothing(db):
    p = _project(db, "dedupe-clean")
    _label(db, p.id, "car")
    assert _plan(db, _groups(db, [p.id])) == []

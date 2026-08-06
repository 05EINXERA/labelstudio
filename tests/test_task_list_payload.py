"""Tests for the lean task list and server-side counts (T3 / findings F1, F6).

The Tasks view needs two integers per row — a comment count and a count of
distinct classes used — and used to obtain them by downloading every task's full
annotation blob and counting in the browser. On a 120-task project that was
~8.9 MB of payload to render two columns
(.devnotes/server-optimization/03_TASKS_PAGE.md).

The counts now come from the server, so `include_annotations=false` can stay
lean. What these tests protect:

- the lean response carries the counts and *not* the blobs, and
- the counts agree with what counting the blobs client-side would have produced,
  since the whole change is only safe if the numbers are identical.
"""
import json

import pytest


def _project(client, headers, name="counts"):
    res = client.post("/api/projects",
                      json={"name": name, "slug": name, "creator": "x"},
                      headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _task(client, headers, project_id, annotations=None):
    payload = {"description": "img.png", "status": "New"}
    if annotations is not None:
        payload["annotations"] = json.dumps(annotations)
    res = client.post(f"/api/tasks?projectId={project_id}", json=payload, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _rows(client, headers, project_id, include_annotations):
    res = client.get(
        f"/api/tasks?projectId={project_id}"
        f"&include_annotations={'true' if include_annotations else 'false'}",
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res.json()


# A blob with a bit of everything: two comments, three labelled shapes across
# two distinct labels, and one shape with no label at all.
_MIXED = [
    {"type": "comment", "text": "look here"},
    {"type": "comment", "text": "and here"},
    {"type": "box", "labelId": "cat"},
    {"type": "box", "labelId": "cat"},
    {"type": "polygon", "labelId": "dog"},
    {"type": "box"},
]


def test_lean_list_omits_annotation_blobs(client, alice):
    """The point of the flag: contents must not be shipped."""
    pid = _project(client, alice)
    _task(client, alice, pid, _MIXED)

    (row,) = _rows(client, alice, pid, include_annotations=False)
    assert row["annotations"] == []


def test_lean_list_reports_counts(client, alice):
    """Two comments; two distinct labels across three labelled shapes."""
    pid = _project(client, alice)
    _task(client, alice, pid, _MIXED)

    (row,) = _rows(client, alice, pid, include_annotations=False)
    assert row["comment_count"] == 2
    assert row["class_count"] == 2


def test_counts_match_between_lean_and_full_responses(client, alice):
    """The two branches compute counts by different routes; they must agree.

    This is the real safety property. The lean branch derives counts from a
    separate narrow query, the full branch from the already-parsed list — an
    easy place for the two to drift.
    """
    pid = _project(client, alice)
    _task(client, alice, pid, _MIXED)
    _task(client, alice, pid, [{"type": "comment"}])
    _task(client, alice, pid, [])

    lean = {r["id"]: (r["comment_count"], r["class_count"])
            for r in _rows(client, alice, pid, include_annotations=False)}
    full = {r["id"]: (r["comment_count"], r["class_count"])
            for r in _rows(client, alice, pid, include_annotations=True)}
    assert lean == full


def test_counts_match_naive_client_side_tally(client, alice):
    """Counts must equal what the old browser-side logic produced.

    Mirrors the pre-change `countAnnotations` implementation exactly, so a
    subtle difference in what "a class" means shows up here rather than as a
    changed number in the UI.
    """
    pid = _project(client, alice)
    _task(client, alice, pid, _MIXED)

    (full,) = _rows(client, alice, pid, include_annotations=True)
    anns = full["annotations"]
    expected_comments = len([a for a in anns if a.get("type") == "comment"])
    expected_classes = len({a["labelId"] for a in anns if a.get("labelId")})

    assert full["comment_count"] == expected_comments
    assert full["class_count"] == expected_classes


@pytest.mark.parametrize(
    "annotations, comments, classes",
    [
        ([], 0, 0),
        ([{"type": "comment"}], 1, 0),
        ([{"type": "box", "labelId": "a"}], 0, 1),
        # The same label twice is one distinct class, not two.
        ([{"type": "box", "labelId": "a"}, {"type": "box", "labelId": "a"}], 0, 1),
        # A shape with no labelId contributes to neither count.
        ([{"type": "box"}], 0, 0),
    ],
)
def test_count_edge_cases(client, alice, annotations, comments, classes):
    pid = _project(client, alice)
    _task(client, alice, pid, annotations)

    (row,) = _rows(client, alice, pid, include_annotations=False)
    assert (row["comment_count"], row["class_count"]) == (comments, classes)


def test_task_with_no_annotations_reports_zero(client, alice):
    """A never-annotated task has NULL in the column, not an empty array."""
    pid = _project(client, alice)
    _task(client, alice, pid, annotations=None)

    (row,) = _rows(client, alice, pid, include_annotations=False)
    assert row["comment_count"] == 0
    assert row["class_count"] == 0


def test_unparseable_annotations_do_not_break_the_list(client, alice):
    """One corrupt blob must not fail the whole view.

    Matches the tolerance every other reader of this column already has.
    """
    pid = _project(client, alice)
    task_id = _task(client, alice, pid, [{"type": "comment"}])

    import models
    from database import SessionLocal

    db = SessionLocal()
    try:
        db.query(models.Task).filter(models.Task.id == task_id).update(
            {models.Task.annotations: "{not json at all"}
        )
        db.commit()
    finally:
        db.close()

    (row,) = _rows(client, alice, pid, include_annotations=False)
    assert row["comment_count"] == 0
    assert row["class_count"] == 0


def test_counts_are_isolated_per_task(client, alice):
    """Counts must be keyed correctly; a shared tally would be invisible above."""
    pid = _project(client, alice)
    a = _task(client, alice, pid, [{"type": "comment"}, {"type": "comment"}])
    b = _task(client, alice, pid, [{"type": "box", "labelId": "x"}])

    rows = {r["id"]: r for r in _rows(client, alice, pid, include_annotations=False)}
    assert (rows[a]["comment_count"], rows[a]["class_count"]) == (2, 0)
    assert (rows[b]["comment_count"], rows[b]["class_count"]) == (0, 1)


# --- label usage (the Classes view) ---------------------------------------


def test_label_usage_counts_annotations_per_label(client, alice):
    pid = _project(client, alice)
    _task(client, alice, pid, [
        {"type": "box", "labelId": "cat"},
        {"type": "box", "labelId": "cat"},
        {"type": "polygon", "labelId": "dog"},
        {"type": "comment"},
    ])

    res = client.get(f"/api/labels/usage?projectId={pid}", headers=alice)
    assert res.status_code == 200, res.text
    assert res.json() == {"cat": 2, "dog": 1}


def test_label_usage_spans_tasks(client, alice):
    pid = _project(client, alice)
    _task(client, alice, pid, [{"type": "box", "labelId": "cat"}])
    _task(client, alice, pid, [{"type": "box", "labelId": "cat"}])

    res = client.get(f"/api/labels/usage?projectId={pid}", headers=alice)
    assert res.json()["cat"] == 2


def test_label_usage_is_empty_for_unannotated_project(client, alice):
    pid = _project(client, alice)
    _task(client, alice, pid, annotations=None)

    res = client.get(f"/api/labels/usage?projectId={pid}", headers=alice)
    assert res.status_code == 200
    assert res.json() == {}


def test_label_usage_route_is_not_shadowed_by_label_id(client, alice):
    """`/usage` is a literal path declared before `/{label_id}`.

    Registered in the wrong order it would be matched as a label id and 404 or
    422 instead of returning counts — a failure mode that only shows up at
    runtime.
    """
    pid = _project(client, alice)
    res = client.get(f"/api/labels/usage?projectId={pid}", headers=alice)
    assert res.status_code == 200
    assert isinstance(res.json(), dict)


def test_label_usage_requires_project_access(client, alice, bob):
    """Same 404-not-403 contract as every other project-scoped read."""
    pid = _project(client, alice)
    _task(client, alice, pid, [{"type": "box", "labelId": "cat"}])

    res = client.get(f"/api/labels/usage?projectId={pid}", headers=bob)
    assert res.status_code == 404


def test_lean_list_still_requires_project_access(client, alice, bob):
    """The payload change must not have widened who can read the list."""
    pid = _project(client, alice)
    _task(client, alice, pid, _MIXED)

    res = client.get(
        f"/api/tasks?projectId={pid}&include_annotations=false", headers=bob
    )
    assert res.status_code == 404

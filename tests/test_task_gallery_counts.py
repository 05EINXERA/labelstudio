"""Regression tests for the gallery's per-task comment and class counts.

`GET /api/tasks` reports `comment_count` and `class_count` for every task on the
page. Both used to be separate aggregate queries; they were merged into a single
grouped pass (one heap fetch instead of two) because every open gallery re-polls
this endpoint every 30s. These tests pin the *values*, so the merge — and any
future rewrite of it — cannot silently change what the gallery displays.

The interesting cases are the ones a naive FILTER/DISTINCT merge gets wrong:
a task whose only annotations are comments, a task with repeated labels (the
class count is distinct, not a row count), and annotations with a NULL label_id.
"""
import uuid

import models
from database import SessionLocal
from conftest import unique_label_id


def _make_project(client, headers, name="Gallery Counts"):
    res = client.post(
        "/api/projects",
        json={"name": name, "slug": "gallery-counts", "creator": "tester"},
        headers=headers,
    )
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


def _seed(project_id, spec):
    """Create tasks with annotations directly, returning {key: task_id}.

    Going through the ORM rather than the save endpoint keeps these tests about
    the read path: the wipe guard and client_id conflict model on POST /api/tasks
    have their own tests and would only add noise here.
    """
    task_ids = {}
    with SessionLocal() as db:
        label_a = models.Label(
            id=unique_label_id(), project_id=project_id, name="Cat", color="#ff0000"
        )
        label_b = models.Label(
            id=unique_label_id(), project_id=project_id, name="Dog", color="#00ff00"
        )
        db.add_all([label_a, label_b])
        db.flush()
        labels = {"a": label_a.id, "b": label_b.id}

        for key, annotations in spec.items():
            task = models.Task(
                project_id=project_id,
                image_path=f"{key}.jpg",
                status="New",
                time_spent=0,
            )
            db.add(task)
            db.flush()
            task_ids[key] = task.id
            for ann_type, label_key in annotations:
                db.add(
                    models.Annotation(
                        id=str(uuid.uuid4()),
                        task_id=task.id,
                        type=ann_type,
                        label_id=labels[label_key] if label_key else None,
                    )
                )
        db.commit()
    return task_ids


def _counts_by_task(client, headers, project_id):
    res = client.get(f"/api/tasks?projectId={project_id}&limit=50", headers=headers)
    assert res.status_code == 200, res.text
    return {
        item["id"]: (item["comment_count"], item["class_count"])
        for item in res.json()["items"]
    }


def test_gallery_counts_separate_comments_from_classes(client, alice):
    """Comments never count as classes, and distinct labels are counted once."""
    project_id = _make_project(client, alice)
    task_ids = _seed(
        project_id,
        {
            # Two labels used across three polygons -> 2 distinct classes.
            "mixed": [
                ("polygon", "a"),
                ("polygon", "a"),
                ("polygon", "b"),
                ("comment", None),
            ],
            # Comments only: no classes at all.
            "comments_only": [("comment", None), ("comment", None)],
            # Polygons only: no comments.
            "polygons_only": [("polygon", "b")],
            # An unlabelled polygon must not count as a class.
            "unlabelled": [("polygon", None)],
        },
    )

    counts = _counts_by_task(client, alice, project_id)

    assert counts[task_ids["mixed"]] == (1, 2)
    assert counts[task_ids["comments_only"]] == (2, 0)
    assert counts[task_ids["polygons_only"]] == (0, 1)
    assert counts[task_ids["unlabelled"]] == (0, 0)


def test_gallery_counts_zero_for_task_without_annotations(client, alice):
    """A task with no annotations reports zeros rather than being omitted."""
    project_id = _make_project(client, alice)
    task_ids = _seed(project_id, {"empty": []})

    counts = _counts_by_task(client, alice, project_id)

    assert counts[task_ids["empty"]] == (0, 0)

"""Exports and imports keep every value exactly: the two changes that touch data.

.devnotes/fix-exports-imports/ changed two things that sit next to the data:

1. **Compact JSON.** The COCO and single-file annotations JSON bodies are now
   written with `separators=(",", ":")` instead of `indent=2`. The claim is that
   this removes whitespace *between* tokens and nothing else: every key,
   string and number is byte-for-byte what the old export wrote. These tests
   rebuild the old `indent=2` body from the same database, strip its
   whitespace the way json does, and demand byte equality with the new body,
   then round-trip an export through an import and demand the geometry comes
   back identical.

2. **No blob write on import.** Imports no longer write the legacy
   `Task.annotations` blob (CLAUDE.md rule 11b); the annotation rows are the
   only copy. So a failed row write must now fail the import and change
   nothing, where before it was logged and swallowed.
"""
import json

import models
from api.export_service import COMPACT
from database import SessionLocal
from formats import annotations_json
from formats import coco as coco_format
from formats.common import annotation_dicts, values_for_labels

from conftest import unique_label_id


def _new_project(client, auth, name="fidp"):
    return client.post("/api/projects", json={"name": name, "slug": name, "creator": "ignored"}, headers=auth).json()["id"]


def _new_label(client, auth, pid, name, color="#3b82f6"):
    lid = unique_label_id("fid")
    client.post("/api/labels", json={"id": lid, "name": name, "color": color, "projectId": pid}, headers=auth)
    return lid


def _new_task(client, auth, pid, description, annotations=None):
    payload = {"description": description, "status": "New"}
    if annotations is not None:
        payload["annotations"] = json.dumps(annotations)
    res = client.post(f"/api/tasks?projectId={pid}", json=payload, headers=auth)
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


def _export_body(client, auth, pid, fmt):
    res = client.post("/api/exports", json={"projectId": pid, "format": fmt}, headers=auth)
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    assert client.get(f"/api/exports/{job_id}", headers=auth).json()["status"] == "completed"
    download = client.get(f"/api/exports/{job_id}/download", headers=auth)
    assert download.status_code == 200
    return download.content


# Coordinates with awkward binary fractions and many decimals: the values a
# careless serialisation change would show up in first. A simple (non-
# crossing) ring, so the import's untangle step leaves it alone.
_AWKWARD = [
    {"x": 0.1, "y": 0.2}, {"x": 1234.5678, "y": 0.30000000000000004},
    {"x": 640.005, "y": 480.015}, {"x": 3.14159265358979, "y": 271.8281828459045},
]


def _seed(client, auth, name):
    pid = _new_project(client, auth, name)
    lid = _new_label(client, auth, pid, "Rust Area")
    other = _new_label(client, auth, pid, "Crack")
    _new_task(client, auth, pid, "a.png", annotations=[
        {"id": "p1", "labelId": lid, "type": "polygon", "points": _AWKWARD},
        {"id": "b1", "labelId": other, "type": "bbox", "x": 10.25, "y": 20.125, "width": 30.5, "height": 40.0625,
         "points": [{"x": 10.25, "y": 20.125}, {"x": 40.75, "y": 20.125},
                    {"x": 40.75, "y": 60.1875}, {"x": 10.25, "y": 60.1875}]},
    ])
    _new_task(client, auth, pid, "b.png", annotations=[
        {"id": "p2", "labelId": lid, "type": "polygon",
         "points": [{"x": 5.5, "y": 5.5}, {"x": 50.05, "y": 5.5}, {"x": 27.777, "y": 44.444}]},
    ])
    return pid


def _load(pid):
    db = SessionLocal()
    tasks = db.query(models.Task).filter(models.Task.project_id == pid).all()
    labels = db.query(models.Label).filter(models.Label.project_id == pid).all()
    return db, tasks, labels


def test_compact_coco_is_the_old_body_minus_whitespace(client, alice):
    pid = _seed(client, alice, "fid-coco")
    new_body = _export_body(client, alice, pid, "coco")

    db, tasks, labels = _load(pid)
    try:
        old_body = json.dumps(coco_format.build(tasks, labels, db=db), indent=2)
    finally:
        db.close()

    # Same parsed value...
    assert json.loads(new_body) == json.loads(old_body)
    # ...and byte-identical once the old body's inter-token whitespace is gone:
    # every number and string is written exactly as before.
    assert new_body.decode("utf-8") == json.dumps(json.loads(old_body), separators=COMPACT)
    assert len(new_body) < len(old_body.encode("utf-8"))


def test_compact_annotations_json_is_the_old_body_minus_whitespace(client, alice):
    pid = _seed(client, alice, "fid-ajson")
    new_body = _export_body(client, alice, pid, "annotations_json")

    db, tasks, labels = _load(pid)
    try:
        by_id = {l.id: l for l in labels}
        values = values_for_labels(labels)
        old_body = json.dumps([annotations_json.task_object(t, by_id, values, db=db) for t in tasks], indent=2)
    finally:
        db.close()

    assert json.loads(new_body) == json.loads(old_body)
    assert new_body.decode("utf-8") == json.dumps(json.loads(old_body), separators=COMPACT)


def test_coco_round_trip_preserves_geometry(client, alice):
    """Export a project, import the file into a fresh project with the same
    images, and get the same shapes back, coordinate for coordinate."""
    src = _seed(client, alice, "fid-rt-src")
    body = _export_body(client, alice, src, "coco")

    dst = _new_project(client, alice, "fid-rt-dst")
    _new_task(client, alice, dst, "a.png")
    _new_task(client, alice, dst, "b.png")
    res = client.post(
        f"/api/imports/annotations?projectId={dst}&mode=replace",
        files={"file": ("export.json", body, "application/json")},
        headers=alice,
    )
    assert res.status_code == 200, res.text
    assert res.json()["annotations_imported"] == 3

    def shapes(pid):
        db = SessionLocal()
        try:
            out = {}
            for t in db.query(models.Task).filter(models.Task.project_id == pid).all():
                labels = {l.id: l.name for l in db.query(models.Label).filter(models.Label.project_id == pid)}
                out[t.description] = sorted(
                    (labels[a["labelId"]], tuple((p["x"], p["y"]) for p in a["points"]))
                    for a in annotation_dicts(t)
                )
            return out
        finally:
            db.close()

    source, imported = shapes(src), shapes(dst)
    # COCO stores coordinates at 2 dp (formats.common.round2), so the source
    # is compared at that precision; the imported copy must match it exactly.
    rounded = {k: sorted((n, tuple((round(x, 2), round(y, 2)) for x, y in pts)) for n, pts in v)
               for k, v in source.items()}
    assert imported == rounded


def _task(pid, description):
    db = SessionLocal()
    try:
        task = db.query(models.Task).filter(models.Task.project_id == pid,
                                            models.Task.description == description).one()
        return task.id, task.annotations, annotation_dicts(task)
    finally:
        db.close()


_COCO = json.dumps({
    "images": [{"id": 1, "file_name": "a.png", "width": 100, "height": 100}],
    "categories": [{"id": 1, "name": "Crack"}],
    "annotations": [{"id": 1, "image_id": 1, "category_id": 1,
                     "segmentation": [[1.25, 2.5, 30.75, 2.5, 30.75, 40.125]], "bbox": [1.25, 2.5, 29.5, 37.625]}],
}).encode()


def test_import_writes_rows_and_leaves_the_legacy_blob_alone(client, alice):
    pid = _new_project(client, alice, "fid-blob")
    lid = _new_label(client, alice, pid, "Crack")
    _new_task(client, alice, pid, "a.png", annotations=[
        {"id": "keep", "labelId": lid, "type": "polygon",
         "points": [{"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 5, "y": 5}]},
    ])
    tid, blob_before, rows_before = _task(pid, "a.png")

    res = client.post(f"/api/imports/annotations?projectId={pid}&mode=merge",
                      files={"file": ("c.json", _COCO, "application/json")}, headers=alice)
    assert res.status_code == 200, res.text

    _, blob_after, rows_after = _task(pid, "a.png")
    assert blob_after == blob_before, "the import must not touch the legacy blob"
    assert [a["id"] for a in rows_after][:1] == ["keep"]
    assert len(rows_after) == len(rows_before) + 1
    imported = rows_after[-1]
    # COCO import stores coordinates at 2 dp (formats.common.round2), as it
    # always has: 40.125 arrives as 40.12.
    assert [(p["x"], p["y"]) for p in imported["points"]] == [(1.25, 2.5), (30.75, 2.5), (30.75, 40.12)]
    # And the API a client reads from agrees with the rows.
    api_anns = client.get(f"/api/tasks/{tid}", headers=alice).json()["annotations"]
    if isinstance(api_anns, str):
        api_anns = json.loads(api_anns)
    assert len(api_anns) == len(rows_after)


def test_a_failed_row_write_fails_the_import_and_changes_nothing(client, alice, monkeypatch):
    """With the blob gone the rows are the only copy, so a failure must not be
    swallowed: the import fails, and no task or label is left half-written."""
    import api.import_service as svc

    pid = _new_project(client, alice, "fid-fail")
    lid = _new_label(client, alice, pid, "Crack")
    _new_task(client, alice, pid, "a.png", annotations=[
        {"id": "keep", "labelId": lid, "type": "polygon",
         "points": [{"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 5, "y": 5}]},
    ])
    _, blob_before, rows_before = _task(pid, "a.png")
    payload = _COCO.replace(b'"Crack"', b'"BrandNewClass"')

    def boom(*args, **kwargs):
        raise RuntimeError("simulated row-write failure")

    monkeypatch.setattr(svc, "sync_task_annotations_for_project", boom)
    res = client.post(f"/api/imports/annotations?projectId={pid}&mode=replace",
                      files={"file": ("c.json", payload, "application/json")}, headers=alice)
    assert res.status_code == 500

    _, blob_after, rows_after = _task(pid, "a.png")
    assert rows_after == rows_before
    assert blob_after == blob_before
    labels = client.get(f"/api/labels?projectId={pid}", headers=alice).json()
    assert "BrandNewClass" not in {l["name"] for l in labels}, "the auto-created label must roll back too"

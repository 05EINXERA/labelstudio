"""LabelMe annotation import, end to end through the endpoints.

The unit-level parse/build coverage is in test_labelme_format.py; this file
covers the wiring — that the new detection branch actually fires ahead of the
task-JSON fall-through, that a ZIP of LabelMe files routes to the JSON walk
rather than being claimed by the YOLO archive check, and that the rows land
through the normal storage path.

Regression: before the detection branch existed these files parsed to {} and
the endpoint answered 422 "No recognizable annotations found" for a document
full of valid polygons. See .devnotes/task-imports-new/fix/01_ANALYSIS.md § 2.
"""
import io
import json
import zipfile

import models
from database import SessionLocal


def _new_project(client, auth, name="lmimp"):
    return client.post("/api/projects", json={"name": name, "slug": name, "creator": "x"},
                       headers=auth).json()["id"]


def _new_task(client, auth, pid, description):
    return client.post("/api/tasks", json={"description": description},
                       params={"projectId": pid}, headers=auth).json()["id"]


def _doc(shapes, image_path):
    return {"version": "5.5.0", "flags": {}, "shapes": shapes,
            "imagePath": image_path, "imageData": None,
            "imageHeight": 1944, "imageWidth": 2592}


def _shape(label, points, shape_type="polygon"):
    return {"label": label, "points": points, "group_id": None,
            "description": "", "shape_type": shape_type, "flags": {}, "mask": None}


_TRIANGLE = [[10, 10], [40, 10], [25, 35]]
_SQUARE = [[50, 50], [90, 50], [90, 90], [50, 90]]


def _payload(doc):
    return json.dumps(doc, ensure_ascii=False).encode("utf-8")


def _rows(task_id):
    db = SessionLocal()
    try:
        return db.query(models.Annotation).filter(
            models.Annotation.task_id == task_id).all()
    finally:
        db.close()


def _upload(client, auth, pid, body, name="P1.json", mode=None, preview=False):
    path = "/api/imports/annotations/preview" if preview else "/api/imports/annotations"
    url = f"{path}?projectId={pid}" + (f"&mode={mode}" if mode else "")
    return client.post(url, files={"file": (name, body, "application/json")}, headers=auth)


# --- preview ----------------------------------------------------------------

def test_preview_matches_a_labelme_file_to_its_task(client, alice):
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "P1.JPG")

    res = _upload(client, alice, pid,
                  _payload(_doc([_shape("Rust", _TRIANGLE)], "P1.JPG")),
                  preview=True)

    assert res.status_code == 200, res.text
    body = res.json()
    assert [m["filename"] for m in body["matched"]] == ["P1.JPG"]
    assert body["matched"][0]["annotation_count"] == 1
    assert body["unmatched"] == []
    assert body["new_labels"] == ["Rust"]
    assert body["total_annotations"] == 1


def test_preview_writes_nothing(client, alice):
    pid = _new_project(client, alice)
    tid = _new_task(client, alice, pid, "P1.JPG")

    _upload(client, alice, pid, _payload(_doc([_shape("Rust", _TRIANGLE)], "P1.JPG")),
            preview=True)

    assert _rows(tid) == []


def test_preview_reports_unsupported_shapes_in_notes(client, alice):
    """A dropped shape must be visible before the import is applied, not
    discovered afterwards."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "P1.JPG")

    res = _upload(client, alice, pid, _payload(_doc([
        _shape("Rust", _TRIANGLE),
        _shape("Spot", [[5, 5]], shape_type="point"),
    ], "P1.JPG")), preview=True)

    body = res.json()
    assert body["total_annotations"] == 1
    assert any("point" in n for n in body["notes"])


# --- apply ------------------------------------------------------------------

def test_import_writes_annotation_rows(client, alice):
    pid = _new_project(client, alice)
    tid = _new_task(client, alice, pid, "P1.JPG")

    res = _upload(client, alice, pid, _payload(
        _doc([_shape("Rust", _TRIANGLE), _shape("Rust", _SQUARE)], "P1.JPG")))

    assert res.status_code == 200, res.text
    assert res.json()["tasks_updated"] == 1
    assert res.json()["annotations_imported"] == 2
    # Rule 11b: annotations are rows. Assert against the table, not the blob.
    assert len(_rows(tid)) == 2


def test_import_matches_on_stem_when_extensions_differ(client, alice):
    pid = _new_project(client, alice)
    tid = _new_task(client, alice, pid, "P1.jpeg")

    _upload(client, alice, pid, _payload(_doc([_shape("Rust", _TRIANGLE)], "P1.JPG")))

    assert len(_rows(tid)) == 1


def test_merge_keeps_existing_annotations(client, alice):
    pid = _new_project(client, alice)
    tid = _new_task(client, alice, pid, "P1.JPG")

    _upload(client, alice, pid, _payload(_doc([_shape("Rust", _TRIANGLE)], "P1.JPG")),
            mode="merge")
    _upload(client, alice, pid, _payload(_doc([_shape("Rust", _SQUARE)], "P1.JPG")),
            mode="merge")

    assert len(_rows(tid)) == 2


def test_replace_overwrites_existing_annotations(client, alice):
    pid = _new_project(client, alice)
    tid = _new_task(client, alice, pid, "P1.JPG")

    _upload(client, alice, pid, _payload(
        _doc([_shape("Rust", _TRIANGLE), _shape("Rust", _SQUARE)], "P1.JPG")), mode="merge")
    _upload(client, alice, pid, _payload(_doc([_shape("Rust", _TRIANGLE)], "P1.JPG")),
            mode="replace")

    assert len(_rows(tid)) == 1


def test_import_needs_no_image_dimensions(client, alice):
    """LabelMe coordinates are absolute, so unlike YOLO a task whose image size
    is unknown imports rather than landing in `skipped`."""
    pid = _new_project(client, alice)
    tid = _new_task(client, alice, pid, "P1.JPG")  # no image uploaded

    res = _upload(client, alice, pid, _payload(_doc([_shape("Rust", _TRIANGLE)], "P1.JPG")))

    assert res.json()["skipped"] == []
    assert len(_rows(tid)) == 1


def test_unmatched_file_is_reported_not_written(client, alice):
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "P1.JPG")

    res = _upload(client, alice, pid, _payload(_doc([_shape("Rust", _TRIANGLE)], "OTHER.JPG")))

    assert res.json()["tasks_updated"] == 0
    assert [u["filename"] for u in res.json()["unmatched"]] == ["OTHER.JPG"]


# --- labels -----------------------------------------------------------------

def test_import_creates_the_labels_it_needs(client, alice):
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "P1.JPG")

    _upload(client, alice, pid, _payload(_doc([
        _shape("健全箇所", _TRIANGLE), _shape("発錆箇所", _SQUARE)], "P1.JPG")))

    names = {l["name"] for l in client.get(f"/api/labels?projectId={pid}",
                                           headers=alice).json()}
    assert {"健全箇所", "発錆箇所"} <= names


def test_reimporting_does_not_duplicate_labels(client, alice):
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "P1.JPG")
    body = _payload(_doc([_shape("健全箇所", _TRIANGLE)], "P1.JPG"))

    _upload(client, alice, pid, body, mode="replace")
    _upload(client, alice, pid, body, mode="replace")

    names = [l["name"] for l in client.get(f"/api/labels?projectId={pid}",
                                           headers=alice).json()]
    assert names.count("健全箇所") == 1


# --- containers -------------------------------------------------------------

def test_a_zip_of_labelme_files_imports(client, alice):
    """The generic JSON walk must claim this archive. The YOLO check runs first
    and requires .txt labels with no .json, so a LabelMe archive should fall
    past it — asserted here rather than assumed."""
    pid = _new_project(client, alice)
    t1 = _new_task(client, alice, pid, "P1.JPG")
    t2 = _new_task(client, alice, pid, "P2.JPG")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("labels/P1.json", _payload(_doc([_shape("Rust", _TRIANGLE)], "P1.JPG")))
        zf.writestr("labels/P2.json", _payload(_doc([_shape("Rust", _SQUARE)], "P2.JPG")))

    res = client.post(f"/api/imports/annotations?projectId={pid}",
                      files={"file": ("labels.zip", buf.getvalue(), "application/zip")},
                      headers=alice)

    assert res.status_code == 200, res.text
    assert res.json()["tasks_updated"] == 2
    assert len(_rows(t1)) == 1
    assert len(_rows(t2)) == 1


def test_a_zip_entry_with_no_image_path_falls_back_to_its_name(client, alice):
    pid = _new_project(client, alice)
    tid = _new_task(client, alice, pid, "P1.JPG")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("P1.json", _payload(_doc([_shape("Rust", _TRIANGLE)], "")))

    res = client.post(f"/api/imports/annotations?projectId={pid}",
                      files={"file": ("labels.zip", buf.getvalue(), "application/zip")},
                      headers=alice)

    assert res.status_code == 200, res.text
    assert len(_rows(tid)) == 1


# --- the other formats still route correctly --------------------------------

def test_coco_import_is_unaffected(client, alice):
    """The LabelMe branch sits ahead of the task-JSON fall-through; neither of
    the formats around it may change behaviour."""
    pid = _new_project(client, alice)
    tid = _new_task(client, alice, pid, "cat.png")
    coco = json.dumps({
        "images": [{"id": 1, "file_name": "cat.png", "width": 100, "height": 100}],
        "categories": [{"id": 1, "name": "cat"}],
        "annotations": [{"id": 1, "image_id": 1, "category_id": 1,
                         "bbox": [10, 10, 20, 30], "segmentation": []}],
    }).encode()

    res = _upload(client, alice, pid, coco, name="coco.json")

    assert res.json()["tasks_updated"] == 1
    assert len(_rows(tid)) == 1


def test_task_json_import_is_unaffected(client, alice):
    pid = _new_project(client, alice)
    tid = _new_task(client, alice, pid, "dog.png")
    native = json.dumps([{"name": "dog.png", "annotations": [
        {"title": "dog", "points": [10, 10, 30, 10, 30, 40, 10, 40]}]}]).encode()

    res = _upload(client, alice, pid, native, name="export.json")

    assert res.json()["tasks_updated"] == 1
    assert len(_rows(tid)) == 1

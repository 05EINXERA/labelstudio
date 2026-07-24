"""COCO export fidelity (data-refactor plan Phase 2).

Three bugs the previous inline builder shipped, each verified against
the reference export shape:
  - `images` carried hard-coded width/height of 0
  - `area` was the bounding box's area, not the polygon's
  - annotations lacked num_keypoints/keypoints/attributes/rotation
"""
import io
import json
import os

import pytest
from PIL import Image

import models
from conftest import unique_label_id
from database import SessionLocal
from formats import coco as coco_format

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "interop")


def _new_project(client, auth, name="coco"):
    res = client.post("/api/projects", json={"name": name, "slug": name, "creator": "x"}, headers=auth)
    return res.json()["id"]


def _new_label(client, auth, pid, lid, name, color="#ef4444"):
    """Create a label under a globally unique id (see conftest.unique_label_id)."""
    unique = unique_label_id(lid)
    client.post("/api/labels", json={"id": unique, "name": name, "color": color, "projectId": pid}, headers=auth)
    return unique


def _new_task(client, auth, pid, description, annotations=None, status="New"):
    payload = {"description": description, "status": status}
    if annotations is not None:
        payload["annotations"] = json.dumps(annotations)
    res = client.post(f"/api/tasks?projectId={pid}", json=payload, headers=auth)
    assert res.status_code in (200, 201), res.text
    return res.json().get("id")


def _export_coco(client, auth, pid):
    res = client.post("/api/exports", json={"projectId": pid, "format": "json"}, headers=auth)
    job_id = res.json()["job_id"]
    client.get(f"/api/exports/{job_id}", headers=auth)
    return client.get(f"/api/exports/{job_id}/download", headers=auth).json()


def _upload_png(client, auth, pid, filename, width, height):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (1, 2, 3)).save(buf, format="PNG")
    res = client.post(
        f"/api/projects/{pid}/upload",
        files=[("file", (filename, buf.getvalue(), "image/png"))],
        headers=auth,
    )
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# Image dimensions
# ---------------------------------------------------------------------------

def test_images_carry_real_dimensions(client, alice):
    """Previously hard-coded to 0, which makes the file useless to any consumer
    that needs to denormalize or validate coordinates."""
    pid = _new_project(client, alice)
    _upload_png(client, alice, pid, "sized.png", 640, 480)

    export = _export_coco(client, alice, pid)
    image = next(i for i in export["images"] if i["file_name"] == "sized.png")
    assert image["width"] == 640
    assert image["height"] == 480


def test_missing_image_degrades_to_zero_not_an_error(client, alice):
    """A task with no image on disk must not fail the whole export."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "ghost.png")

    export = _export_coco(client, alice, pid)
    image = next(i for i in export["images"] if i["file_name"] == "ghost.png")
    assert (image["width"], image["height"]) == (0, 0)


# ---------------------------------------------------------------------------
# Area
# ---------------------------------------------------------------------------

def test_area_is_polygon_area_not_bbox_area(client, alice):
    """A right triangle in a 100x100 box has area 5000, not 10000."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "lbl-tri", "Triangle")
    _new_task(client, alice, pid, "tri.png", annotations=[{
        "id": "a1", "labelId": lid, "type": "polygon",
        "points": [{"x": 0, "y": 0}, {"x": 100, "y": 0}, {"x": 0, "y": 100}],
    }])

    export = _export_coco(client, alice, pid)
    ann = export["annotations"][0]
    assert ann["area"] == pytest.approx(5000.0)
    assert ann["bbox"] == [0, 0, 100, 100]
    # The bug being fixed: area used to equal bbox width * height.
    assert ann["area"] != ann["bbox"][2] * ann["bbox"][3]


def test_area_equals_bbox_area_for_a_rectangle(client, alice):
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "lbl-rect", "Rect")
    _new_task(client, alice, pid, "rect.png", annotations=[{
        "id": "a1", "labelId": lid, "type": "bbox",
        "points": [{"x": 10, "y": 10}, {"x": 30, "y": 10}, {"x": 30, "y": 40}, {"x": 10, "y": 40}],
    }])

    export = _export_coco(client, alice, pid)
    ann = export["annotations"][0]
    assert ann["area"] == pytest.approx(600.0)
    assert ann["area"] == pytest.approx(ann["bbox"][2] * ann["bbox"][3])


# ---------------------------------------------------------------------------
# Annotation key set
# ---------------------------------------------------------------------------

def test_annotation_key_set_matches_interop(client, alice):
    """Compared against the real the interop format COCO export's annotation keys."""
    with open(os.path.join(FIXTURES, "coco_annotations.json"), encoding="utf-8") as fh:
        reference = json.load(fh)
    expected_keys = set(reference["annotations"][0])

    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "lbl-k", "Thing")
    _new_task(client, alice, pid, "k.png", annotations=[{
        "id": "a1", "labelId": lid, "type": "polygon",
        "points": [{"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 5, "y": 9}],
    }])

    export = _export_coco(client, alice, pid)
    assert set(export["annotations"][0]) == expected_keys


def test_category_key_set_matches_interop(client, alice):
    with open(os.path.join(FIXTURES, "coco_annotations.json"), encoding="utf-8") as fh:
        reference = json.load(fh)
    expected_keys = set(reference["categories"][0])

    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "lbl-c", "Thing")
    export = _export_coco(client, alice, pid)
    assert set(export["categories"][0]) == expected_keys


def test_attributes_is_an_object_not_an_array(client, alice):
    """COCO annotations use an object here; the per-task format uses an array.
    They must not be unified."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "lbl-o", "Thing")
    _new_task(client, alice, pid, "o.png", annotations=[{
        "id": "a1", "labelId": lid, "points": [{"x": 0, "y": 0}, {"x": 5, "y": 5}],
    }])

    export = _export_coco(client, alice, pid)
    assert isinstance(export["annotations"][0]["attributes"], dict)


# ---------------------------------------------------------------------------
# supercategory
# ---------------------------------------------------------------------------

def test_supercategory_is_the_value_form(client, alice):
    """the interop format puts the punctuation-stripped identifier here, not the display
    name — matching it is what lets an interop importer resolve the class."""
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "lbl-s", "AC Paint / Exposed Steel Plate")

    export = _export_coco(client, alice, pid)
    cat = export["categories"][0]
    assert cat["name"] == "AC Paint / Exposed Steel Plate"
    assert cat["supercategory"] == "ACPaintExposedSteelPlate"


# ---------------------------------------------------------------------------
# Shape type round trip (gap G1)
# ---------------------------------------------------------------------------

def test_shape_type_is_carried_in_attributes(client, alice):
    """COCO has no shape-type concept, so the bbox/polygon distinction rides in
    `attributes` — inert to other tools, readable by our own importer."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "lbl-t", "Thing")
    _new_task(client, alice, pid, "t.png", annotations=[
        {"id": "a1", "labelId": lid, "type": "bbox",
         "points": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}, {"x": 0, "y": 10}]},
        {"id": "a2", "labelId": lid, "type": "polygon",
         "points": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 5, "y": 10}]},
    ])

    export = _export_coco(client, alice, pid)
    types = [a["attributes"]["shapeType"] for a in export["annotations"]]
    assert types == ["bbox", "polygon"]


def test_shape_type_survives_export_then_import(client, alice):
    """The round trip gap G1 exists to close."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "lbl-r", "Thing")
    _new_task(client, alice, pid, "r.png", annotations=[
        {"id": "a1", "labelId": lid, "type": "bbox",
         "points": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}, {"x": 0, "y": 10}]},
    ])
    export = _export_coco(client, alice, pid)

    target = _new_project(client, alice, "target")
    _new_task(client, alice, target, "r.png")
    res = client.post(
        f"/api/imports/annotations?projectId={target}&mode=replace",
        files=[("file", ("coco.json", json.dumps(export).encode(), "application/json"))],
        headers=alice,
    )
    assert res.status_code == 200, res.text

    tasks = client.get(f"/api/tasks?projectId={target}", headers=alice).json()
    imported = next(t for t in tasks if t["description"] == "r.png")["annotations"]
    assert imported[0]["type"] == "bbox"


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------

def test_parse_reports_both_name_and_value():
    """the interop COCO puts the value form in supercategory while its per-task
    JSON uses the display name. Both are reported so the caller can match on
    either and avoid creating two labels for one class."""
    doc = {
        "images": [{"id": 1, "file_name": "a.png"}],
        "categories": [{"id": 1, "name": "Rust Area", "supercategory": "RustArea", "color": "#fff"}],
        "annotations": [{"image_id": 1, "category_id": 1, "segmentation": [[0, 0, 5, 0, 5, 5]]}],
    }
    parsed = coco_format.parse(doc)
    ann = parsed["a.png"][0]
    assert ann["labelName"] == "Rust Area"
    assert ann["labelValue"] == "RustArea"
    assert ann["labelColor"] == "#fff"


def test_parse_bbox_only_annotation_is_a_bbox():
    doc = {
        "images": [{"id": 1, "file_name": "a.png"}],
        "categories": [{"id": 1, "name": "X"}],
        "annotations": [{"image_id": 1, "category_id": 1, "bbox": [10, 20, 30, 40]}],
    }
    ann = coco_format.parse(doc)["a.png"][0]
    assert ann["type"] == "bbox"
    assert ann["x"] == 10 and ann["y"] == 20
    assert ann["width"] == 30 and ann["height"] == 40
    assert len(ann["points"]) == 4


def test_parse_multipolygon_becomes_separate_annotations():
    """A COCO segmentation may hold several disjoint rings."""
    doc = {
        "images": [{"id": 1, "file_name": "a.png"}],
        "categories": [{"id": 1, "name": "X"}],
        "annotations": [{
            "image_id": 1, "category_id": 1,
            "segmentation": [[0, 0, 5, 0, 5, 5], [10, 10, 15, 10, 15, 15]],
        }],
    }
    assert len(coco_format.parse(doc)["a.png"]) == 2


def test_parse_skips_annotation_with_unknown_image():
    doc = {
        "images": [{"id": 1, "file_name": "a.png"}],
        "categories": [{"id": 1, "name": "X"}],
        "annotations": [{"image_id": 99, "category_id": 1, "segmentation": [[0, 0, 5, 0, 5, 5]]}],
    }
    assert coco_format.parse(doc) == {}


def test_parse_ignores_malformed_entries():
    """A hand-edited file must not take the whole import down."""
    doc = {
        "images": [{"id": 1, "file_name": "a.png"}, {"no_id": True}],
        "categories": [{"id": 1, "name": "X"}, {"broken": True}],
        "annotations": [
            "not a dict",
            {"image_id": 1, "category_id": 1},  # neither segmentation nor bbox
            {"image_id": 1, "category_id": 1, "segmentation": [[0, 0, 5, 0, 5, 5]]},
        ],
    }
    parsed = coco_format.parse(doc)
    assert len(parsed["a.png"]) == 1


# ---------------------------------------------------------------------------
# Name-or-value label matching
#
# the interop COCO puts the value form in `supercategory` ("RustArea") while
# its per-task JSON uses the display name ("Rust Area"). Importing both files
# from one source project used to create two labels for the same class.
# ---------------------------------------------------------------------------

def _import(client, auth, pid, filename, payload):
    return client.post(
        f"/api/imports/annotations?projectId={pid}",
        files=[("file", (filename, json.dumps(payload).encode(), "application/json"))],
        headers=auth,
    )


def _coco_doc(category_name, filename="dup.png"):
    return {
        "images": [{"id": 1, "file_name": filename, "width": 100, "height": 100}],
        "categories": [{"id": 1, "name": category_name, "supercategory": category_name,
                        "color": "#D95319"}],
        "annotations": [{"image_id": 1, "category_id": 1,
                         "segmentation": [[0, 0, 10, 0, 10, 10]], "bbox": [0, 0, 10, 10]}],
    }


def _pertask_doc(title, value, filename="dup.png"):
    return {
        "name": filename, "width": 100, "height": 100,
        "annotations": [{"id": "a1", "type": "polygon", "title": title, "value": value,
                         "color": "#D95319", "points": [20, 20, 30, 20, 30, 30]}],
    }


def test_coco_then_pertask_does_not_duplicate_the_class(client, alice):
    """The value form arrives first, then the display name."""
    pid = _new_project(client, alice, "dup1")
    _new_task(client, alice, pid, "dup.png")

    assert _import(client, alice, pid, "coco.json", _coco_doc("RustArea")).status_code == 200
    assert _import(client, alice, pid, "task.json", _pertask_doc("Rust Area", "RustArea")).status_code == 200

    labels = client.get(f"/api/labels?projectId={pid}", headers=alice).json()
    assert len(labels) == 1, [l["name"] for l in labels]


def test_pertask_then_coco_does_not_duplicate_the_class(client, alice):
    """And the other order — the display name arrives first."""
    pid = _new_project(client, alice, "dup2")
    _new_task(client, alice, pid, "dup.png")

    assert _import(client, alice, pid, "task.json", _pertask_doc("Rust Area", "RustArea")).status_code == 200
    assert _import(client, alice, pid, "coco.json", _coco_doc("RustArea")).status_code == 200

    labels = client.get(f"/api/labels?projectId={pid}", headers=alice).json()
    assert len(labels) == 1, [l["name"] for l in labels]
    # The display name is kept — it is the more informative of the two.
    assert labels[0]["name"] == "Rust Area"


def test_preview_agrees_with_apply_about_new_labels(client, alice):
    """The preview must not promise a new label the import then merges away."""
    pid = _new_project(client, alice, "dup3")
    _new_task(client, alice, pid, "dup.png")
    _import(client, alice, pid, "task.json", _pertask_doc("Rust Area", "RustArea"))

    res = client.post(
        f"/api/imports/annotations/preview?projectId={pid}",
        files=[("file", ("coco.json", json.dumps(_coco_doc("RustArea")).encode(), "application/json"))],
        headers=alice,
    )
    assert res.status_code == 200, res.text
    assert res.json()["new_labels"] == []


def test_genuinely_new_class_is_still_created(client, alice):
    """Value matching must not over-merge distinct classes."""
    pid = _new_project(client, alice, "dup4")
    _new_task(client, alice, pid, "dup.png")

    _import(client, alice, pid, "a.json", _pertask_doc("Rust Area", "RustArea"))
    _import(client, alice, pid, "b.json", _pertask_doc("Sound Area", "SoundArea"))

    labels = client.get(f"/api/labels?projectId={pid}", headers=alice).json()
    assert {l["name"] for l in labels} == {"Rust Area", "Sound Area"}

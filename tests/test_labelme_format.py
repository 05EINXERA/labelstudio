"""LabelMe import: parsing, detection, and the full import path.

Shapes and label names here follow the reference files (a bridge-inspection
project labelling 健全箇所 / 汚れ1 / 汚れ2), because the non-ASCII class names
are exactly what a transliterating or ascii-assuming parser would break.
"""
import json
import uuid

import pytest

from formats import labelme as labelme_format
from api.routers.imports import _parse_single_json


def _doc(shapes, image_path="P1000678.JPG", **extra):
    doc = {
        "version": "5.5.0",
        "flags": {},
        "shapes": shapes,
        "imagePath": image_path,
        "imageData": None,
        "imageHeight": 1944,
        "imageWidth": 2592,
    }
    doc.update(extra)
    return doc


def _shape(label="健全箇所", points=None, shape_type="polygon", **extra):
    shape = {
        "label": label,
        "points": points if points is not None else [[10.0, 10.0], [30.0, 10.0], [20.0, 40.0]],
        "group_id": None,
        "description": "",
        "shape_type": shape_type,
        "flags": {},
        "mask": None,
    }
    shape.update(extra)
    return shape


# --- detection ---------------------------------------------------------


def test_detects_labelme_document():
    assert labelme_format.looks_like_document(_doc([_shape()])) is True


@pytest.mark.parametrize("data", [
    # COCO: identified by images + annotations, must not be claimed here.
    {"images": [], "annotations": [], "categories": []},
    # Native interop: a list of task objects.
    [{"name": "a.png", "annotations": []}],
    # A dict with shapes but no imagePath is not a LabelMe document.
    {"shapes": []},
    # ...nor is one with imagePath but no shapes list.
    {"imagePath": "a.png"},
    {"shapes": "not-a-list", "imagePath": "a.png"},
    None,
    "a string",
])
def test_does_not_claim_other_formats(data):
    assert labelme_format.looks_like_document(data) is False


def test_dispatcher_routes_labelme_to_the_labelme_parser():
    raw = json.dumps(_doc([_shape()])).encode()
    parsed = _parse_single_json(raw)
    assert list(parsed) == ["P1000678.JPG"]
    assert parsed["P1000678.JPG"][0]["labelName"] == "健全箇所"


# --- parsing -----------------------------------------------------------


def test_polygon_points_and_bbox():
    parsed = labelme_format.parse(_doc([
        _shape(points=[[10.0, 20.0], [40.0, 20.0], [40.0, 60.0], [10.0, 60.0]])
    ]))
    ann = parsed["P1000678.JPG"][0]

    assert ann["points"] == [
        {"x": 10.0, "y": 20.0}, {"x": 40.0, "y": 20.0},
        {"x": 40.0, "y": 60.0}, {"x": 10.0, "y": 60.0},
    ]
    # The enclosing box is derived, not read from the file — LabelMe has none.
    assert (ann["x"], ann["y"], ann["width"], ann["height"]) == (10.0, 20.0, 30.0, 40.0)
    assert ann["type"] == "polygon"
    assert ann["labelName"] == "健全箇所"
    # No colour exists in a LabelMe file; the caller assigns from its palette.
    assert "labelColor" not in ann


def test_non_ascii_label_names_survive_verbatim():
    parsed = labelme_format.parse(_doc([
        _shape(label="健全箇所"), _shape(label="汚れ1"), _shape(label="汚れ2"),
    ]))
    names = [a["labelName"] for a in parsed["P1000678.JPG"]]
    assert names == ["健全箇所", "汚れ1", "汚れ2"]


def test_rectangle_two_corners_becomes_four_point_bbox():
    parsed = labelme_format.parse(_doc([
        _shape(points=[[10.0, 20.0], [50.0, 80.0]], shape_type="rectangle")
    ]))
    ann = parsed["P1000678.JPG"][0]

    assert ann["type"] == "bbox"
    assert ann["points"] == [
        {"x": 10.0, "y": 20.0}, {"x": 50.0, "y": 20.0},
        {"x": 50.0, "y": 80.0}, {"x": 10.0, "y": 80.0},
    ]


def test_rectangle_drawn_in_reverse_still_winds_correctly():
    """Corners bottom-right -> top-left describe the same box."""
    forward = labelme_format.parse(_doc([
        _shape(points=[[10.0, 20.0], [50.0, 80.0]], shape_type="rectangle")
    ]))["P1000678.JPG"][0]
    reverse = labelme_format.parse(_doc([
        _shape(points=[[50.0, 80.0], [10.0, 20.0]], shape_type="rectangle")
    ]))["P1000678.JPG"][0]

    assert forward["points"] == reverse["points"]


def test_missing_shape_type_is_treated_as_polygon():
    shape = _shape()
    del shape["shape_type"]
    parsed = labelme_format.parse(_doc([shape]))
    assert parsed["P1000678.JPG"][0]["type"] == "polygon"


@pytest.mark.parametrize("shape_type", ["circle", "line", "linestrip", "point", "mask"])
def test_unsupported_shape_types_are_skipped_not_coerced(shape_type):
    """The canvas has no such primitive; a silently reshaped import is worse."""
    parsed = labelme_format.parse(_doc([
        _shape(shape_type=shape_type),
        _shape(label="汚れ1"),  # a real polygon alongside it still imports
    ]))
    anns = parsed["P1000678.JPG"]
    assert len(anns) == 1
    assert anns[0]["labelName"] == "汚れ1"


def test_degenerate_and_malformed_shapes_are_skipped():
    parsed = labelme_format.parse(_doc([
        _shape(points=[[10.0, 10.0], [20.0, 20.0]]),      # 2 points: a line, not an area
        _shape(points=[[10.0, 10.0], [20.0], [30.0, 30.0]]),  # short vertex
        _shape(points=[[10.0, 10.0], ["x", 20.0], [30.0, 30.0]]),  # non-numeric
        _shape(label="汚れ2", points=[[0.0, 0.0], [5.0, 0.0], [5.0, 5.0]]),
    ]))
    anns = parsed["P1000678.JPG"]
    assert len(anns) == 1
    assert anns[0]["labelName"] == "汚れ2"


def test_group_id_is_carried_when_present():
    parsed = labelme_format.parse(_doc([
        _shape(group_id=3), _shape(label="汚れ1"),
    ]))
    anns = parsed["P1000678.JPG"]
    assert anns[0]["group_id"] == "3"
    assert "group_id" not in anns[1]


def test_image_path_directory_is_stripped():
    """imagePath may be relative; tasks are matched on the bare filename."""
    parsed = labelme_format.parse(_doc([_shape()], image_path="../images/P1000678.JPG"))
    assert list(parsed) == ["P1000678.JPG"]


def test_missing_image_path_is_an_error():
    doc = _doc([_shape()], image_path="")
    with pytest.raises(ValueError, match="imagePath"):
        labelme_format.parse(doc)


def test_every_annotation_gets_a_distinct_id():
    parsed = labelme_format.parse(_doc([_shape(), _shape(), _shape()]))
    ids = [a["id"] for a in parsed["P1000678.JPG"]]
    assert len(set(ids)) == 3


def test_document_with_no_shapes_yields_nothing():
    assert labelme_format.parse(_doc([])) == {}


# --- end-to-end through the import endpoint ----------------------------


def _new_project(client, auth, name="labelme-proj"):
    res = client.post(
        "/api/projects",
        json={"name": name, "slug": name, "creator": "tester"},
        headers=auth,
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _new_task(client, auth, pid, description):
    res = client.post(
        "/api/tasks", json={"description": description},
        params={"projectId": pid}, headers=auth,
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def test_import_creates_annotations_and_missing_labels(client, alice):
    """A LabelMe file imports onto the matching task, creating its classes."""
    pid = _new_project(client, alice)
    task_id = _new_task(client, alice, pid, "P1000678.JPG")

    payload = json.dumps(_doc([
        _shape(label="健全箇所", points=[[10.0, 10.0], [40.0, 10.0], [40.0, 50.0]]),
        _shape(label="汚れ1", points=[[100.0, 100.0], [150.0, 100.0], [150.0, 160.0]]),
    ])).encode()

    res = client.post(
        f"/api/imports/annotations?projectId={pid}",
        files={"file": ("P1000678.json", payload, "application/json")},
        headers=alice,
    )
    assert res.status_code == 200, res.text

    detail = client.get(f"/api/tasks/{task_id}", headers=alice)
    assert detail.status_code == 200, detail.text
    anns = detail.json()["annotations"]
    assert len(anns) == 2

    labels = client.get(f"/api/labels?projectId={pid}", headers=alice).json()
    names = {l["name"] for l in labels}
    assert {"健全箇所", "汚れ1"} <= names


def test_zip_of_labelme_files_imports_every_entry(client, alice):
    """A LabelMe project folder is usually a directory of per-image JSONs.

    The archive path dispatches each entry through the same per-file detection,
    so this needs no LabelMe-specific zip handling — which is exactly why it is
    worth pinning.
    """
    import io
    import zipfile

    pid = _new_project(client, alice)
    first = _new_task(client, alice, pid, "P1000678.JPG")
    second = _new_task(client, alice, pid, "P1000679.JPG")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("labels/P1000678.json", json.dumps(_doc(
            [_shape(label="健全箇所")], image_path="P1000678.JPG")))
        zf.writestr("labels/P1000679.json", json.dumps(_doc(
            [_shape(label="汚れ1"), _shape(label="汚れ2")], image_path="P1000679.JPG")))
        zf.writestr("images/P1000678.JPG", b"not-an-annotation")  # skipped, not an error

    res = client.post(
        f"/api/imports/annotations?projectId={pid}",
        files={"file": ("labelme.zip", buf.getvalue(), "application/zip")},
        headers=alice,
    )
    assert res.status_code == 200, res.text
    assert res.json()["annotations_imported"] == 3

    assert len(client.get(f"/api/tasks/{first}", headers=alice).json()["annotations"]) == 1
    assert len(client.get(f"/api/tasks/{second}", headers=alice).json()["annotations"]) == 2


def test_preview_matches_task_without_writing(client, alice):
    pid = _new_project(client, alice)
    task_id = _new_task(client, alice, pid, "P1000678.JPG")

    payload = json.dumps(_doc([_shape()])).encode()
    res = client.post(
        f"/api/imports/annotations/preview?projectId={pid}",
        files={"file": ("P1000678.json", payload, "application/json")},
        headers=alice,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert [m["filename"] for m in body["matched"]] == ["P1000678.JPG"]

    detail = client.get(f"/api/tasks/{task_id}", headers=alice)
    assert detail.json()["annotations"] == []

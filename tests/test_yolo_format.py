"""YOLO segmentation export/import (data-refactor plan Phase 3.3).

The reference export is YOLOv8 *segmentation* — `<cls> x1 y1 x2 y2 ...`
normalized to [0, 1] — not the 5-token detection format. Verified against
tests/fixtures/interop/yolo_*.

Two failure modes the format cannot express are pinned here as explicit
behaviour: a task with unknown image dimensions is skipped and reported rather
than divided by zero, and a label file without classes.txt is rejected rather
than guessed at.
"""
import io
import json
import os
import zipfile

import pytest
from PIL import Image

from conftest import unique_label_id
from formats import yolo

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "interop")


def _new_project(client, auth, name="yolo"):
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


def _upload_png(client, auth, pid, filename, width, height):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (1, 2, 3)).save(buf, format="PNG")
    res = client.post(
        f"/api/projects/{pid}/upload",
        files=[("file", (filename, buf.getvalue(), "image/png"))],
        headers=auth,
    )
    assert res.status_code == 200, res.text


def _set_annotations(client, auth, pid, description, annotations):
    tasks = client.get(f"/api/tasks?projectId={pid}", headers=auth).json()
    tid = next(t["id"] for t in tasks if t["description"] == description)
    res = client.patch(f"/api/tasks/{tid}", json={"annotations": json.dumps(annotations)}, headers=auth)
    assert res.status_code == 200, res.text


def _export_yolo(client, auth, pid):
    res = client.post("/api/exports", json={"projectId": pid, "format": "yolo"}, headers=auth)
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    status = client.get(f"/api/exports/{job_id}", headers=auth).json()
    assert status["status"] == "completed", status
    download = client.get(f"/api/exports/{job_id}/download", headers=auth)
    zf = zipfile.ZipFile(io.BytesIO(download.content))
    return {n: zf.read(n) for n in zf.namelist()}, status


# ---------------------------------------------------------------------------
# Archive layout
# ---------------------------------------------------------------------------

def test_archive_layout_matches_the_reference(client, alice):
    """classes.txt at the root, label files under annotations/."""
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Rust Area")
    _upload_png(client, alice, pid, "P1000015.png", 100, 50)

    entries, _ = _export_yolo(client, alice, pid)
    assert "classes.txt" in entries
    assert "annotations/P1000015.txt" in entries


def test_classes_file_uses_the_value_form(client, alice):
    """The reference classes.txt carries identifiers, not display names."""
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "a", "Rust Area")
    _new_label(client, alice, pid, "b", "AC Paint / Exposed Steel Plate")

    entries, _ = _export_yolo(client, alice, pid)
    lines = entries["classes.txt"].decode().strip().split("\n")
    assert lines == ["RustArea", "ACPaintExposedSteelPlate"]


def test_class_index_is_the_classes_txt_line_number(client, alice):
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "a", "First")
    second = _new_label(client, alice, pid, "b", "Second")
    _upload_png(client, alice, pid, "idx.png", 100, 100)
    _set_annotations(client, alice, pid, "idx.png", [
        {"id": "a1", "labelId": second, "type": "polygon",
         "points": [{"x": 0, "y": 0}, {"x": 50, "y": 0}, {"x": 25, "y": 50}]},
    ])

    entries, _ = _export_yolo(client, alice, pid)
    classes = entries["classes.txt"].decode().strip().split("\n")
    line = entries["annotations/idx.txt"].decode().strip()
    assert classes.index("Second") == int(line.split()[0])


# ---------------------------------------------------------------------------
# Coordinates
# ---------------------------------------------------------------------------

def test_coordinates_are_normalized(client, alice):
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "n.png", 200, 100)
    _set_annotations(client, alice, pid, "n.png", [
        {"id": "a1", "labelId": lid, "type": "polygon",
         "points": [{"x": 0, "y": 0}, {"x": 100, "y": 0}, {"x": 100, "y": 50}]},
    ])

    entries, _ = _export_yolo(client, alice, pid)
    parts = entries["annotations/n.txt"].decode().strip().split()
    coords = [float(v) for v in parts[1:]]
    assert coords == [0.0, 0.0, 0.5, 0.0, 0.5, 0.5]


def test_coordinates_are_clamped_to_unit_range(client, alice):
    """A shape dragged past the image edge must not emit an invalid coordinate."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "c.png", 100, 100)
    _set_annotations(client, alice, pid, "c.png", [
        {"id": "a1", "labelId": lid, "type": "polygon",
         "points": [{"x": -20, "y": 0}, {"x": 150, "y": 0}, {"x": 50, "y": 120}]},
    ])

    entries, _ = _export_yolo(client, alice, pid)
    coords = [float(v) for v in entries["annotations/c.txt"].decode().strip().split()[1:]]
    assert all(0.0 <= c <= 1.0 for c in coords), coords


def test_float_formatting_matches_the_reference(client, alice):
    """The reference emits repr()-style floats — "0.0" and "8.87e-05" appear in
    the real file, which a fixed "%.7f" would render differently."""
    assert yolo._fmt(0.0) == "0.0"
    assert yolo._fmt(0.4384516) == "0.4384516"
    assert yolo._fmt(0.0000887) == "8.87e-05"
    # Rounded to 7 dp, matching the reference's precision.
    assert yolo._fmt(0.12345678999) == "0.1234568"


def test_reference_label_file_parses_with_our_reader():
    """Our parser reads the real exported file, not just our own output."""
    with open(os.path.join(FIXTURES, "yolo_classes.txt"), "rb") as fh:
        classes = yolo.parse_classes(fh.read())
    with open(os.path.join(FIXTURES, "yolo_P1000015.txt"), "rb") as fh:
        anns = yolo.parse_label_file(fh.read(), classes)

    assert len(anns) == 3
    assert all(a["normalized"] for a in anns)
    assert all(a["labelName"] in classes for a in anns)
    for a in anns:
        for p in a["points"]:
            assert 0.0 <= p["x"] <= 1.0 and 0.0 <= p["y"] <= 1.0


# ---------------------------------------------------------------------------
# Tasks the format cannot represent
# ---------------------------------------------------------------------------

def test_task_without_dimensions_is_skipped_and_reported(client, alice):
    """Normalizing by a zero dimension is a crash; inventing a size would emit
    coordinates that silently mean nothing."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "l", "Thing")
    _new_task(client, alice, pid, "ghost.png", annotations=[
        {"id": "a1", "labelId": lid, "points": [{"x": 0, "y": 0}, {"x": 5, "y": 5}]},
    ])

    entries, status = _export_yolo(client, alice, pid)
    assert "annotations/ghost.txt" not in entries
    assert len(status["skipped"]) == 1
    assert status["skipped"][0]["filename"] == "ghost.png"
    assert "dimensions" in status["skipped"][0]["reason"]


def test_export_with_no_skips_reports_an_empty_list(client, alice):
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "fine.png", 40, 40)

    _, status = _export_yolo(client, alice, pid)
    assert status["skipped"] == []


def test_task_with_no_annotations_gets_an_empty_file(client, alice):
    """An empty label file is the YOLO convention for a negative sample, and
    the reference export writes one too."""
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "empty.png", 40, 40)

    entries, _ = _export_yolo(client, alice, pid)
    assert entries["annotations/empty.txt"] == b""


def test_annotation_for_deleted_label_is_skipped(client, alice):
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "orphan.png", 40, 40)
    _set_annotations(client, alice, pid, "orphan.png", [
        {"id": "a1", "labelId": "gone", "points": [{"x": 0, "y": 0}, {"x": 5, "y": 5}]},
    ])

    entries, _ = _export_yolo(client, alice, pid)
    assert entries["annotations/orphan.txt"] == b""


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def test_parse_reads_segmentation_lines():
    anns = yolo.parse_label_file(b"0 0.1 0.1 0.5 0.1 0.5 0.5\n", ["Thing"])
    assert len(anns) == 1
    assert anns[0]["type"] == "polygon"
    assert len(anns[0]["points"]) == 3


def test_parse_reads_detection_lines_as_boxes():
    """A 4-value line is unambiguously detection format — a polygon needs at
    least 3 points, i.e. 6 values."""
    anns = yolo.parse_label_file(b"0 0.5 0.5 0.2 0.4\n", ["Thing"])
    assert anns[0]["type"] == "bbox"
    xs = [p["x"] for p in anns[0]["points"]]
    ys = [p["y"] for p in anns[0]["points"]]
    assert min(xs) == pytest.approx(0.4) and max(xs) == pytest.approx(0.6)
    assert min(ys) == pytest.approx(0.3) and max(ys) == pytest.approx(0.7)


def test_parse_skips_malformed_and_out_of_range_lines():
    raw = (
        b"not a number 0.1 0.2\n"
        b"9 0.1 0.1 0.5 0.1 0.5 0.5\n"   # class index not in classes.txt
        b"0 0.1 0.2 0.3\n"                # odd coordinate count
        b"\n"
        b"0 0.1 0.1 0.5 0.1 0.5 0.5\n"    # the only valid line
    )
    assert len(yolo.parse_label_file(raw, ["Thing"])) == 1


def test_denormalize_scales_into_pixels():
    ann = {"labelName": "X", "normalized": True, "type": "polygon",
           "points": [{"x": 0.0, "y": 0.0}, {"x": 0.5, "y": 0.25}]}
    out = yolo.denormalize(ann, 200, 100)
    assert out["points"] == [{"x": 0.0, "y": 0.0}, {"x": 100.0, "y": 25.0}]
    assert (out["x"], out["y"], out["width"], out["height"]) == (0.0, 0.0, 100.0, 25.0)
    assert "normalized" not in out


def test_parse_archive_requires_classes_txt():
    """A class index is meaningless without it; guessing would mislabel
    every annotation."""
    with pytest.raises(ValueError, match="classes.txt"):
        yolo.parse_archive({"annotations/a.txt": b"0 0.1 0.1 0.5 0.1 0.5 0.5\n"})


def test_parse_archive_rejects_empty_classes_txt():
    with pytest.raises(ValueError, match="empty"):
        yolo.parse_archive({"classes.txt": b"\n", "annotations/a.txt": b"0 0.1 0.1 0.5 0.1 0.5 0.5\n"})


def test_parse_archive_keys_on_stem():
    """A label file is P1000015.txt while the task is P1000015.JPG, so the
    caller has to match on the stem."""
    parsed = yolo.parse_archive({
        "classes.txt": b"Thing\n",
        "annotations/P1000015.txt": b"0 0.1 0.1 0.5 0.1 0.5 0.5\n",
    })
    assert list(parsed) == ["P1000015"]


def test_looks_like_archive_detection():
    assert yolo.looks_like_archive(["classes.txt", "annotations/a.txt"])
    assert not yolo.looks_like_archive(["classes.txt"])           # no label files
    assert not yolo.looks_like_archive(["annotations/a.txt"])     # no classes.txt
    assert not yolo.looks_like_archive(["jsons/a.json"])


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------

def test_export_then_parse_round_trips_geometry(client, alice):
    """Export, read the archive back, denormalize — coordinates return to
    roughly where they started (within the format's 7 dp precision)."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "l", "Rust Area")
    _upload_png(client, alice, pid, "rt.png", 200, 100)
    original = [{"x": 10, "y": 20}, {"x": 150, "y": 20}, {"x": 80, "y": 90}]
    _set_annotations(client, alice, pid, "rt.png", [
        {"id": "a1", "labelId": lid, "type": "polygon", "points": original},
    ])

    entries, _ = _export_yolo(client, alice, pid)
    parsed = yolo.parse_archive(entries)
    ann = yolo.denormalize(parsed["rt"][0], 200, 100)

    assert ann["labelName"] == "RustArea"
    for got, want in zip(ann["points"], original):
        assert got["x"] == pytest.approx(want["x"], abs=0.01)
        assert got["y"] == pytest.approx(want["y"], abs=0.01)

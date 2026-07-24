"""YOLO import and the export-only rejections (data-refactor plan Phase 4).

YOLO import reshapes the parse -> match -> apply pipeline: coordinates stay
normalized out of the parser and are scaled by the matched task's dimensions
in the apply step, since that is the first point the image size is known. A
task whose dimensions are unknown is reported rather than written with
meaningless coordinates.

Masks and class-set files are rejected with messages that name the reason,
rather than the generic "nothing recognizable" they would otherwise hit.
"""
import io
import json
import os
import zipfile

import pytest
from PIL import Image

from conftest import unique_label_id

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "interop")


def _new_project(client, auth, name="imp"):
    res = client.post("/api/projects", json={"name": name, "slug": name, "creator": "x"}, headers=auth)
    return res.json()["id"]


def _new_label(client, auth, pid, name, color="#ef4444"):
    lid = unique_label_id("lbl")
    client.post("/api/labels", json={"id": lid, "name": name, "color": color, "projectId": pid}, headers=auth)
    return lid


def _upload_png(client, auth, pid, filename, width, height):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (1, 2, 3)).save(buf, format="PNG")
    res = client.post(
        f"/api/projects/{pid}/upload",
        files=[("file", (filename, buf.getvalue(), "image/png"))],
        headers=auth,
    )
    assert res.status_code == 200, res.text


def _new_task(client, auth, pid, description):
    res = client.post(f"/api/tasks?projectId={pid}", json={"description": description, "status": "New"}, headers=auth)
    assert res.status_code in (200, 201), res.text


def _yolo_zip(classes, label_files):
    """classes: list[str]; label_files: {arcname: text}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("classes.txt", "\n".join(classes) + "\n")
        for name, text in label_files.items():
            zf.writestr(name, text)
    return buf.getvalue()


def _import(client, auth, pid, filename, raw, mode="merge", ctype="application/zip"):
    return client.post(
        f"/api/imports/annotations?projectId={pid}&mode={mode}",
        files=[("file", (filename, raw, ctype))],
        headers=auth,
    )


def _preview(client, auth, pid, filename, raw, ctype="application/zip"):
    return client.post(
        f"/api/imports/annotations/preview?projectId={pid}",
        files=[("file", (filename, raw, ctype))],
        headers=auth,
    )


def _annotations(client, auth, pid, description):
    tasks = client.get(f"/api/tasks?projectId={pid}", headers=auth).json()
    return next(t for t in tasks if t["description"] == description)["annotations"]


# ---------------------------------------------------------------------------
# YOLO import
# ---------------------------------------------------------------------------

def test_yolo_import_scales_by_the_matched_task_dimensions(client, alice):
    """The parser leaves coordinates normalized; the apply step scales them by
    the matched image's real size."""
    pid = _new_project(client, alice)
    _upload_png(client, alice, pid, "P1000015.JPG", 200, 100)

    # A right triangle at (0,0)-(100,0)-(0,50) normalizes to these.
    raw = _yolo_zip(["Rust Area"], {"annotations/P1000015.txt": "0 0.0 0.0 0.5 0.0 0.0 0.5\n"})
    res = _import(client, alice, pid, "yolo.zip", raw)
    assert res.status_code == 200, res.text
    assert res.json()["tasks_updated"] == 1

    anns = _annotations(client, alice, pid, "P1000015.JPG")
    assert len(anns) == 1
    assert anns[0]["points"] == [{"x": 0.0, "y": 0.0}, {"x": 100.0, "y": 0.0}, {"x": 0.0, "y": 50.0}]


def test_yolo_import_matches_on_stem(client, alice):
    """The label file is P1000015.txt while the task is P1000015.JPG."""
    pid = _new_project(client, alice)
    _upload_png(client, alice, pid, "P1000015.JPG", 100, 100)

    raw = _yolo_zip(["Thing"], {"annotations/P1000015.txt": "0 0.1 0.1 0.5 0.1 0.5 0.5\n"})
    res = _preview(client, alice, pid, "yolo.zip", raw)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["matched"]) == 1
    assert body["matched"][0]["filename"] == "P1000015"


def test_yolo_import_creates_classes_from_classes_txt(client, alice):
    pid = _new_project(client, alice)
    _upload_png(client, alice, pid, "img.jpg", 100, 100)

    raw = _yolo_zip(["RustArea"], {"annotations/img.txt": "0 0.1 0.1 0.5 0.1 0.5 0.5\n"})
    _import(client, alice, pid, "yolo.zip", raw)

    labels = client.get(f"/api/labels?projectId={pid}", headers=alice).json()
    assert "RustArea" in {l["name"] for l in labels}


def test_yolo_import_preserves_shape_type(client, alice):
    """A 4-value detection line comes back as a box, not a polygon."""
    pid = _new_project(client, alice)
    _upload_png(client, alice, pid, "shapes.jpg", 100, 100)

    raw = _yolo_zip(["Thing"], {"annotations/shapes.txt": "0 0.5 0.5 0.2 0.4\n"})
    _import(client, alice, pid, "yolo.zip", raw)

    anns = _annotations(client, alice, pid, "shapes.jpg")
    assert anns[0]["type"] == "bbox"


def test_yolo_import_reports_task_without_dimensions(client, alice):
    """A matched task with no image on disk cannot be denormalized; it is
    reported, not written with meaningless coordinates."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "nodim.jpg")  # task row, no image

    raw = _yolo_zip(["Thing"], {"annotations/nodim.txt": "0 0.1 0.1 0.5 0.1 0.5 0.5\n"})
    res = _import(client, alice, pid, "yolo.zip", raw)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tasks_updated"] == 0
    assert len(body["skipped"]) == 1
    assert "dimensions" in body["skipped"][0]["reason"]


def test_yolo_import_without_classes_txt_is_rejected(client, alice):
    """A class index is meaningless without the names; guessing would mislabel
    every annotation."""
    pid = _new_project(client, alice)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("annotations/a.txt", "0 0.1 0.1 0.5 0.1 0.5 0.5\n")

    res = _import(client, alice, pid, "yolo.zip", buf.getvalue())
    assert res.status_code == 422
    assert "classes.txt" in res.json()["detail"]


def test_yolo_export_import_round_trip(client, alice):
    """Export YOLO from one project, import into another, geometry preserved
    within the format's precision."""
    src = _new_project(client, alice, "src")
    lid = _new_label(client, alice, src, "Rust Area", "#D95319")
    _upload_png(client, alice, src, "rt.png", 200, 100)
    tasks = client.get(f"/api/tasks?projectId={src}", headers=alice).json()
    tid = next(t["id"] for t in tasks if t["description"] == "rt.png")
    original = [{"x": 10, "y": 20}, {"x": 150, "y": 20}, {"x": 80, "y": 90}]
    client.patch(f"/api/tasks/{tid}", json={"annotations": json.dumps([
        {"id": "a1", "labelId": lid, "type": "polygon", "points": original},
    ])}, headers=alice)

    exp = client.post("/api/exports", json={"projectId": src, "format": "yolo"}, headers=alice)
    job = exp.json()["job_id"]
    client.get(f"/api/exports/{job}", headers=alice)
    archive = client.get(f"/api/exports/{job}/download", headers=alice).content

    dst = _new_project(client, alice, "dst")
    _upload_png(client, alice, dst, "rt.png", 200, 100)
    res = _import(client, alice, dst, "yolo.zip", archive, mode="replace")
    assert res.status_code == 200, res.text

    anns = _annotations(client, alice, dst, "rt.png")
    assert len(anns) == 1
    for got, want in zip(anns[0]["points"], original):
        assert got["x"] == pytest.approx(want["x"], abs=0.05)
        assert got["y"] == pytest.approx(want["y"], abs=0.05)

    labels = {l["name"]: l["color"] for l in client.get(f"/api/labels?projectId={dst}", headers=alice).json()}
    assert "RustArea" in labels  # value form, since that is what classes.txt carries


# ---------------------------------------------------------------------------
# Mask archives are rejected
# ---------------------------------------------------------------------------

def _mask_zip():
    buf = io.BytesIO()
    png = io.BytesIO()
    Image.new("P", (10, 10)).save(png, format="PNG")
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("semantic_segmentations/a.png", png.getvalue())
        zf.writestr("instance_segmentations/a.png", png.getvalue())
    return buf.getvalue()


def test_mask_archive_is_rejected_with_a_clear_reason(client, alice):
    pid = _new_project(client, alice)
    res = _import(client, alice, pid, "masks.zip", _mask_zip())
    assert res.status_code == 422
    detail = res.json()["detail"].lower()
    assert "mask" in detail
    assert "export-only" in detail or "export only" in detail


def test_mask_archive_rejection_is_not_the_generic_message(client, alice):
    """The rejection must name masks, not fall through to 'nothing
    recognizable'."""
    pid = _new_project(client, alice)
    res = _import(client, alice, pid, "masks.zip", _mask_zip())
    assert "No recognizable annotations" not in res.json()["detail"]


# ---------------------------------------------------------------------------
# Class-set files are redirected
# ---------------------------------------------------------------------------

def test_class_set_file_is_redirected_to_classes_import(client, alice):
    """The Classes export uploaded to the annotation importer names label
    definitions with no geometry — it belongs in Classes -> Import."""
    with open(os.path.join(FIXTURES, "..", "..", "..", ".devnotes",
                           "data-examples", "imports", "classes.json")) as fh:
        # Fall back to a synthesized class set if the sample is not present.
        try:
            class_set = json.load(fh)
        except Exception:
            class_set = None
    if class_set is None:
        class_set = [{"type": "polygon", "title": "Rust Area", "value": "RustArea", "color": "#D95319"}]

    pid = _new_project(client, alice)
    res = _import(client, alice, pid, "classes.json",
                  json.dumps(class_set).encode(), ctype="application/json")
    assert res.status_code == 422
    assert "class-set" in res.json()["detail"].lower() or "classes" in res.json()["detail"].lower()


def test_synthesized_class_set_is_redirected(client, alice):
    """Self-contained version, not depending on the sample file."""
    pid = _new_project(client, alice)
    class_set = [
        {"type": "polygon", "title": "Rust Area", "value": "RustArea", "color": "#D95319", "order": 1},
        {"type": "polygon", "title": "Sound Area", "value": "SoundArea", "color": "#000000", "order": 2},
    ]
    res = _import(client, alice, pid, "classes.json",
                  json.dumps(class_set).encode(), ctype="application/json")
    assert res.status_code == 422
    assert "Classes" in res.json()["detail"]


def test_a_real_annotation_array_is_not_mistaken_for_a_class_set(client, alice):
    """The task-JSON array has `name` and `annotations`; it must still import."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "real.png")
    payload = [{
        "name": "real.png",
        "annotations": [{"title": "Thing", "value": "Thing", "color": "#fff",
                         "points": [0, 0, 5, 0, 5, 5]}],
    }]
    res = _import(client, alice, pid, "tasks.json",
                  json.dumps(payload).encode(), ctype="application/json")
    assert res.status_code == 200, res.text
    assert res.json()["tasks_updated"] == 1

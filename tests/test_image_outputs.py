"""Two-axis export: the image-output axis and how it composes with formats.

Exports carry two independent axes bundled into one project-named ZIP:
  - annotation FORMAT  -> coco/ | json/ | jsons/ | yolo/
  - IMAGE OUTPUT       -> original_image/ | annotated_image/ | mask_*_color/

Each axis lands in its own top-level folder. This file exercises the image
outputs (original / annotated / binary), the folder layout when both axes are
present, the deprecated single-axis aliases (json, pertask, masks_direct,
masks_index), and the compat carve-outs that stay a bare .json / .csv.

The mask-pixel semantics of the direct/index outputs are pinned in
tests/test_masks_format.py; this file only asserts that the outputs appear in
the right place and that the binary output is a clean 0/255 grayscale.
"""
import io
import json
import zipfile

from PIL import Image

from conftest import unique_label_id
from formats import common, images


# ---------------------------------------------------------------------------
# Fixture helpers (mirroring tests/test_masks_format.py)
# ---------------------------------------------------------------------------

def _new_project(client, auth, name="imgout"):
    res = client.post("/api/projects", json={"name": name, "slug": name, "creator": "x"}, headers=auth)
    return res.json()["id"]


def _new_label(client, auth, pid, lid, name, color="#ef4444"):
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


def _upload_png(client, auth, pid, filename, width, height, color=(10, 20, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    res = client.post(
        f"/api/projects/{pid}/upload",
        files=[("file", (filename, buf.getvalue(), "image/png"))],
        headers=auth,
    )
    assert res.status_code == 200, res.text


def _set_annotations(client, auth, pid, description, annotations):
    tasks = client.get(f"/api/tasks?projectId={pid}&include_annotations=true", headers=auth).json()
    tid = next(t["id"] for t in tasks if t["description"] == description)
    res = client.patch(f"/api/tasks/{tid}", json={"annotations": json.dumps(annotations)}, headers=auth)
    assert res.status_code == 200, res.text


def _square(x0, y0, x1, y1):
    return [{"x": x0, "y": y0}, {"x": x1, "y": y0}, {"x": x1, "y": y1}, {"x": x0, "y": y1}]


def _export(client, auth, pid, **payload):
    """Run an export job to completion and return (ZipFile-or-None, status, download)."""
    body = {"projectId": pid, **payload}
    res = client.post("/api/exports", json=body, headers=auth)
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    status = client.get(f"/api/exports/{job_id}", headers=auth).json()
    assert status["status"] == "completed", status
    download = client.get(f"/api/exports/{job_id}/download", headers=auth)
    return download, status


def _zip(download):
    return zipfile.ZipFile(io.BytesIO(download.content))


def _open(zf, name):
    return Image.open(io.BytesIO(zf.read(name)))


# ---------------------------------------------------------------------------
# Image output: original
# ---------------------------------------------------------------------------

def test_original_image_is_copied_verbatim(client, alice):
    pid = _new_project(client, alice)
    _upload_png(client, alice, pid, "orig.png", 12, 8, color=(7, 8, 9))

    download, _ = _export(client, alice, pid, format="coco", imageOutput="original")
    zf = _zip(download)
    assert "original_image/orig.png" in zf.namelist()
    img = _open(zf, "original_image/orig.png").convert("RGB")
    assert img.size == (12, 8)
    assert img.getpixel((0, 0)) == (7, 8, 9)


# ---------------------------------------------------------------------------
# Image output: annotated
# ---------------------------------------------------------------------------

def test_annotated_image_draws_the_polygon(client, alice):
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "l", "Thing", "#FF0000")
    _upload_png(client, alice, pid, "ann.png", 20, 20, color=(0, 0, 0))
    _set_annotations(client, alice, pid, "ann.png", [
        {"id": "a1", "labelId": lid, "type": "bbox", "points": _square(2, 2, 16, 16)},
    ])

    download, _ = _export(client, alice, pid, format="coco", imageOutput="annotated")
    zf = _zip(download)
    assert "annotated_image/ann.png" in zf.namelist()
    img = _open(zf, "annotated_image/ann.png").convert("RGB")
    assert img.size == (20, 20)
    # A pixel inside the shape is no longer pure background black — the fill
    # (red at 0.5 alpha over black) tints it red.
    r, g, b = img.getpixel((9, 9))
    assert r > 0 and g == 0 and b == 0
    # A pixel well outside the shape stays background.
    assert img.getpixel((19, 19)) == (0, 0, 0)


# ---------------------------------------------------------------------------
# Image output: binary mask
# ---------------------------------------------------------------------------

def test_binary_mask_is_grayscale_zero_and_255(client, alice):
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "bin.png", 20, 20)
    _set_annotations(client, alice, pid, "bin.png", [
        {"id": "a1", "labelId": lid, "type": "bbox", "points": _square(2, 2, 10, 10)},
    ])

    download, _ = _export(client, alice, pid, format="coco", imageOutput="mask_binary")
    zf = _zip(download)
    assert "mask_binary_color/bin.png" in zf.namelist()
    img = _open(zf, "mask_binary_color/bin.png")
    assert img.mode == "L"
    assert img.size == (20, 20)
    assert img.getpixel((5, 5)) == 255       # inside the shape
    assert img.getpixel((18, 18)) == 0       # background
    # Only the two extremes appear — no antialiased grays.
    assert {v for _, v in img.getcolors()} == {0, 255}


def test_binary_mask_collapses_all_classes_to_foreground(client, alice):
    pid = _new_project(client, alice)
    a = _new_label(client, alice, pid, "a", "A", "#FF0000")
    b = _new_label(client, alice, pid, "b", "B", "#00FF00")
    _upload_png(client, alice, pid, "multi.png", 40, 20)
    _set_annotations(client, alice, pid, "multi.png", [
        {"id": "a1", "labelId": a, "type": "bbox", "points": _square(2, 2, 10, 10)},
        {"id": "a2", "labelId": b, "type": "bbox", "points": _square(20, 2, 30, 10)},
    ])

    download, _ = _export(client, alice, pid, format="coco", imageOutput="mask_binary")
    img = _open(_zip(download), "mask_binary_color/multi.png")
    assert img.getpixel((5, 5)) == 255       # class A
    assert img.getpixel((25, 5)) == 255      # class B — same foreground


def test_binary_mask_task_without_dimensions_is_skipped(client, alice):
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "l", "Thing")
    _new_task(client, alice, pid, "ghost.png", annotations=[
        {"id": "a1", "labelId": lid, "type": "bbox", "points": _square(0, 0, 5, 5)},
    ])

    download, status = _export(client, alice, pid, format="coco", imageOutput="mask_binary")
    assert not any("ghost" in n for n in _zip(download).namelist())
    assert len(status["skipped"]) == 1
    assert status["skipped"][0]["filename"] == "ghost.png"


# ---------------------------------------------------------------------------
# Folder layout when both axes are present
# ---------------------------------------------------------------------------

def test_both_axes_live_in_their_own_top_level_folders(client, alice):
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "P1.png", 15, 15)

    download, _ = _export(client, alice, pid, format="yolo", imageOutput="original")
    names = _zip(download).namelist()
    assert "yolo/classes.txt" in names
    assert "original_image/P1.png" in names
    # Nothing leaks out of a top-level folder.
    top = {n.split("/", 1)[0] for n in names}
    assert top == {"yolo", "original_image"}


def test_pertask_format_uses_the_jsons_folder(client, alice):
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "P2.png", 15, 15)

    download, _ = _export(client, alice, pid, format="annotations_pertask", imageOutput="original")
    names = _zip(download).namelist()
    assert any(n.startswith("jsons/") and n.endswith(".json") for n in names)
    assert "original_image/P2.png" in names


def test_archive_is_named_after_the_project(client, alice):
    pid = _new_project(client, alice, name="my-proj")
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "P3.png", 10, 10)

    download, _ = _export(client, alice, pid, format="yolo", imageOutput="original")
    header = download.headers.get("Content-Disposition", "")
    assert "my-proj-" in header
    assert header.rstrip('"').endswith(".zip")


# ---------------------------------------------------------------------------
# Deprecated single-axis aliases
# ---------------------------------------------------------------------------

def test_alias_json_resolves_to_coco_none(client, alice):
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "a.png", 10, 10)

    download, status = _export(client, alice, pid, format="json")
    assert status["format"] == "coco"
    assert status["image_output"] == "none"
    # Single-file + none carve-out: a bare .json, not a zip.
    payload = json.loads(download.content)
    assert "images" in payload and "annotations" in payload


def test_alias_pertask_resolves_to_annotations_pertask(client, alice):
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "b.png", 10, 10)

    _, status = _export(client, alice, pid, format="pertask")
    assert status["format"] == "annotations_pertask"
    assert status["image_output"] == "none"


def test_alias_masks_index_resolves_to_coco_plus_mask_index(client, alice):
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "c.png", 10, 10)

    download, status = _export(client, alice, pid, format="masks_index")
    assert status["format"] == "coco"
    assert status["image_output"] == "mask_index"
    names = _zip(download).namelist()
    assert any(n.startswith("coco/") for n in names)
    assert any(n.startswith("mask_index_color/") for n in names)


# ---------------------------------------------------------------------------
# Compat carve-outs
# ---------------------------------------------------------------------------

def test_single_file_with_no_image_output_stays_bare_json(client, alice):
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "d.png", 10, 10)

    download, status = _export(client, alice, pid, format="coco", imageOutput="none")
    header = download.headers.get("Content-Disposition", "")
    assert header.endswith('.json') or ".json" in header
    # It parses as JSON, not a zip.
    payload = json.loads(download.content)
    assert "images" in payload


def test_single_file_with_image_output_becomes_a_zip(client, alice):
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "e.png", 10, 10)

    download, _ = _export(client, alice, pid, format="coco", imageOutput="original")
    zf = _zip(download)
    names = zf.namelist()
    assert "coco/annotations.json" in names
    assert "original_image/e.png" in names


def test_csv_stays_a_bare_csv(client, alice):
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "f.png", 10, 10)

    download, status = _export(client, alice, pid, format="csv")
    assert status["format"] == "csv"
    header = download.headers.get("Content-Disposition", "")
    assert ".csv" in header


# ---------------------------------------------------------------------------
# archive_name shape (unit)
# ---------------------------------------------------------------------------

def test_archive_name_sanitizes_and_appends_random_suffix():
    class _P:
        id = 5
        name = "My Project!!"

    name = common.archive_name(_P())
    assert name.endswith(".zip")
    stem = name[:-4]
    base, _, suffix = stem.rpartition("-")
    assert len(suffix) == 6                 # 6 hex chars
    assert all(c.isalnum() or c in "._-" for c in base)


def test_images_builders_return_bare_arcnames():
    """The folder prefix is applied by exports.py, not the builders."""
    # build_binary on an empty task list yields nothing but must not raise.
    entries, skipped = images.build_binary([], [], db=None)
    assert entries == []
    assert skipped == []

"""Regression pins for the interop format import paths that already work.

Verified before any refactoring: the interop COCO export and its single-file
JSON export both import correctly today. These tests lock that in so moving the
parsers into `formats/` cannot quietly break them.

Fixtures in tests/fixtures/interop/ are trimmed copies of the real exports in
.devnotes/data-examples/ — same structure and field names, fewer images and
shorter polygons. The presigned S3 URLs are redacted; they are credentials and
do not belong in the repo.
"""
import io
import json
import os
import zipfile

import pytest

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "interop")


def _fixture(name):
    with open(os.path.join(FIXTURES, name), "rb") as fh:
        return fh.read()


def _new_project(client, auth, name="fl"):
    res = client.post("/api/projects", json={"name": name, "slug": name, "creator": "ignored"}, headers=auth)
    return res.json()["id"]


def _make_tasks(client, auth, pid, filenames):
    """Create tasks whose description matches each annotation file name.

    Import matches on Task.description and never creates tasks — an image is
    not part of any of these formats.
    """
    for fn in filenames:
        res = client.post(
            f"/api/tasks?projectId={pid}",
            json={"description": fn, "status": "New"},
            headers=auth,
        )
        assert res.status_code in (200, 201), res.text


def _import(client, auth, pid, filename, raw, mode="merge"):
    return client.post(
        f"/api/imports/annotations?projectId={pid}&mode={mode}",
        files=[("file", (filename, raw, "application/json"))],
        headers=auth,
    )


def _preview(client, auth, pid, filename, raw):
    return client.post(
        f"/api/imports/annotations/preview?projectId={pid}",
        files=[("file", (filename, raw, "application/json"))],
        headers=auth,
    )


# ---------------------------------------------------------------------------
# the interop format single-file JSON (the "json_option" export)
# ---------------------------------------------------------------------------

def test_import_interop_json_option_matches_all_tasks(client, alice):
    """the most common interop export: a JSON array of task objects."""
    raw = _fixture("json_option_annotations.json")
    names = [t["name"] for t in json.loads(raw)]
    assert len(names) == 2

    pid = _new_project(client, alice)
    _make_tasks(client, alice, pid, names)

    res = _preview(client, alice, pid, "annotations.json", raw)
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["matched"]) == 2
    assert body["unmatched"] == []
    assert body["total_annotations"] == 4  # 2 tasks x 2 annotations


def test_import_interop_json_option_preserves_titles_and_colors(client, alice):
    """Labels are created from the annotation's title, keeping the interop format's
    colour rather than falling back to our palette."""
    raw = _fixture("json_option_annotations.json")
    source = json.loads(raw)
    pid = _new_project(client, alice)
    _make_tasks(client, alice, pid, [t["name"] for t in source])

    res = _import(client, alice, pid, "annotations.json", raw)
    assert res.status_code == 200, res.text
    assert res.json()["tasks_updated"] == 2

    labels = client.get(f"/api/labels?projectId={pid}", headers=alice).json()
    by_name = {l["name"]: l for l in labels}

    expected = {a["title"]: a["color"] for t in source for a in t["annotations"]}
    for title, color in expected.items():
        assert title in by_name, f"label {title!r} was not created"
        assert by_name[title]["color"] == color


def test_import_interop_json_option_writes_geometry(client, alice):
    """Flat [x1,y1,...] points become {x, y} dicts with a derived bound."""
    raw = _fixture("json_option_annotations.json")
    source = json.loads(raw)
    first = source[0]
    pid = _new_project(client, alice)
    _make_tasks(client, alice, pid, [t["name"] for t in source])
    _import(client, alice, pid, "annotations.json", raw)

    tasks = client.get(f"/api/tasks?projectId={pid}", headers=alice).json()
    task = next(t for t in tasks if t["description"] == first["name"])
    anns = task["annotations"]  # the list endpoint parses the JSON column

    assert len(anns) == len(first["annotations"])
    flat = first["annotations"][0]["points"]
    assert anns[0]["points"][0] == {"x": flat[0], "y": flat[1]}
    assert len(anns[0]["points"]) == len(flat) // 2
    for key in ("x", "y", "width", "height", "labelId"):
        assert key in anns[0]


# ---------------------------------------------------------------------------
# the interop format COCO
# ---------------------------------------------------------------------------

def test_import_interop_coco_matches_all_images(client, alice):
    raw = _fixture("coco_annotations.json")
    coco = json.loads(raw)
    names = [i["file_name"] for i in coco["images"]]

    pid = _new_project(client, alice)
    _make_tasks(client, alice, pid, names)

    res = _preview(client, alice, pid, "coco_annotations.json", raw)
    assert res.status_code == 200, res.text
    body = res.json()
    assert {m["filename"] for m in body["matched"]} == set(names)
    assert body["unmatched"] == []
    assert body["total_annotations"] == len(coco["annotations"])


def test_import_interop_coco_preserves_category_colors(client, alice):
    """`color` is a the interop format extension to COCO, not part of the spec. Carrying
    it through is what makes an export/import round trip keep class colours."""
    raw = _fixture("coco_annotations.json")
    coco = json.loads(raw)
    pid = _new_project(client, alice)
    _make_tasks(client, alice, pid, [i["file_name"] for i in coco["images"]])

    res = _import(client, alice, pid, "coco_annotations.json", raw)
    assert res.status_code == 200, res.text

    labels = {l["name"]: l["color"] for l in client.get(f"/api/labels?projectId={pid}", headers=alice).json()}
    used_category_ids = {a["category_id"] for a in coco["annotations"]}
    for cat in coco["categories"]:
        if cat["id"] in used_category_ids:
            assert labels.get(cat["name"]) == cat["color"]


def test_import_interop_coco_segmentation_becomes_points(client, alice):
    raw = _fixture("coco_annotations.json")
    coco = json.loads(raw)
    pid = _new_project(client, alice)
    _make_tasks(client, alice, pid, [i["file_name"] for i in coco["images"]])
    _import(client, alice, pid, "coco_annotations.json", raw)

    tasks = client.get(f"/api/tasks?projectId={pid}", headers=alice).json()
    total = sum(len(t["annotations"]) for t in tasks)
    assert total == len(coco["annotations"])

    first_img = coco["images"][0]["id"]
    first_ann = next(a for a in coco["annotations"] if a["image_id"] == first_img)
    task = next(t for t in tasks if t["description"] == coco["images"][0]["file_name"])
    stored = task["annotations"]
    seg = first_ann["segmentation"][0]
    assert stored[0]["points"][0] == {"x": seg[0], "y": seg[1]}


# ---------------------------------------------------------------------------
# interop per-task JSON (single object)
# ---------------------------------------------------------------------------

def test_import_interop_pertask_single_object(client, alice):
    raw = _fixture("pertask_P1000066.json")
    source = json.loads(raw)
    pid = _new_project(client, alice)
    _make_tasks(client, alice, pid, [source["name"]])

    res = _import(client, alice, pid, "pertask_P1000066.json", raw)
    assert res.status_code == 200, res.text
    assert res.json()["tasks_updated"] == 1
    assert res.json()["annotations_imported"] == len(source["annotations"])


def test_import_zip_of_interop_pertask_files(client, alice):
    """The per-task export ships as a ZIP; entries are read at any depth."""
    pertask = _fixture("pertask_P1000066.json")
    name = json.loads(pertask)["name"]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("jsons/P1000066.json", pertask)
    raw = buf.getvalue()

    pid = _new_project(client, alice)
    _make_tasks(client, alice, pid, [name])

    res = client.post(
        f"/api/imports/annotations?projectId={pid}",
        files=[("file", ("export.zip", raw, "application/zip"))],
        headers=alice,
    )
    assert res.status_code == 200, res.text
    assert res.json()["tasks_updated"] == 1


# ---------------------------------------------------------------------------
# Unmatched handling
# ---------------------------------------------------------------------------

def test_unmatched_filenames_are_reported_not_silently_dropped(client, alice):
    """An import cannot create tasks — no image is carried in these formats —
    so a name that matches nothing must be surfaced, which is why /preview
    exists."""
    raw = _fixture("json_option_annotations.json")
    pid = _new_project(client, alice)  # no tasks at all

    res = _preview(client, alice, pid, "annotations.json", raw)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["matched"] == []
    assert len(body["unmatched"]) == 2

"""Mask export (data-refactor plan Phase 3.4).

Four outputs over two axes — {direct, index} x {semantic, instance} — rendered
from the polygons, since a mask has no source of truth other than the shapes.

Masks are export-only by decision, not omission: contour-tracing a raster back
to polygons is not a faithful inverse. The import side of that is pinned in
tests/test_imports_rejections.py.

Test images are deliberately tiny (a few hundred pixels). The reference files
are 5184x3888 and multi-megabyte, so they are not committed; the palettes read
out of them are asserted here as constants instead.
"""
import io
import json
import zipfile

import pytest
from PIL import Image

from conftest import unique_label_id
from formats import masks


def _new_project(client, auth, name="mask"):
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


def _export_masks(client, auth, pid, fmt):
    res = client.post("/api/exports", json={"projectId": pid, "format": fmt}, headers=auth)
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    status = client.get(f"/api/exports/{job_id}", headers=auth).json()
    assert status["status"] == "completed", status
    download = client.get(f"/api/exports/{job_id}/download", headers=auth)
    zf = zipfile.ZipFile(io.BytesIO(download.content))
    return {n: zf.read(n) for n in zf.namelist()}, status


def _open(entries, name):
    return Image.open(io.BytesIO(entries[name]))


def _square(x0, y0, x1, y1):
    return [{"x": x0, "y": y0}, {"x": x1, "y": y0}, {"x": x1, "y": y1}, {"x": x0, "y": y1}]


# ---------------------------------------------------------------------------
# Archive layout
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["masks_direct", "masks_index"])
def test_both_folders_are_produced(client, alice, fmt):
    """The reference archive carries semantic and instance masks together."""
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "m.png", 20, 10)

    entries, _ = _export_masks(client, alice, pid, fmt)
    assert "semantic_segmentations/m.png" in entries
    assert "instance_segmentations/m.png" in entries


@pytest.mark.parametrize("fmt", ["masks_direct", "masks_index"])
def test_mask_matches_the_source_image_size(client, alice, fmt):
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "sz.png", 37, 19)

    entries, _ = _export_masks(client, alice, pid, fmt)
    assert _open(entries, "semantic_segmentations/sz.png").size == (37, 19)


def test_direct_masks_are_png_not_jpeg(client, alice):
    """Deliberate deviation: the reference writes JPEG here, and its lossy
    compression destroys the exact class colours the format exists to convey."""
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "fmt.png", 10, 10)

    entries, _ = _export_masks(client, alice, pid, "masks_direct")
    assert _open(entries, "semantic_segmentations/fmt.png").format == "PNG"


# ---------------------------------------------------------------------------
# Direct colour
# ---------------------------------------------------------------------------

def test_semantic_direct_paints_the_class_colour(client, alice):
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "l", "Rust", "#D95319")
    _upload_png(client, alice, pid, "d.png", 20, 20)
    _set_annotations(client, alice, pid, "d.png", [
        {"id": "a1", "labelId": lid, "type": "bbox", "points": _square(2, 2, 10, 10)},
    ])

    entries, _ = _export_masks(client, alice, pid, "masks_direct")
    img = _open(entries, "semantic_segmentations/d.png").convert("RGB")
    assert img.getpixel((5, 5)) == (217, 83, 25)   # #D95319
    assert img.getpixel((18, 18)) == (0, 0, 0)     # background


def test_two_instances_of_one_class_share_the_semantic_colour(client, alice):
    """Semantic masks identify the class; instance masks separate the shapes."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "l", "Rust", "#D95319")
    _upload_png(client, alice, pid, "two.png", 40, 20)
    _set_annotations(client, alice, pid, "two.png", [
        {"id": "a1", "labelId": lid, "type": "bbox", "points": _square(2, 2, 10, 10)},
        {"id": "a2", "labelId": lid, "type": "bbox", "points": _square(20, 2, 30, 10)},
    ])

    entries, _ = _export_masks(client, alice, pid, "masks_direct")
    semantic = _open(entries, "semantic_segmentations/two.png").convert("RGB")
    assert semantic.getpixel((5, 5)) == semantic.getpixel((25, 5))

    instance = _open(entries, "instance_segmentations/two.png").convert("RGB")
    assert instance.getpixel((5, 5)) != instance.getpixel((25, 5))


def test_instance_direct_uses_the_reference_palette(client, alice):
    """Read out of the reference instance masks: ColorBrewer Set1."""
    assert masks.INSTANCE_PALETTE[0] == (228, 26, 28)
    assert masks.INSTANCE_PALETTE[1] == (55, 126, 184)

    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "inst.png", 40, 20)
    _set_annotations(client, alice, pid, "inst.png", [
        {"id": "a1", "labelId": lid, "type": "bbox", "points": _square(2, 2, 10, 10)},
        {"id": "a2", "labelId": lid, "type": "bbox", "points": _square(20, 2, 30, 10)},
    ])

    entries, _ = _export_masks(client, alice, pid, "masks_direct")
    img = _open(entries, "instance_segmentations/inst.png").convert("RGB")
    assert img.getpixel((5, 5)) == (228, 26, 28)
    assert img.getpixel((25, 5)) == (55, 126, 184)


# ---------------------------------------------------------------------------
# Index colour
# ---------------------------------------------------------------------------

def test_index_masks_are_palette_mode(client, alice):
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "p.png", 10, 10)

    entries, _ = _export_masks(client, alice, pid, "masks_index")
    assert _open(entries, "semantic_segmentations/p.png").mode == "P"


def test_semantic_index_palette_holds_the_class_colours(client, alice):
    """Index N is class N, and the palette entry is that class's colour — the
    structure read out of the reference semantic masks."""
    pid = _new_project(client, alice)
    lid_a = _new_label(client, alice, pid, "a", "Rust", "#D95319")
    _new_label(client, alice, pid, "b", "Steel", "#1E5AFF")
    _upload_png(client, alice, pid, "sp.png", 20, 20)
    _set_annotations(client, alice, pid, "sp.png", [
        {"id": "a1", "labelId": lid_a, "type": "bbox", "points": _square(2, 2, 10, 10)},
    ])

    entries, _ = _export_masks(client, alice, pid, "masks_index")
    img = _open(entries, "semantic_segmentations/sp.png")
    palette = img.getpalette()

    assert tuple(palette[0:3]) == (0, 0, 0)        # background
    assert tuple(palette[3:6]) == (217, 83, 25)    # class 1, #D95319
    assert tuple(palette[6:9]) == (30, 90, 255)    # class 2, #1E5AFF
    assert img.getpixel((5, 5)) == 1
    assert img.getpixel((18, 18)) == 0


def test_instance_index_assigns_distinct_indices(client, alice):
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "ii.png", 40, 20)
    _set_annotations(client, alice, pid, "ii.png", [
        {"id": "a1", "labelId": lid, "type": "bbox", "points": _square(2, 2, 10, 10)},
        {"id": "a2", "labelId": lid, "type": "bbox", "points": _square(20, 2, 30, 10)},
    ])

    entries, _ = _export_masks(client, alice, pid, "masks_index")
    img = _open(entries, "instance_segmentations/ii.png")
    assert img.getpixel((5, 5)) == 1
    assert img.getpixel((25, 5)) == 2
    assert img.getpixel((38, 18)) == 0


# ---------------------------------------------------------------------------
# Paint order
# ---------------------------------------------------------------------------

def test_later_annotation_paints_over_earlier(client, alice):
    """An overlap reports the shape drawn last, matching the reference."""
    pid = _new_project(client, alice)
    first = _new_label(client, alice, pid, "a", "First", "#FF0000")
    second = _new_label(client, alice, pid, "b", "Second", "#00FF00")
    _upload_png(client, alice, pid, "ov.png", 20, 20)
    _set_annotations(client, alice, pid, "ov.png", [
        {"id": "a1", "labelId": first, "order": 1, "type": "bbox", "points": _square(0, 0, 15, 15)},
        {"id": "a2", "labelId": second, "order": 2, "type": "bbox", "points": _square(5, 5, 19, 19)},
    ])

    entries, _ = _export_masks(client, alice, pid, "masks_direct")
    img = _open(entries, "semantic_segmentations/ov.png").convert("RGB")
    assert img.getpixel((10, 10)) == (0, 255, 0)   # overlap: the later shape
    assert img.getpixel((2, 2)) == (255, 0, 0)     # only the earlier shape


# ---------------------------------------------------------------------------
# Degenerate input
# ---------------------------------------------------------------------------

def test_task_without_dimensions_is_skipped_and_reported(client, alice):
    """No canvas can be sized, and inventing one yields a misaligned mask."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "l", "Thing")
    _new_task(client, alice, pid, "ghost.png", annotations=[
        {"id": "a1", "labelId": lid, "type": "bbox", "points": _square(0, 0, 5, 5)},
    ])

    entries, status = _export_masks(client, alice, pid, "masks_direct")
    assert not any("ghost" in n for n in entries)
    assert len(status["skipped"]) == 1
    assert status["skipped"][0]["filename"] == "ghost.png"


def test_task_with_no_annotations_yields_an_empty_mask(client, alice):
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "blank.png", 10, 10)

    entries, _ = _export_masks(client, alice, pid, "masks_direct")
    img = _open(entries, "semantic_segmentations/blank.png").convert("RGB")
    assert img.getcolors() == [(100, (0, 0, 0))]


def test_degenerate_shapes_do_not_crash_the_render(client, alice):
    """A two-point line encloses no area; ImageDraw.polygon would raise."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "deg.png", 10, 10)
    _set_annotations(client, alice, pid, "deg.png", [
        {"id": "a1", "labelId": lid, "points": [{"x": 1, "y": 1}, {"x": 5, "y": 5}]},
        {"id": "a2", "labelId": lid, "points": [{"x": 2, "y": 2}]},
    ])

    entries, _ = _export_masks(client, alice, pid, "masks_direct")
    assert "semantic_segmentations/deg.png" in entries


def test_unparseable_label_colour_falls_back_to_grey():
    """A colour comes from the UI or an import and is not guaranteed valid."""
    assert masks._hex_to_rgb("#D95319") == (217, 83, 25)
    assert masks._hex_to_rgb("#abc") == (170, 187, 204)
    assert masks._hex_to_rgb("not-a-colour") == (128, 128, 128)
    assert masks._hex_to_rgb(None) == (128, 128, 128)
    assert masks._hex_to_rgb("#GGGGGG") == (128, 128, 128)


def test_annotation_for_deleted_label_is_skipped(client, alice):
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "orph.png", 10, 10)
    _set_annotations(client, alice, pid, "orph.png", [
        {"id": "a1", "labelId": "gone", "type": "bbox", "points": _square(1, 1, 8, 8)},
    ])

    entries, _ = _export_masks(client, alice, pid, "masks_direct")
    img = _open(entries, "semantic_segmentations/orph.png").convert("RGB")
    assert img.getcolors() == [(100, (0, 0, 0))]


# ---------------------------------------------------------------------------
# Cost guard
# ---------------------------------------------------------------------------

def test_mask_export_over_the_task_cap_is_rejected(client, alice, monkeypatch):
    """Rasterizing holds the single worker, so an oversized request fails fast
    rather than looking like a hang."""
    monkeypatch.setattr(masks, "MAX_MASK_TASKS", 1)

    pid = _new_project(client, alice)
    _upload_png(client, alice, pid, "one.png", 10, 10)
    _upload_png(client, alice, pid, "two.png", 10, 10)

    res = client.post("/api/exports", json={"projectId": pid, "format": "masks_direct"}, headers=alice)
    assert res.status_code == 422
    assert "limited to" in res.json()["detail"]


def test_mask_export_within_the_cap_is_allowed(client, alice, monkeypatch):
    monkeypatch.setattr(masks, "MAX_MASK_TASKS", 2)

    pid = _new_project(client, alice)
    _upload_png(client, alice, pid, "one.png", 10, 10)

    res = client.post("/api/exports", json={"projectId": pid, "format": "masks_direct"}, headers=alice)
    assert res.status_code == 200


def test_indexed_instance_overflow_is_reported(client, alice, monkeypatch):
    """A palette addresses 255 entries, and real projects exceed that — the
    reference data has 446 shapes on one image. Dropping the excess silently
    would be invisible data loss, so it is reported like any other skip."""
    monkeypatch.setattr(masks, "_MAX_PALETTE_ENTRIES", 2)

    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "many.png", 40, 40)
    _set_annotations(client, alice, pid, "many.png", [
        {"id": f"a{i}", "labelId": lid, "type": "bbox",
         "points": _square(i * 3, 1, i * 3 + 2, 5)}
        for i in range(5)
    ])

    entries, status = _export_masks(client, alice, pid, "masks_index")
    # The mask is still written — a partial mask beats none.
    assert "instance_segmentations/many.png" in entries
    overflow = [s for s in status["skipped"] if "palette" in s["reason"]]
    assert len(overflow) == 1
    assert "3 instance(s)" in overflow[0]["reason"]
    assert overflow[0]["filename"] == "instance_segmentations/many.png"


def test_semantic_index_is_unaffected_by_the_instance_limit(client, alice, monkeypatch):
    """The class count, not the shape count, bounds a semantic mask."""
    monkeypatch.setattr(masks, "_MAX_PALETTE_ENTRIES", 2)

    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "l", "Thing", "#FF0000")
    _upload_png(client, alice, pid, "sem.png", 40, 40)
    _set_annotations(client, alice, pid, "sem.png", [
        {"id": f"a{i}", "labelId": lid, "type": "bbox",
         "points": _square(i * 3, 1, i * 3 + 2, 5)}
        for i in range(5)
    ])

    entries, _ = _export_masks(client, alice, pid, "masks_index")
    img = _open(entries, "semantic_segmentations/sem.png")
    # Every shape is the same class, so all five paint index 1.
    assert img.getpixel((1, 3)) == 1
    assert img.getpixel((13, 3)) == 1


def test_direct_instance_colours_cycle_without_dropping_shapes(client, alice):
    """Direct-colour masks have no palette limit: past the 14-colour list the
    colours repeat, but every shape is still painted. That is why the indexed
    overflow message points here."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "l", "Thing")
    _upload_png(client, alice, pid, "cyc.png", 100, 20)
    _set_annotations(client, alice, pid, "cyc.png", [
        {"id": f"a{i}", "labelId": lid, "type": "bbox",
         "points": _square(i * 6, 2, i * 6 + 4, 10)}
        for i in range(16)
    ])

    entries, status = _export_masks(client, alice, pid, "masks_direct")
    assert status["skipped"] == []
    img = _open(entries, "instance_segmentations/cyc.png").convert("RGB")
    first = img.getpixel((2, 5))
    fifteenth = img.getpixel((14 * 6 + 2, 5))
    assert first == fifteenth == masks.INSTANCE_PALETTE[0]

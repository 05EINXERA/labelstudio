"""Unit tests for formats/common.py (plan Phase 1.2-1.5).

These are pure helpers, so they run without a TestClient or a database
session — the reason the format logic was lifted out of the routers.
"""
import logging
import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import models
from formats.common import (
    annotation_type_of,
    bbox_of,
    flatten_points,
    from_external_status,
    image_size,
    is_annotation,
    measure_image,
    points_of,
    polygon_area,
    safe_stem,
    to_external_status,
    unflatten_points,
    value_from_name,
    values_for_labels,
)


def _label(name, id_="l1"):
    return models.Label(id=id_, name=name, color="#ff0000", project_id=1)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def test_polygon_area_uses_shoelace_not_bbox():
    """A triangle in a 10x10 box has area 50, not the bbox's 100.

    This is the COCO `area` bug: bbox_w * bbox_h overstates every
    non-rectangular shape.
    """
    triangle = [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 0, "y": 10}]
    assert polygon_area(triangle) == pytest.approx(50.0)
    x, y, w, h = bbox_of(triangle)
    assert w * h == 100.0


def test_polygon_area_matches_bbox_for_a_rectangle():
    rect = [{"x": 2, "y": 3}, {"x": 12, "y": 3}, {"x": 12, "y": 8}, {"x": 2, "y": 8}]
    assert polygon_area(rect) == pytest.approx(50.0)


def test_polygon_area_is_orientation_independent():
    """Clockwise and counter-clockwise windings give the same positive area."""
    ccw = [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}]
    cw = list(reversed(ccw))
    assert polygon_area(ccw) == pytest.approx(polygon_area(cw))
    assert polygon_area(cw) > 0


def test_polygon_area_degenerate_is_zero():
    assert polygon_area([]) == 0.0
    assert polygon_area([{"x": 1, "y": 1}]) == 0.0
    assert polygon_area([{"x": 1, "y": 1}, {"x": 2, "y": 2}]) == 0.0


def test_points_of_falls_back_to_bbox_corners():
    ann = {"x": 5, "y": 10, "width": 20, "height": 30}
    assert points_of(ann) == [
        {"x": 5, "y": 10}, {"x": 25, "y": 10}, {"x": 25, "y": 40}, {"x": 5, "y": 40},
    ]


def test_flatten_unflatten_round_trip():
    points = [{"x": 1.5, "y": 2.5}, {"x": 3.25, "y": 4.75}]
    assert flatten_points(points) == [1.5, 2.5, 3.25, 4.75]
    assert unflatten_points(flatten_points(points)) == points


def test_unflatten_drops_trailing_odd_value():
    """A truncated coordinate list must not produce a half-point or raise."""
    assert unflatten_points([1, 2, 3]) == [{"x": 1, "y": 2}]


# ---------------------------------------------------------------------------
# Annotation type (gap G1)
# ---------------------------------------------------------------------------

def test_explicit_type_wins_over_inference():
    """A 4-point rectangle explicitly marked polygon stays a polygon."""
    rect = [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}, {"x": 0, "y": 10}]
    assert annotation_type_of({"type": "polygon", "points": rect}) == "polygon"
    assert annotation_type_of({"type": "bbox", "points": rect}) == "bbox"


def test_infers_bbox_for_axis_aligned_rectangle():
    rect = [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}, {"x": 0, "y": 10}]
    assert annotation_type_of({"points": rect}) == "bbox"


def test_infers_polygon_for_non_rectangle():
    tri = [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 5, "y": 10}]
    assert annotation_type_of({"points": tri}) == "polygon"


def test_infers_polygon_for_rotated_square():
    """Four points, equal sides, but not axis-aligned — a polygon, not a box."""
    diamond = [{"x": 5, "y": 0}, {"x": 10, "y": 5}, {"x": 5, "y": 10}, {"x": 0, "y": 5}]
    assert annotation_type_of({"points": diamond}) == "polygon"


def test_infers_polygon_for_five_point_shape():
    pts = [{"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 10, "y": 5},
           {"x": 5, "y": 10}, {"x": 0, "y": 10}]
    assert annotation_type_of({"points": pts}) == "polygon"


def test_rectangle_within_rounding_tolerance_is_still_bbox():
    """2 dp storage means a round-tripped box is never exactly equal."""
    rect = [{"x": 0.0, "y": 0.0}, {"x": 10.01, "y": 0.0},
            {"x": 10.0, "y": 10.0}, {"x": 0.0, "y": 9.99}]
    assert annotation_type_of({"points": rect}) == "bbox"


def test_bbox_only_annotation_infers_bbox():
    """No points at all — the x/y/w/h fallback is a rectangle by construction."""
    assert annotation_type_of({"x": 1, "y": 2, "width": 3, "height": 4}) == "bbox"


def test_is_annotation_excludes_comments():
    assert is_annotation({"type": "polygon"})
    assert is_annotation({"labelId": "x"})
    assert not is_annotation({"type": "comment"})
    assert not is_annotation("not a dict")
    assert not is_annotation(None)


# ---------------------------------------------------------------------------
# Value derivation (gap G5)
# ---------------------------------------------------------------------------

def test_value_from_name_matches_interop_sample():
    """Verified against .devnotes/data-examples/imports/classes.json.

    Note the trailing "." survives — the strip set is punctuation-specific, not
    a general slug.
    """
    assert value_from_name("Dirt 2 (Light Rust Stains, Water Stains, etc.)") == \
        "Dirt2LightRustStainsWaterStainsetc."
    assert value_from_name("Rust Area") == "RustArea"
    assert value_from_name("AC Paint / Exposed Steel Plate") == "ACPaintExposedSteelPlate"


def test_value_from_name_handles_empty():
    assert value_from_name("") == ""
    assert value_from_name(None) == ""


def test_values_for_labels_disambiguates_collisions(caplog):
    """"A/B" and "AB" both strip to "AB" — they must not merge.

    YOLO's classes.txt uses the value as the class identity, so a collision
    there corrupts every class index in the export.
    """
    labels = [_label("A/B", "id1"), _label("AB", "id2")]
    with caplog.at_level(logging.WARNING):
        values = values_for_labels(labels)

    assert values["id1"] == "AB"
    assert values["id2"] == "AB-2"
    assert len(set(values.values())) == 2
    assert "AB" in caplog.text


def test_values_for_labels_leaves_distinct_names_alone():
    labels = [_label("Rust Area", "id1"), _label("Sound Area", "id2")]
    assert values_for_labels(labels) == {"id1": "RustArea", "id2": "SoundArea"}


def test_values_for_labels_handles_three_way_collision():
    labels = [_label("A B", "id1"), _label("AB", "id2"), _label("A,B", "id3")]
    values = values_for_labels(labels)
    assert len(set(values.values())) == 3


# ---------------------------------------------------------------------------
# Status vocabulary (gap G4)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ours", ["New", "In Progress", "Completed", "Approved"])
def test_status_round_trips_for_every_known_status(ours):
    status, external = to_external_status(ours)
    assert from_external_status(status, external) == ours


def test_approved_is_completed_plus_external_status():
    """the interop format splits across two fields what we keep in one column."""
    assert to_external_status("Approved") == ("completed", "approved")
    assert to_external_status("Completed") == ("completed", "")


def test_external_status_approved_wins():
    assert from_external_status("completed", "approved") == "Approved"
    assert from_external_status("completed", "") == "Completed"


def test_unknown_status_passes_through_on_export(caplog):
    with caplog.at_level(logging.WARNING):
        assert to_external_status("Archived") == ("Archived", "")
    assert "Archived" in caplog.text


def test_unknown_status_becomes_new_on_import(caplog):
    """An unrecognised status must not silently become Completed."""
    with caplog.at_level(logging.WARNING):
        assert from_external_status("some_future_state") == "New"
    assert "some_future_state" in caplog.text


def test_missing_status_becomes_new_without_warning(caplog):
    with caplog.at_level(logging.WARNING):
        assert from_external_status(None) == "New"
        assert from_external_status("") == "New"
    assert caplog.text == ""


# ---------------------------------------------------------------------------
# Image dimensions (gap G2)
# ---------------------------------------------------------------------------

def test_image_size_prefers_stored_columns():
    """No disk access when the columns are populated."""
    task = models.Task(id=1, image_path="uploads/does-not-exist.png",
                       image_width=640, image_height=480)
    assert image_size(task) == (640, 480)


def test_image_size_returns_zero_for_missing_file(caplog):
    """A missing image degrades, it never raises — one bad file must not fail
    a whole export."""
    task = models.Task(id=1, image_path="uploads/nope.png")
    with caplog.at_level(logging.WARNING):
        assert image_size(task) == (0, 0)
    assert "nope.png" in caplog.text


def test_image_size_returns_zero_when_no_path():
    assert image_size(models.Task(id=1)) == (0, 0)


def test_measure_image_reads_real_dimensions(tmp_path):
    path = tmp_path / "img.png"
    Image.new("RGB", (321, 123)).save(path)
    assert measure_image(str(path)) == (321, 123)


def test_measure_image_returns_none_for_unreadable(tmp_path, caplog):
    """None, not 0 — an unreadable file stays eligible for a later backfill
    instead of being recorded as a genuine 0x0."""
    path = tmp_path / "broken.png"
    path.write_bytes(b"not an image")
    with caplog.at_level(logging.WARNING):
        assert measure_image(str(path)) == (None, None)


# ---------------------------------------------------------------------------
# Archive naming
# ---------------------------------------------------------------------------

def test_safe_stem_strips_extension():
    assert safe_stem(models.Task(id=1, description="P1000015.JPG")) == "P1000015"


def test_safe_stem_rejects_traversal_and_directories():
    """Client-supplied filenames reach this; a path is discarded, not sanitised
    piecemeal."""
    assert safe_stem(models.Task(id=7, description="../../etc/passwd")) == "passwd"
    assert safe_stem(models.Task(id=7, description="C:\\Users\\x\\img.png")) == "img"
    assert safe_stem(models.Task(id=7, description="..")) == "task-7"
    assert safe_stem(models.Task(id=7, description="")) == "task-7"
    assert safe_stem(models.Task(id=7, description=None)) == "task-7"


def test_legacy_box_spelling_is_treated_as_bbox():
    """The auto-detect path wrote type "box" before the vocabulary was
    unified; those annotations are still in the database."""
    tri = [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 5, "y": 10}]
    assert annotation_type_of({"type": "box", "points": tri}) == "bbox"


def test_unrecognised_type_falls_through_to_inference():
    """An unknown string must not be trusted — infer from the geometry."""
    tri = [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 5, "y": 10}]
    assert annotation_type_of({"type": "circle", "points": tri}) == "polygon"

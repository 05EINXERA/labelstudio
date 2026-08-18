"""Unit tests for `formats.annotation_diff`.

The asymmetry under test: `is_pure_append` guards whether a history row may be
*skipped*, so a false positive destroys recoverable work while a false negative
only costs disk. Most of these cases therefore assert False.
"""

import json

import pytest

from formats.annotation_diff import is_pure_append, total_point_count


def _blob(objects):
    return json.dumps(objects)


def _box(oid="a", x=10, y=20, label=1):
    return {"id": oid, "type": "rect", "x": x, "y": y,
            "width": 30, "height": 40, "labelId": label}


def _poly(oid="p", points=None, label=2):
    return {"id": oid, "type": "polygon", "labelId": label,
            "points": points if points is not None else [{"x": 1, "y": 1}]}


# --- the case this module exists for -------------------------------------


def test_vertex_inserted_mid_array_is_append():
    """The observed production case: history rows 38196 -> 38200.

    One polygon of a 56-object blob gained a single vertex at index 877 of
    1233. A prefix check would call this destructive; it is not.
    """
    old_points = [{"x": i, "y": i} for i in range(1233)]
    new_points = list(old_points)
    new_points.insert(877, {"x": 4688.9, "y": 3456.24})

    others = [_box(f"o{i}", x=i) for i in range(55)]
    old = _blob(others + [_poly("big", old_points)])
    new = _blob(others + [_poly("big", new_points)])

    assert is_pure_append(old, new) is True


def test_vertex_appended_at_tail_is_append():
    old = _blob([_poly("p", [{"x": 1, "y": 1}, {"x": 2, "y": 2}])])
    new = _blob([_poly("p", [{"x": 1, "y": 1}, {"x": 2, "y": 2}, {"x": 3, "y": 3}])])
    assert is_pure_append(old, new) is True


def test_many_vertices_added_at_once():
    old = _blob([_poly("p", [{"x": 1, "y": 1}])])
    new = _blob([_poly("p", [{"x": 0, "y": 0}, {"x": 1, "y": 1}, {"x": 2, "y": 2}])])
    assert is_pure_append(old, new) is True


def test_identical_blobs_are_append():
    same = _blob([_box(), _poly()])
    assert is_pure_append(same, same) is True


def test_new_object_added():
    old = _blob([_box("a")])
    new = _blob([_box("a"), _poly("b")])
    assert is_pure_append(old, new) is True


def test_object_gains_a_new_field():
    old = _blob([{"id": "a", "type": "rect", "x": 1}])
    new = _blob([{"id": "a", "type": "rect", "x": 1, "note": "hi"}])
    assert is_pure_append(old, new) is True


def test_object_gains_points_where_it_had_none():
    old = _blob([{"id": "a", "type": "polygon", "labelId": 1}])
    new = _blob([{"id": "a", "type": "polygon", "labelId": 1,
                  "points": [{"x": 1, "y": 1}]}])
    assert is_pure_append(old, new) is True


# --- destructive: every one of these must be preserved -------------------


def test_vertex_removed_is_not_append():
    old = _blob([_poly("p", [{"x": 1, "y": 1}, {"x": 2, "y": 2}, {"x": 3, "y": 3}])])
    new = _blob([_poly("p", [{"x": 1, "y": 1}, {"x": 3, "y": 3}])])
    assert is_pure_append(old, new) is False


def test_polygon_truncated_to_stub_is_not_append():
    """Vertex-level loss the object count cannot see: 1233 -> 3, count stays 1."""
    old = _blob([_poly("p", [{"x": i, "y": i} for i in range(1233)])])
    new = _blob([_poly("p", [{"x": i, "y": i} for i in range(3)])])
    assert is_pure_append(old, new) is False


def test_object_deleted_is_not_append():
    old = _blob([_box("a"), _box("b")])
    new = _blob([_box("a")])
    assert is_pure_append(old, new) is False


def test_object_replaced_keeping_count_is_not_append():
    old = _blob([_box("a"), _box("b")])
    new = _blob([_box("a"), _box("c")])
    assert is_pure_append(old, new) is False


def test_coordinate_changed_is_not_append():
    old = _blob([_box("a", x=10)])
    new = _blob([_box("a", x=99)])
    assert is_pure_append(old, new) is False


def test_label_changed_is_not_append():
    old = _blob([_box("a", label=1)])
    new = _blob([_box("a", label=7)])
    assert is_pure_append(old, new) is False


def test_field_removed_is_not_append():
    old = _blob([{"id": "a", "type": "rect", "x": 1, "y": 2}])
    new = _blob([{"id": "a", "type": "rect", "x": 1}])
    assert is_pure_append(old, new) is False


def test_points_reordered_is_not_append():
    old = _blob([_poly("p", [{"x": 1, "y": 1}, {"x": 2, "y": 2}])])
    new = _blob([_poly("p", [{"x": 2, "y": 2}, {"x": 1, "y": 1}])])
    assert is_pure_append(old, new) is False


def test_points_replaced_wholesale_is_not_append():
    old = _blob([_poly("p", [{"x": 1, "y": 1}, {"x": 2, "y": 2}])])
    new = _blob([_poly("p", [{"x": 8, "y": 8}, {"x": 9, "y": 9}, {"x": 7, "y": 7}])])
    assert is_pure_append(old, new) is False


def test_points_became_non_list_is_not_append():
    old = _blob([_poly("p", [{"x": 1, "y": 1}])])
    new = _blob([{"id": "p", "type": "polygon", "labelId": 2, "points": "nope"}])
    assert is_pure_append(old, new) is False


def test_wipe_to_empty_is_not_append():
    old = _blob([_box("a")])
    assert is_pure_append(old, "[]") is False


# --- unusable input: always conservative ---------------------------------


@pytest.mark.parametrize("bad", [None, "", "   ", "not json", "{}", '"str"', "123", "null"])
def test_unparseable_new_blob_is_not_append(bad):
    old = _blob([_box("a")])
    assert is_pure_append(old, bad) is False


@pytest.mark.parametrize("bad", [None, "", "not json", "{}"])
def test_unparseable_old_blob_is_not_append(bad):
    new = _blob([_box("a")])
    assert is_pure_append(bad, new) is False


def test_missing_id_is_not_append():
    old = _blob([{"type": "rect", "x": 1}])
    new = _blob([{"type": "rect", "x": 1}, {"type": "rect", "x": 2}])
    assert is_pure_append(old, new) is False


def test_duplicate_ids_are_not_append():
    old = _blob([_box("a"), _box("a", x=50)])
    new = _blob([_box("a"), _box("a", x=50), _box("b")])
    assert is_pure_append(old, new) is False


def test_non_dict_object_is_not_append():
    old = _blob(["just a string"])
    new = _blob(["just a string", "another"])
    assert is_pure_append(old, new) is False


def test_empty_old_list_is_append():
    assert is_pure_append("[]", _blob([_box("a")])) is True


# --- total_point_count ---------------------------------------------------


def test_total_point_count_sums_across_objects():
    blob = _blob([
        _poly("a", [{"x": 1, "y": 1}, {"x": 2, "y": 2}]),
        _poly("b", [{"x": 3, "y": 3}]),
        _box("c"),
    ])
    assert total_point_count(blob) == 3


def test_total_point_count_zero_without_polygons():
    assert total_point_count(_blob([_box("a"), _box("b")])) == 0


@pytest.mark.parametrize("bad", [None, "", "not json", "{}"])
def test_total_point_count_unusable_is_negative_one(bad):
    assert total_point_count(bad) == -1


# --- derived bounding box -------------------------------------------------
#
# A polygon extended outward arrives with x/y/width/height recomputed by
# `updateAnnotationBounds` (frontend/js/canvas/geometry.js), so a real vertex
# append also mutates those fields. They are ignored *only* when the object is
# point-backed and its stored bounds match its own vertices; the tests below
# pin both halves of that, because the exemption is the one place this module
# deliberately looks past a changed field.


def _bbox(points):
    xs = [p["x"] for p in points]
    ys = [p["y"] for p in points]
    return {"x": round(min(xs), 2), "y": round(min(ys), 2),
            "width": round(max(xs) - min(xs), 2),
            "height": round(max(ys) - min(ys), 2)}


def _poly_bb(oid, points, label=2, **extra):
    obj = {"id": oid, "type": "polygon", "labelId": label, "points": points}
    obj.update(_bbox(points))
    obj.update(extra)
    return obj


def test_polygon_grown_outward_is_append_despite_new_bbox():
    """The dev-server case: points 42 -> 53 with width 563.48 -> 565.52."""
    old_points = [{"x": float(i), "y": float(i)} for i in range(42)]
    new_points = old_points + [{"x": 99.0, "y": 99.0}]

    old = _blob([_poly_bb("p", old_points)])
    new = _blob([_poly_bb("p", new_points)])

    assert _bbox(old_points) != _bbox(new_points), "precondition: bbox must move"
    assert is_pure_append(old, new) is True


def test_vertex_inserted_mid_array_with_recomputed_bbox():
    old_points = [{"x": float(i), "y": float(i)} for i in range(200)]
    new_points = list(old_points)
    new_points.insert(88, {"x": -50.0, "y": -50.0})

    old = _blob([_poly_bb("p", old_points)])
    new = _blob([_poly_bb("p", new_points)])
    assert is_pure_append(old, new) is True


def test_bbox_ignored_only_for_the_bbox_fields():
    """A label change alongside a legitimate bbox move still records."""
    old_points = [{"x": float(i), "y": float(i)} for i in range(10)]
    new_points = old_points + [{"x": 77.0, "y": 77.0}]

    old = _blob([_poly_bb("p", old_points, label=1)])
    new = _blob([_poly_bb("p", new_points, label=9)])
    assert is_pure_append(old, new) is False


def test_box_resize_is_not_append():
    """No `points`, so width is real data — a resize must be preserved."""
    old = _blob([{"id": "b", "type": "rect", "x": 0, "y": 0,
                  "width": 10, "height": 10, "labelId": 1}])
    new = _blob([{"id": "b", "type": "rect", "x": 0, "y": 0,
                  "width": 500, "height": 10, "labelId": 1}])
    assert is_pure_append(old, new) is False


def test_bbox_inconsistent_with_points_is_not_exempt():
    """Bounds that no `updateAnnotationBounds` call could have produced.

    If the stored bbox does not describe the stored vertices, it was set by
    something else and must be compared as ordinary data.
    """
    old_points = [{"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 10.0}]
    new_points = old_points + [{"x": 20.0, "y": 20.0}]

    old = _blob([{"id": "p", "type": "polygon", "labelId": 1,
                  "points": old_points,
                  "x": 0, "y": 0, "width": 999, "height": 999}])
    new = _blob([{"id": "p", "type": "polygon", "labelId": 1,
                  "points": new_points,
                  "x": 0, "y": 0, "width": 1234, "height": 999}])
    assert is_pure_append(old, new) is False


def test_bbox_shrunk_while_points_grew_is_not_append():
    """Points grew but bounds shrank — inconsistent, so not exempt."""
    old_points = [{"x": 0.0, "y": 0.0}, {"x": 50.0, "y": 50.0}]
    new_points = old_points + [{"x": 60.0, "y": 60.0}]

    old = _blob([_poly_bb("p", old_points)])
    bad = {"id": "p", "type": "polygon", "labelId": 2, "points": new_points,
           "x": 0, "y": 0, "width": 5, "height": 5}
    assert is_pure_append(old, _blob([bad])) is False


def test_points_removed_with_consistent_bbox_is_not_append():
    """The exemption must never rescue a shrinking polygon."""
    old_points = [{"x": float(i), "y": float(i)} for i in range(30)]
    new_points = old_points[:5]

    old = _blob([_poly_bb("p", old_points)])
    new = _blob([_poly_bb("p", new_points)])
    assert is_pure_append(old, new) is False


def test_points_moved_with_consistent_bbox_is_not_append():
    old_points = [{"x": float(i), "y": float(i)} for i in range(30)]
    moved = list(old_points)
    moved[7] = {"x": -3.0, "y": -3.0}

    old = _blob([_poly_bb("p", old_points)])
    new = _blob([_poly_bb("p", moved)])
    assert is_pure_append(old, new) is False


def test_polygon_translated_is_not_append():
    """Dragging a whole shape changes every vertex — not additive."""
    old_points = [{"x": float(i), "y": float(i)} for i in range(20)]
    shifted = [{"x": p["x"] + 100, "y": p["y"] + 100} for p in old_points]

    old = _blob([_poly_bb("p", old_points)])
    new = _blob([_poly_bb("p", shifted)])
    assert is_pure_append(old, new) is False


def test_bbox_tolerance_absorbs_rounding_but_not_real_edits():
    points = [{"x": 0.0, "y": 0.0}, {"x": 10.005, "y": 10.005}]
    grown = points + [{"x": 12.0, "y": 12.0}]

    within = _poly_bb("p", points)
    within["width"] = within["width"] + 0.005          # sub-rounding noise
    old = _blob([within])
    assert is_pure_append(old, _blob([_poly_bb("p", grown)])) is True

    beyond = _poly_bb("p", points)
    beyond["width"] = beyond["width"] + 5.0            # a real change
    old2 = _blob([beyond])
    assert is_pure_append(old2, _blob([_poly_bb("p", grown)])) is False


def test_non_numeric_bbox_is_not_exempt():
    old_points = [{"x": 0.0, "y": 0.0}, {"x": 5.0, "y": 5.0}]
    new_points = old_points + [{"x": 9.0, "y": 9.0}]
    old = _blob([{"id": "p", "type": "polygon", "labelId": 1,
                  "points": old_points, "x": "0", "y": 0,
                  "width": 5, "height": 5}])
    new = _blob([_poly_bb("p", new_points)])
    assert is_pure_append(old, new) is False


def test_new_object_with_bbox_still_additive():
    old_points = [{"x": float(i), "y": float(i)} for i in range(10)]
    old = _blob([_poly_bb("a", old_points)])
    new = _blob([_poly_bb("a", old_points), _poly_bb("b", old_points)])
    assert is_pure_append(old, new) is True

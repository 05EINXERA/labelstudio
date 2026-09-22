"""Ctrl+Z straight after closing a polygon trims its last vertex.

Closing a polygon used to mean the very next Ctrl+Z threw the whole shape away:
`finalizePolygon` nulls `view.drag`, so `undoLastPoint` (which only handles the
in-progress case) no longer matched and the press fell through to the history
stack, whose top entry was the polygon's creation.

That is the wrong reading of "undo" at that moment -- an annotator who has just
closed a shape and presses Ctrl+Z almost always means "that last vertex was
wrong", not "scrap the polygon". `finalizePolygon` now records the shape in
`view.lastFinalizedPolygonId` and `undoAction` trims its last vertex while that
arming stands.

There is no JS test runner here, so the trim rule is ported to Python (the same
approach test_merge_ungroup.py takes for the ungroup restore) and the wiring
that cannot be ported is covered by source assertions.
"""
import os

FRONTEND_JS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "frontend", "js"
)

# Below this a polygon stops being a polygon, so the trim must refuse and let
# ordinary history undo remove the shape instead.
MIN_POLYGON_POINTS = 3


def _read(*parts):
    with open(os.path.join(FRONTEND_JS_DIR, *parts), "r", encoding="utf-8") as f:
        return f.read()


def undo_last_finalized_point(view, annotations):
    """Port of undoLastFinalizedPoint. Returns True if it consumed the press."""
    armed_id = view.get("lastFinalizedPolygonId")
    if not armed_id:
        return False
    # An in-progress polygon is the other undo path's business, not this one's.
    if (view.get("drag") or {}).get("type") == "draw-polygon":
        return False
    annotation = next((a for a in annotations if a["id"] == armed_id), None)
    if annotation is None or annotation.get("type") != "polygon":
        view["lastFinalizedPolygonId"] = None
        return False
    if len(annotation.get("points") or []) <= MIN_POLYGON_POINTS:
        return False
    annotation["points"].pop()
    return True


def _square():
    return {
        "id": "poly-1",
        "type": "polygon",
        "points": [
            {"x": 0, "y": 0},
            {"x": 10, "y": 0},
            {"x": 10, "y": 10},
            {"x": 5, "y": 12},
            {"x": 0, "y": 10},
        ],
    }


def test_trim_removes_only_the_last_vertex():
    annotation = _square()
    annotations = [annotation]
    view = {"lastFinalizedPolygonId": "poly-1", "drag": None}

    assert undo_last_finalized_point(view, annotations) is True
    assert len(annotation["points"]) == 4
    assert annotation["points"][-1] == {"x": 5, "y": 12}
    # The shape survives -- this is the whole point of the change.
    assert annotations == [annotation]


def test_trim_repeats_down_to_the_polygon_floor():
    annotation = _square()
    annotations = [annotation]
    view = {"lastFinalizedPolygonId": "poly-1", "drag": None}

    assert undo_last_finalized_point(view, annotations) is True   # 5 -> 4
    assert undo_last_finalized_point(view, annotations) is True   # 4 -> 3
    assert len(annotation["points"]) == MIN_POLYGON_POINTS

    # At the floor the press is not consumed, so undoAction falls through to
    # history and removes the shape instead of leaving a 2-point "polygon".
    assert undo_last_finalized_point(view, annotations) is False
    assert len(annotation["points"]) == MIN_POLYGON_POINTS


def test_trim_is_inert_when_nothing_was_just_finalized():
    annotation = _square()
    view = {"lastFinalizedPolygonId": None, "drag": None}

    assert undo_last_finalized_point(view, [annotation]) is False
    assert len(annotation["points"]) == 5


def test_trim_defers_to_the_in_progress_polygon_path():
    """undoLastPoint owns a polygon still being drawn; this must not double-pop."""
    annotation = _square()
    view = {
        "lastFinalizedPolygonId": "poly-1",
        "drag": {"type": "draw-polygon", "annotationId": "poly-2"},
    }

    assert undo_last_finalized_point(view, [annotation]) is False
    assert len(annotation["points"]) == 5


def test_trim_disarms_itself_when_the_polygon_is_gone():
    """The armed shape can be deleted by other means; the stale id must clear."""
    view = {"lastFinalizedPolygonId": "poly-1", "drag": None}

    assert undo_last_finalized_point(view, []) is False
    assert view["lastFinalizedPolygonId"] is None


def test_trim_ignores_non_polygon_shapes():
    box = {"id": "poly-1", "type": "box", "points": [{"x": 0, "y": 0}] * 5}
    view = {"lastFinalizedPolygonId": "poly-1", "drag": None}

    assert undo_last_finalized_point(view, [box]) is False
    assert len(box["points"]) == 5


def test_finalize_arms_the_trim():
    source = _read("canvas", "interactions.js")
    finalize = source.split("export function finalizePolygon()")[1].split(
        "export function undoLastPoint"
    )[0]
    assert "view.lastFinalizedPolygonId = annotation.id" in finalize, (
        "closing a polygon must arm the trim, or Ctrl+Z deletes the whole shape"
    )


def test_undo_action_tries_the_trim_before_history():
    source = _read("canvas", "interactions.js")
    undo = source.split("export function undoAction()")[1][:1400]
    assert "undoLastFinalizedPoint()" in undo
    assert undo.index("undoLastFinalizedPoint()") < undo.index("state.history.pop()"), (
        "the trim must be attempted before falling through to history undo"
    )
    assert "view.lastFinalizedPolygonId = null" in undo, (
        "an undo that is not a trim must disarm, so a later press cannot resume "
        "trimming a shape the annotator has moved on from"
    )


def test_the_trim_takes_a_history_snapshot():
    """Unlike in-progress point undo, each trim is a real, redoable history step."""
    source = _read("canvas", "interactions.js")
    trim = source.split("function undoLastFinalizedPoint()")[1].split(
        "export function undoAction"
    )[0]
    assert "snapshot()" in trim
    assert "updateAnnotationBounds(annotation)" in trim, "bounds must follow the points"
    assert "save()" in trim, "a trimmed vertex must persist"


def test_new_edits_disarm_the_trim():
    source = _read("canvas", "interactions.js")

    pointerdown = source.split('canvas.addEventListener("pointerdown"')[1][:1600]
    assert "view.lastFinalizedPolygonId = null" in pointerdown, (
        "clicking the canvas means the annotator moved on from the closed polygon"
    )

    delete_selected = source.split("export function deleteSelected()")[1][:600]
    assert "view.lastFinalizedPolygonId = null" in delete_selected


def test_view_declares_the_arming_field():
    source = _read("canvas", "view.js")
    assert "lastFinalizedPolygonId: null" in source

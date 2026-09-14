"""Double-click closes an in-progress polygon, without leaving a stray vertex.

The behaviour under test lives in the canvas `dblclick` handler
(frontend/js/canvas/interactions.js). The browser fires mousedown/mouseup/click
for BOTH clicks of a double-click before `dblclick` runs, so by the time the
handler executes, the pointerdown path has already added two vertices a few
screen pixels apart. The second is an artifact of the gesture, not a point the
annotator placed, so it is dropped before finalizing.

This matters historically: the feature existed once, was removed "as per user
request", and has now been asked for again. The doubled vertex is the most
likely reason it was dropped the first time, so it is the thing worth pinning.

Two layers here, following the convention in test_polygon_intersection.py:
the geometry is ported to Python and tested directly, and a source-level check
asserts the shipped JS still carries the guard. Neither simulates a real click
— canvas interaction cannot be exercised from pytest, so the gesture itself
still needs a manual check in the app.
"""
import math
import os
import re

import pytest

INTERACTIONS_JS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "frontend", "js", "canvas", "interactions.js",
)

# Mirrors DBLCLICK_DUPLICATE_RADIUS_PX in interactions.js.
DBLCLICK_DUPLICATE_RADIUS_PX = 6


def drop_duplicate_tail(points, scale=1.0, radius_px=DBLCLICK_DUPLICATE_RADIUS_PX):
    """Port of the dedup the dblclick handler runs before finalizing.

    Returns the points it would finalize with. `scale` is view.imageBox.scale —
    the threshold is a constant on-screen distance, converted to image space,
    so it keeps working at any zoom.
    """
    pts = [dict(p) for p in points]
    # Below 4 points, dropping one cannot leave a valid polygon; finalizePolygon
    # discards the shape anyway, so the handler leaves it alone.
    if len(pts) < 4:
        return pts
    last, prev = pts[-1], pts[-2]
    threshold = radius_px / (scale or 1)
    if math.hypot(last["x"] - prev["x"], last["y"] - prev["y"]) <= threshold:
        pts.pop()
    return pts


def pt(x, y):
    return {"x": x, "y": y}


# ---------------------------------------------------------------------------
# The duplicate vertex
# ---------------------------------------------------------------------------

def test_near_duplicate_tail_vertex_is_dropped():
    """The ordinary case: two clicks land a couple of pixels apart."""
    points = [pt(0, 0), pt(100, 0), pt(100, 100), pt(98, 99)]
    result = drop_duplicate_tail(points)
    assert len(result) == 3
    assert result == [pt(0, 0), pt(100, 0), pt(100, 100)]


def test_exactly_coincident_tail_vertex_is_dropped():
    points = [pt(0, 0), pt(100, 0), pt(100, 100), pt(100, 100)]
    assert len(drop_duplicate_tail(points)) == 3


def test_a_deliberate_nearby_vertex_is_kept():
    """A real vertex placed just outside the radius must survive.

    The radius is small on purpose: an annotator tracing fine detail may
    legitimately place vertices close together, and eating one of those would
    be a worse bug than the doubled vertex this guard exists to remove.
    """
    points = [pt(0, 0), pt(100, 0), pt(100, 100), pt(100, 92)]
    result = drop_duplicate_tail(points)
    assert len(result) == 4, "a vertex 8px away is deliberate, not a double-click artifact"


@pytest.mark.parametrize("distance,expected_len", [
    (0.0, 3),
    (5.9, 3),
    (6.0, 3),     # inclusive bound
    (6.1, 4),
    (50.0, 4),
])
def test_radius_boundary(distance, expected_len):
    points = [pt(0, 0), pt(100, 0), pt(100, 100), pt(100 + distance, 100)]
    assert len(drop_duplicate_tail(points)) == expected_len


# ---------------------------------------------------------------------------
# Zoom independence
# ---------------------------------------------------------------------------

def test_threshold_scales_with_zoom():
    """At 8x zoom, two clicks 6 screen px apart are 0.75px apart in the image.

    A fixed image-space threshold would stop recognising the double-click as
    the annotator zooms in — which is exactly when they are placing vertices
    most carefully and would notice the stray point.
    """
    points = [pt(0, 0), pt(100, 0), pt(100, 100), pt(100.75, 100)]
    assert len(drop_duplicate_tail(points, scale=8.0)) == 3, "should dedup at 8x zoom"
    # The same image-space gap at 1x is a real 0.75px-apart vertex... still
    # inside the 6px radius, so it dedups there too. Use a wider gap to show
    # the threshold genuinely tracks scale.
    far = [pt(0, 0), pt(100, 0), pt(100, 100), pt(104, 100)]
    assert len(drop_duplicate_tail(far, scale=8.0)) == 4, "4 image px is 32 screen px at 8x — deliberate"
    assert len(drop_duplicate_tail(far, scale=1.0)) == 3, "4 image px is 4 screen px at 1x — a double-click"


def test_zoomed_out_does_not_eat_distant_vertices():
    """At 0.25x, 6 screen px is 24 image px — still a bounded, small radius."""
    points = [pt(0, 0), pt(100, 0), pt(100, 100), pt(130, 100)]
    assert len(drop_duplicate_tail(points, scale=0.25)) == 4


# ---------------------------------------------------------------------------
# Shapes too small to trim
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_short_point_lists_are_left_alone(count):
    """Below 4 points, dropping one cannot leave a valid polygon.

    finalizePolygon discards a shape with fewer than 3 points, so trimming here
    would only change which incomplete shape gets discarded — no benefit, and
    it would turn a just-valid triangle into a discarded one.
    """
    points = [pt(i, i) for i in range(count)]
    assert drop_duplicate_tail(points) == points


def test_a_triangle_closed_by_double_click_survives():
    """Three real vertices plus the gesture's duplicate = 4 points in.

    Dropping the duplicate leaves a valid triangle. This is the smallest shape
    a double-click can legitimately close, and the case where an off-by-one in
    the length guard would silently discard the annotator's work.
    """
    points = [pt(0, 0), pt(100, 0), pt(50, 80), pt(51, 81)]
    result = drop_duplicate_tail(points)
    assert len(result) == 3, "must leave a valid triangle, not discard the shape"


# ---------------------------------------------------------------------------
# The shipped implementation still carries the guard
# ---------------------------------------------------------------------------

def _source():
    with open(INTERACTIONS_JS, "r", encoding="utf-8") as f:
        return f.read()


def test_dblclick_handler_finalizes_a_polygon():
    """Guards against the feature being silently dropped again.

    It was removed once already; if that happens a third time, this test should
    make it a deliberate act rather than a quiet deletion.
    """
    source = _source()
    match = re.search(r'canvas\.addEventListener\("dblclick".*?\n\}\);', source, re.S)
    assert match, "dblclick handler not found"
    handler = match.group(0)
    assert "finalizePolygon()" in handler, "double-click no longer closes a polygon"
    assert 'view.drag?.type === "draw-polygon"' in handler, "must be scoped to an in-progress polygon"


def test_dblclick_handler_drops_the_duplicate_vertex():
    source = _source()
    assert "DBLCLICK_DUPLICATE_RADIUS_PX" in source
    match = re.search(r'canvas\.addEventListener\("dblclick".*?\n\}\);', source, re.S)
    handler = match.group(0)
    assert "pts.pop()" in handler, "the gesture's duplicate vertex is no longer dropped"
    # Converted to image space, matching the convention used by the vertex and
    # edge grab radii and the freehand spacing.
    assert "view.imageBox?.scale" in handler


def test_dblclick_handler_still_deletes_vertices_in_select_mode():
    """The pre-existing select-mode behaviour must not be swallowed."""
    match = re.search(r'canvas\.addEventListener\("dblclick".*?\n\}\);', _source(), re.S)
    handler = match.group(0)
    assert 'state.mode === "select"' in handler
    assert "Vertex removed" in handler


def test_python_port_matches_the_js_radius():
    """A drift check: the ported threshold must equal the shipped constant."""
    match = re.search(r"const DBLCLICK_DUPLICATE_RADIUS_PX = (\d+)", _source())
    assert match, "DBLCLICK_DUPLICATE_RADIUS_PX not found in interactions.js"
    assert int(match.group(1)) == DBLCLICK_DUPLICATE_RADIUS_PX

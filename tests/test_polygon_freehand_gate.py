"""Freehand polygon tracing only runs for a press that placed a vertex.

The pointermove handler in frontend/js/canvas/interactions.js lays down
freehand points while the left button is held during a polygon draw. It used to
gate on `event.buttons === 1` alone. So any held button counted as a trace: a
press that began on the toolbar or class list and was dragged onto the canvas,
or a canvas press that placed no vertex. `appendEvenlySpacedPoints` then filled
the run from the last vertex to the cursor with evenly spaced points, and an
edge the annotator never drew appeared on its own.

The fix: the polygon pointerdown branch sets `view.drag.freehandStroke`,
pointerup clears it, and pointermove requires it. Canvas gestures cannot be run
from pytest, so this checks the source for that wiring.
"""
import os
import re

INTERACTIONS_JS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "frontend", "js", "canvas", "interactions.js",
)


def _source():
    with open(INTERACTIONS_JS, encoding="utf-8") as fh:
        return fh.read()


def _handler(src, event_name):
    start = src.index(f'canvas.addEventListener("{event_name}"')
    nxt = src.find("canvas.addEventListener(", start + 1)
    return src[start:nxt if nxt != -1 else len(src)]


def test_pointermove_freehand_requires_a_canvas_stroke():
    move = _handler(_source(), "pointermove")
    assert re.search(r"event\.buttons === 1 && view\.drag\.freehandStroke", move)
    # No ungated freehand branch left behind.
    assert not re.search(r"if \(event\.buttons === 1\)\s*\{", move)


def test_pointerdown_polygon_branch_arms_the_stroke():
    down = _handler(_source(), "pointerdown")
    polygon = down[down.index('state.shape === "polygon"'):]
    assert "view.drag.freehandStroke = true" in polygon


def test_pointerup_disarms_the_stroke():
    up = _handler(_source(), "pointerup")
    assert "view.drag.freehandStroke = false" in up

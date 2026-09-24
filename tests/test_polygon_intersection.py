"""The vectorised self-intersection finder against its pure-Python oracle.

`formats.common._find_first_self_intersection` became a numpy, bbox-pruned
finder after the pure-Python O(v²) loop cost ~10 minutes of GIL-held CPU per
export on a 1 M-vertex task (.devnotes/fix-exports-imports/01_INCIDENT.md).
It feeds `untangle_polygon`, which rewrites imported geometry, so it must
return *exactly* the scalar answer: the same (i, j) pair in the same scan
order, and the same intersection point. A different first hit would split an
imported ring at a different crossing and change the imported shape, and
it would drift from `frontend/js/canvas/untangle.js`.

The scalar loop is kept as `_find_first_self_intersection_scalar`, the oracle.
"""
import math
import random
import time

import pytest

from formats import common
from formats.common import (_find_first_self_intersection,
                            _find_first_self_intersection_scalar,
                            is_simple_polygon, untangle_polygon)


def _ring(rnd, n, kind):
    if kind == "random":
        return [{"x": rnd.uniform(0, 100), "y": rnd.uniform(0, 100)} for _ in range(n)]
    if kind == "grid":
        # Integer coordinates on a small grid: collinear overlaps, shared
        # vertices and touching segments, the cases the epsilon excludes.
        return [{"x": float(rnd.randint(0, 6)), "y": float(rnd.randint(0, 6))} for _ in range(n)]
    if kind == "zigzag":
        # Every segment's box overlaps many others: defeats the pruning.
        return [{"x": float(i), "y": float((i % 2) * 10)} for i in range(n)] + [{"x": n / 2, "y": -5.0}]
    if kind == "circle":
        return [{"x": 50 + 40 * math.cos(2 * math.pi * k / n), "y": 50 + 40 * math.sin(2 * math.pi * k / n)}
                for k in range(n)]
    if kind == "spiky":
        # A star whose spikes cross their neighbours: many crossings.
        pts = []
        for k in range(n):
            r = 40 if k % 2 else 10
            a = 2 * math.pi * k / n * 3.01
            pts.append({"x": 50 + r * math.cos(a), "y": 50 + r * math.sin(a)})
        return pts
    raise ValueError(kind)


@pytest.mark.parametrize("kind", ["random", "grid", "zigzag", "circle", "spiky"])
def test_vectorised_finder_matches_scalar_exactly(kind):
    rnd = random.Random(f"seed-{kind}")
    sizes = list(range(4, 70)) + [100, 257, 600]
    for n in sizes:
        for _ in range(3 if n < 70 else 1):
            ring = _ring(rnd, n, kind)
            assert _find_first_self_intersection(ring) == _find_first_self_intersection_scalar(ring), (kind, n)


def test_small_chunks_do_not_change_the_answer(monkeypatch):
    """The chunk boundary must be invisible: force one row per chunk."""
    monkeypatch.setattr(common, "_VECTOR_CHUNK_CELLS", 1)
    rnd = random.Random(11)
    for kind in ("random", "spiky", "grid"):
        for n in (40, 90, 200):
            ring = _ring(rnd, n, kind)
            assert _find_first_self_intersection(ring) == _find_first_self_intersection_scalar(ring)


def test_closing_edge_is_not_a_crossing():
    """Edge n-1 (last -> first) is adjacent to edge 0 and must be skipped."""
    square = [{"x": 0.0, "y": 0.0}, {"x": 10.0, "y": 0.0}, {"x": 10.0, "y": 10.0}, {"x": 0.0, "y": 10.0}]
    # Densify past the scalar fast path so the vectorised branch runs.
    ring = []
    for a, b in zip(square, square[1:] + square[:1]):
        for t in range(12):
            ring.append({"x": a["x"] + (b["x"] - a["x"]) * t / 12, "y": a["y"] + (b["y"] - a["y"]) * t / 12})
    assert len(ring) > common._VECTOR_MIN_VERTICES
    assert _find_first_self_intersection(ring) is None
    assert is_simple_polygon(ring)


def test_untangle_output_is_unchanged():
    """untangle_polygon (the import path) resolves to the same ring as before."""
    rnd = random.Random(5)
    for n in (40, 80, 150):
        ring = _ring(rnd, n, "spiky")
        fast = untangle_polygon(ring)
        # The same algorithm with the scalar finder swapped back in.
        original = common._find_first_self_intersection
        common._find_first_self_intersection = _find_first_self_intersection_scalar
        try:
            slow = untangle_polygon(ring)
        finally:
            common._find_first_self_intersection = original
        assert fast == slow


def test_large_simple_ring_is_fast():
    """Guards against a revert to the O(v²) loop: 20,000 vertices, the size of
    the polygons in the incident, take seconds in the scalar code and well under
    a second here. The bound is loose so a slow CI box does not flake.
    """
    n = 20_000
    ring = [{"x": 500 + 400 * math.cos(2 * math.pi * k / n), "y": 500 + 400 * math.sin(2 * math.pi * k / n)}
            for k in range(n)]
    started = time.perf_counter()
    assert is_simple_polygon(ring)
    assert time.perf_counter() - started < 5.0

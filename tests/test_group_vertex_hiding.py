"""Buried-vertex hiding for grouped polygons (`hiddenGroupVertexFlags`).

A vertex of a grouped shape that lies inside a group-mate gets no handle. That
containment test costs vertices x edges across the group, and it used to run on
every frame, which made the workspace lag once dense polygons were grouped. The
result is now cached per annotation and keyed on the group's geometry.

These tests run the real `frontend/js/canvas/geometry.js` under Node. They pin
that the cached answer matches a direct computation, and that editing points in
place or changing group membership invalidates the cache. That second case is
the stale-cache bug draw.js warns about in `buildGroupMap`.
"""
import json
import os
import shutil
import subprocess

import pytest

GEOMETRY_JS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "frontend", "js", "canvas", "geometry.js",
)


def _run(body):
    if shutil.which("node") is None:
        pytest.skip("node is not available on PATH")
    url = "file:///" + GEOMETRY_JS.replace("\\", "/")
    script = f"import('{url}').then(g => {{ {body} }});"
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, f"node failed: {result.stderr}"
    return json.loads(result.stdout.strip())


SETUP = """
const sq = (x, y, s) => [{x, y}, {x: x + s, y}, {x: x + s, y: y + s}, {x, y: y + s}];
const a = { id: 'a', groupId: 'g', points: sq(0, 0, 10) };
const b = { id: 'b', groupId: 'g', points: sq(5, 5, 10) };
const group = [a, b];
const direct = (ann, members) => ann.points.map(
  p => g.isPointInsideOtherGroupPolygons(p, ann, members));
"""


def test_flags_match_direct_containment():
    data = _run(SETUP + """
    console.log(JSON.stringify({
      a: g.hiddenGroupVertexFlags(a, group), aDirect: direct(a, group),
      b: g.hiddenGroupVertexFlags(b, group), bDirect: direct(b, group),
    }));
    """)
    assert data["a"] == data["aDirect"]
    assert data["b"] == data["bDirect"]
    # (10,10) of a sits inside b; (5,5) of b sits inside a. Nothing else does.
    assert data["a"] == [False, False, True, False]
    assert data["b"] == [True, False, False, False]


def test_unchanged_group_is_served_from_cache():
    data = _run(SETUP + """
    const first = g.hiddenGroupVertexFlags(a, group);
    // A fresh array with the same members, as draw.js builds per frame.
    const second = g.hiddenGroupVertexFlags(a, [a, b]);
    console.log(JSON.stringify({ same: first === second }));
    """)
    assert data["same"] is True


def test_in_place_point_edit_invalidates_cache():
    data = _run(SETUP + """
    const before = g.hiddenGroupVertexFlags(a, group).slice();
    // Drag b away in place, the way a vertex/shape drag mutates points.
    b.points.forEach(p => { p.x += 100; });
    const afterMateMoved = g.hiddenGroupVertexFlags(a, group).slice();
    // Now move a's own vertex into the relocated b.
    a.points[0].x = 110; a.points[0].y = 10;
    const afterSelfMoved = g.hiddenGroupVertexFlags(a, group).slice();
    console.log(JSON.stringify({
      before, afterMateMoved, afterSelfMoved, direct: direct(a, group),
    }));
    """)
    assert data["before"] == [False, False, True, False]
    assert data["afterMateMoved"] == [False, False, False, False]
    assert data["afterSelfMoved"] == data["direct"]
    assert data["afterSelfMoved"][0] is True


def test_membership_change_invalidates_cache():
    data = _run(SETUP + """
    const grouped = g.hiddenGroupVertexFlags(a, group).slice();
    const alone = g.hiddenGroupVertexFlags(a, [a]).slice();
    const regrouped = g.hiddenGroupVertexFlags(a, group).slice();
    console.log(JSON.stringify({ grouped, alone, regrouped }));
    """)
    assert data["grouped"] == [False, False, True, False]
    assert data["alone"] == [False, False, False, False]
    assert data["regrouped"] == data["grouped"]

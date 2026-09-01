"""Unit tests for merging touching annotations into a single polygon.

Mirrors the union geometry in `frontend/js/canvas/geometry.js` (`unionPolygons`,
`polygonsTouch`). When grouped shapes touch or overlap, they are collapsed into
one outline and the vertices that fall inside the overlap — the intersection
coordinates — are discarded rather than left behind as draggable handles.

Ported to Python the same way test_polygon_intersection.py ports the drawing
geometry, so the algorithm is covered without a JS test runner.
"""
import math

EPSILON = 1e-6


def line_segment_intersection(p1, p2, p3, p4):
    dx1 = p2["x"] - p1["x"]
    dy1 = p2["y"] - p1["y"]
    dx2 = p4["x"] - p3["x"]
    dy2 = p4["y"] - p3["y"]

    denom = dx1 * dy2 - dy1 * dx2
    if abs(denom) < 1e-9:
        return None

    dx31 = p3["x"] - p1["x"]
    dy31 = p3["y"] - p1["y"]

    t = (dx31 * dy2 - dy31 * dx2) / denom
    u = (dx31 * dy1 - dy31 * dx1) / denom

    return {"t": t, "u": u, "x": p1["x"] + t * dx1, "y": p1["y"] + t * dy1}


def point_in_polygon(point, polygon):
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        current = polygon[i]
        previous = polygon[j]
        intersects = ((current["y"] > point["y"]) != (previous["y"] > point["y"])) and (
            point["x"] < ((previous["x"] - current["x"]) * (point["y"] - current["y"])
                          / (previous["y"] - current["y"] + 1e-16)) + current["x"]
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def point_on_polygon_boundary(point, polygon, tolerance=EPSILON):
    j = len(polygon) - 1
    for i in range(len(polygon)):
        p1 = polygon[j]
        p2 = polygon[i]
        j = i
        dx = p2["x"] - p1["x"]
        dy = p2["y"] - p1["y"]
        length_squared = dx * dx + dy * dy
        if length_squared == 0:
            continue
        t = ((point["x"] - p1["x"]) * dx + (point["y"] - p1["y"]) * dy) / length_squared
        t = max(0.0, min(1.0, t))
        distance = math.hypot(point["x"] - (p1["x"] + t * dx), point["y"] - (p1["y"] + t * dy))
        if distance <= tolerance:
            return True
    return False


def point_in_or_on_polygon(point, polygon):
    if point_on_polygon_boundary(point, polygon, 1e-3):
        return True
    return point_in_polygon(point, polygon)


def point_strictly_inside(point, polygon, tolerance=EPSILON):
    if point_on_polygon_boundary(point, polygon, tolerance):
        return False
    return point_in_polygon(point, polygon)


def polygons_touch(a, b):
    """Shared area or a run of shared border. Corner-only contact does not count:
    bridging a pinch point would sweep in the empty space either side of it."""
    # Edges that properly cross. Endpoint-only grazing is excluded, so two
    # shapes meeting at a single corner do not qualify.
    for i in range(len(a)):
        p1 = a[i]
        p2 = a[(i + 1) % len(a)]
        for j in range(len(b)):
            p3 = b[j]
            p4 = b[(j + 1) % len(b)]
            hit = line_segment_intersection(p1, p2, p3, p4)
            if hit and EPSILON < hit["t"] < 1 - EPSILON and EPSILON < hit["u"] < 1 - EPSILON:
                return True

    # Shared area without crossing edges: containment, or a common border.
    # Edge midpoints are sampled rather than vertices, which are ambiguous
    # exactly at a corner touch.
    for first, second in ((a, b), (b, a)):
        for i in range(len(first)):
            p1 = first[i]
            p2 = first[(i + 1) % len(first)]
            mid = {"x": (p1["x"] + p2["x"]) / 2, "y": (p1["y"] + p2["y"]) / 2}
            if point_strictly_inside(mid, second):
                return True
            if point_on_polygon_boundary(mid, second, EPSILON):
                return True

    return False


def same_point(a, b, epsilon=1e-3):
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"]) < epsilon


def polygon_area(points):
    if not points or len(points) < 3:
        return 0.0
    area = 0.0
    j = len(points) - 1
    for i in range(len(points)):
        area += (points[j]["x"] * points[i]["y"]) - (points[i]["x"] * points[j]["y"])
        j = i
    return abs(area) / 2.0


def signed_area(points):
    area = 0.0
    j = len(points) - 1
    for i in range(len(points)):
        area += (points[j]["x"] * points[i]["y"]) - (points[i]["x"] * points[j]["y"])
        j = i
    return area / 2.0


def filter_consecutive_duplicates(pts):
    if not pts:
        return []
    res = [{"x": pts[0]["x"], "y": pts[0]["y"]}]
    for i in range(1, len(pts)):
        prev = res[-1]
        curr = {"x": pts[i]["x"], "y": pts[i]["y"]}
        if math.hypot(prev["x"] - curr["x"], prev["y"] - curr["y"]) >= 1e-3:
            res.append(curr)
    if len(res) > 1 and math.hypot(res[0]["x"] - res[-1]["x"], res[0]["y"] - res[-1]["y"]) < 1e-3:
        res.pop()
    return res


def drop_collinear_vertices(points, tolerance=EPSILON):
    if len(points) < 3:
        return points
    result = []
    for i in range(len(points)):
        previous = points[(i - 1) % len(points)]
        current = points[i]
        nxt = points[(i + 1) % len(points)]
        cross = ((current["x"] - previous["x"]) * (nxt["y"] - previous["y"])
                 - (current["y"] - previous["y"]) * (nxt["x"] - previous["x"]))
        scale = math.hypot(nxt["x"] - previous["x"], nxt["y"] - previous["y"])
        if scale > 0 and abs(cross) / scale <= tolerance:
            continue
        result.append(current)
    return result if len(result) >= 3 else points


def interior_angle_at(points, i):
    current = points[i]
    previous = points[(i - 1) % len(points)]
    nxt = points[(i + 1) % len(points)]
    to_previous = math.atan2(previous["y"] - current["y"], previous["x"] - current["x"])
    to_next = math.atan2(nxt["y"] - current["y"], nxt["x"] - current["x"])
    delta = abs(to_previous - to_next)
    if delta > math.pi:
        delta = 2 * math.pi - delta
    return delta * 180 / math.pi


def smooth_union_cusps(points, angle_threshold=150, max_segment_length=3, arc_steps=4):
    """Rounds the cusps a union leaves where two shapes crossed, without
    disturbing corners the annotator drew. Angle alone cannot separate the two —
    a merged box's 90 degree corner is sharper than a typical blob cusp — so a
    vertex is only smoothed when both neighbouring segments are short, which
    marks a densely traced curve rather than a deliberate straight edge."""
    if not points or len(points) < 3:
        return points or []

    result = []
    for i in range(len(points)):
        current = points[i]
        previous = points[(i - 1) % len(points)]
        nxt = points[(i + 1) % len(points)]

        to_previous_length = math.hypot(current["x"] - previous["x"], current["y"] - previous["y"])
        to_next_length = math.hypot(nxt["x"] - current["x"], nxt["y"] - current["y"])

        is_sharp = interior_angle_at(points, i) < angle_threshold
        on_traced_curve = (to_previous_length <= max_segment_length
                           and to_next_length <= max_segment_length)

        if not is_sharp or not on_traced_curve:
            result.append(current)
            continue

        inset = min(to_previous_length, to_next_length) / 3
        if inset <= 1e-6:
            result.append(current)
            continue

        start = {
            "x": current["x"] + (previous["x"] - current["x"]) * (inset / to_previous_length),
            "y": current["y"] + (previous["y"] - current["y"]) * (inset / to_previous_length),
        }
        end = {
            "x": current["x"] + (nxt["x"] - current["x"]) * (inset / to_next_length),
            "y": current["y"] + (nxt["y"] - current["y"]) * (inset / to_next_length),
        }

        for step in range(arc_steps + 1):
            t = step / arc_steps
            inverse = 1 - t
            result.append({
                "x": inverse * inverse * start["x"] + 2 * inverse * t * current["x"] + t * t * end["x"],
                "y": inverse * inverse * start["y"] + 2 * inverse * t * current["y"] + t * t * end["y"],
            })

    return result if len(result) >= 3 else points


def has_repeated_vertex(points):
    for i in range(len(points)):
        for j in range(i + 1, len(points)):
            if same_point(points[i], points[j]):
                return True
    return False


def sum_of_areas(shapes):
    return sum(polygon_area(s) for s in shapes)


def contains_all_shapes(outline, shapes):
    for shape in shapes:
        for i in range(len(shape)):
            current = shape[i]
            nxt = shape[(i + 1) % len(shape)]
            mid = {"x": (current["x"] + nxt["x"]) / 2, "y": (current["y"] + nxt["y"]) / 2}
            if not point_in_or_on_polygon(current, outline):
                return False
            if not point_in_or_on_polygon(mid, outline):
                return False
    return True


def shapes_are_connected(shapes):
    seen = {0}
    queue = [0]
    while queue:
        current = queue.pop()
        for i in range(len(shapes)):
            if i in seen:
                continue
            if not polygons_touch(shapes[current], shapes[i]):
                continue
            seen.add(i)
            queue.append(i)
    return len(seen) == len(shapes)


def split_polygon_at_intersections(subject, others):
    result = []
    for i in range(len(subject)):
        p1 = subject[i]
        p2 = subject[(i + 1) % len(subject)]
        result.append(p1)
        cuts = []
        for other in others:
            for j in range(len(other)):
                p3 = other[j]
                p4 = other[(j + 1) % len(other)]
                hit = line_segment_intersection(p1, p2, p3, p4)
                if not hit:
                    continue
                if hit["t"] <= EPSILON or hit["t"] >= 1 - EPSILON:
                    continue
                if hit["u"] < -EPSILON or hit["u"] > 1 + EPSILON:
                    continue
                cuts.append(hit)
        cuts.sort(key=lambda c: c["t"])
        for cut in cuts:
            if not same_point(result[-1], cut):
                result.append({"x": cut["x"], "y": cut["y"]})
    return result


def pick_outermost_turn(previous, vertex, candidates):
    incoming = math.atan2(vertex["y"] - previous["y"], vertex["x"] - previous["x"])
    best = candidates[0]
    best_turn = -math.inf
    for candidate in candidates:
        outgoing = math.atan2(candidate["to"]["y"] - vertex["y"], candidate["to"]["x"] - vertex["x"])
        turn = outgoing - incoming
        while turn <= -math.pi:
            turn += 2 * math.pi
        while turn > math.pi:
            turn -= 2 * math.pi
        if turn > best_turn:
            best_turn = turn
            best = candidate
    return best


def union_polygons(polygons):
    shapes = [filter_consecutive_duplicates(p) for p in (polygons or [])]
    shapes = [s for s in shapes if len(s) >= 3]

    if not shapes:
        return None
    if len(shapes) == 1:
        return shapes[0]
    if not shapes_are_connected(shapes):
        return None

    kept = [
        shape for index, shape in enumerate(shapes)
        if not any(
            other_index != index
            and polygon_area(other) >= polygon_area(shape)
            and contains_all_shapes(other, [shape])
            for other_index, other in enumerate(shapes)
        )
    ]
    active = kept if kept else [shapes[0]]
    if len(active) == 1:
        return active[0]

    oriented = [list(reversed(s)) if signed_area(s) > 0 else s for s in active]

    edges = []
    for index in range(len(oriented)):
        others = [s for i, s in enumerate(oriented) if i != index]
        split = split_polygon_at_intersections(oriented[index], others)
        for i in range(len(split)):
            frm = split[i]
            to = split[(i + 1) % len(split)]
            if same_point(frm, to):
                continue
            mid = {"x": (frm["x"] + to["x"]) / 2, "y": (frm["y"] + to["y"]) / 2}
            if any(not point_on_polygon_boundary(mid, o) and point_in_or_on_polygon(mid, o)
                   for o in others):
                continue
            if any(same_point(e["from"], frm) and same_point(e["to"], to) for e in edges):
                continue
            edges.append({"from": frm, "to": to, "used": False})

    if not edges:
        return None

    start = edges[0]
    for edge in edges:
        if (edge["from"]["x"] < start["from"]["x"] - 1e-9
                or (abs(edge["from"]["x"] - start["from"]["x"]) < 1e-9
                    and edge["from"]["y"] < start["from"]["y"])):
            start = edge

    start["used"] = True
    ring = [start["from"], start["to"]]

    while True:
        tail = ring[-1]
        previous = ring[-2]
        candidates = [e for e in edges if not e["used"] and same_point(e["from"], tail)]

        if len(ring) > 2 and same_point(tail, ring[0]) and not candidates:
            ring.pop()
            break
        if not candidates:
            return None

        nxt = candidates[0] if len(candidates) == 1 else pick_outermost_turn(previous, tail, candidates)
        nxt["used"] = True
        ring.append(nxt["to"])

        if len(ring) > len(edges) + 2:
            return None

    merged = drop_collinear_vertices(filter_consecutive_duplicates(ring))
    if len(merged) < 3:
        return None

    if has_repeated_vertex(merged):
        return None
    if not contains_all_shapes(merged, active):
        return None
    if polygon_area(merged) > sum_of_areas(active) + 1e-3:
        return None

    return merged


def box(x, y, w, h):
    return [
        {"x": x, "y": y},
        {"x": x + w, "y": y},
        {"x": x + w, "y": y + h},
        {"x": x, "y": y + h},
    ]


def test_touching_detected_for_overlapping_boxes():
    assert polygons_touch(box(0, 0, 100, 100), box(50, 0, 100, 100))


def test_touching_detected_for_shared_border():
    assert polygons_touch(box(0, 0, 100, 100), box(100, 0, 100, 100))


def test_disjoint_boxes_do_not_touch():
    assert not polygons_touch(box(0, 0, 50, 50), box(200, 200, 50, 50))


def test_overlapping_boxes_merge_into_one_rectangle():
    # 100x100 at x=0 plus 100x100 at x=50 overlap by 50 -> union is 150x100.
    merged = union_polygons([box(0, 0, 100, 100), box(50, 0, 100, 100)])
    assert merged is not None
    assert abs(polygon_area(merged) - 15000) < 1
    assert len(merged) == 4


def test_overlap_vertices_are_deleted():
    """The intersection coordinates inside the overlap must not survive."""
    merged = union_polygons([box(0, 0, 100, 100), box(50, 0, 100, 100)])
    interior = [p for p in merged if abs(p["x"] - 50) < 1e-3 and 0 < p["y"] < 100]
    assert interior == []
    # No vertex of the merged outline may sit strictly inside either input shape.
    # Corners shared with an input box lie *on* its boundary, which is expected.
    for p in merged:
        for shape in (box(0, 0, 100, 100), box(50, 0, 100, 100)):
            if point_on_polygon_boundary(p, shape, 1e-3):
                continue
            assert not point_in_polygon(p, shape)


def test_l_shaped_overlap_keeps_six_corners():
    merged = union_polygons([box(0, 0, 100, 40), box(0, 0, 40, 100)])
    assert merged is not None
    assert len(merged) == 6
    assert abs(polygon_area(merged) - 6400) < 1


def test_edge_touching_boxes_merge():
    merged = union_polygons([box(0, 0, 100, 100), box(100, 0, 100, 100)])
    assert merged is not None
    assert abs(polygon_area(merged) - 20000) < 1
    assert len(merged) == 4


def test_disjoint_shapes_refuse_to_merge():
    assert union_polygons([box(0, 0, 50, 50), box(200, 200, 50, 50)]) is None


def test_contained_shape_yields_outer_outline():
    merged = union_polygons([box(0, 0, 100, 100), box(25, 25, 20, 20)])
    assert merged is not None
    assert abs(polygon_area(merged) - 10000) < 1


def test_three_chained_boxes_merge():
    merged = union_polygons([box(0, 0, 60, 40), box(50, 0, 60, 40), box(100, 0, 60, 40)])
    assert merged is not None
    assert abs(polygon_area(merged) - 6400) < 1


def test_diagonal_partial_overlap():
    merged = union_polygons([box(0, 0, 100, 100), box(70, 70, 100, 100)])
    assert merged is not None
    # 10000 + 10000 minus the 30x30 overlap.
    assert abs(polygon_area(merged) - 19100) < 1


def test_merge_is_independent_of_winding_order():
    clockwise = list(reversed(box(50, 0, 100, 100)))
    merged = union_polygons([box(0, 0, 100, 100), clockwise])
    assert merged is not None
    assert abs(polygon_area(merged) - 15000) < 1


def test_overlapping_triangles_with_diagonal_edges():
    a = [{"x": 0, "y": 0}, {"x": 100, "y": 0}, {"x": 50, "y": 80}]
    b = [{"x": 0, "y": 60}, {"x": 100, "y": 60}, {"x": 50, "y": -20}]
    merged = union_polygons([a, b])
    assert merged is not None
    assert polygon_area(merged) >= max(polygon_area(a), polygon_area(b)) - 1e-3


def test_many_vertex_blobs_merge():
    """SAM-style masks have dozens of vertices; the union must still close."""
    def circle(cx, cy, r, n):
        return [
            {"x": cx + r * math.cos(i / n * 2 * math.pi),
             "y": cy + r * math.sin(i / n * 2 * math.pi)}
            for i in range(n)
        ]

    a = circle(100, 100, 50, 40)
    b = circle(160, 100, 50, 40)
    merged = union_polygons([a, b])
    assert merged is not None
    assert polygon_area(merged) > polygon_area(a)


def test_single_polygon_is_returned_unchanged():
    shape = box(0, 0, 10, 10)
    assert union_polygons([shape]) == shape


def test_empty_input_returns_none():
    assert union_polygons([]) is None
    assert union_polygons(None) is None


# --- Partial-touch regressions -------------------------------------------------
# Merging must never absorb space that belongs to neither shape, and must never
# silently drop a shape that only partially touches another.

def c_shape():
    """A "C" opening to the right: a concave notch cut out of its middle."""
    return [
        {"x": 0, "y": 0}, {"x": 60, "y": 0}, {"x": 60, "y": 30}, {"x": 20, "y": 30},
        {"x": 20, "y": 70}, {"x": 60, "y": 70}, {"x": 60, "y": 100}, {"x": 0, "y": 100},
    ]


def test_concave_notch_is_preserved_when_only_a_prong_is_touched():
    """A box overlapping one prong must not fill in the untouched notch."""
    shape = c_shape()
    other = box(50, 10, 40, 10)
    merged = union_polygons([shape, other])
    assert merged is not None
    # Only the 10x10 overlap is shared, so the notch area stays excluded.
    expected = polygon_area(shape) + polygon_area(other) - 100
    assert abs(polygon_area(merged) - expected) < 1
    # A point in the middle of the untouched notch must remain outside the union.
    assert not point_in_polygon({"x": 40, "y": 50}, merged)


def test_cross_arm_overlap_keeps_the_other_arms_gaps():
    cross = [
        {"x": 40, "y": 0}, {"x": 60, "y": 0}, {"x": 60, "y": 40}, {"x": 100, "y": 40},
        {"x": 100, "y": 60}, {"x": 60, "y": 60}, {"x": 60, "y": 100}, {"x": 40, "y": 100},
        {"x": 40, "y": 60}, {"x": 0, "y": 60}, {"x": 0, "y": 40}, {"x": 40, "y": 40},
    ]
    other = box(50, -20, 20, 30)
    merged = union_polygons([cross, other])
    assert merged is not None
    assert abs(polygon_area(merged) - (polygon_area(cross) + polygon_area(other) - 100)) < 1
    # The concave corners between the cross arms stay empty.
    assert not point_in_polygon({"x": 10, "y": 10}, merged)
    assert not point_in_polygon({"x": 90, "y": 90}, merged)


def test_l_shape_tip_overlap_keeps_inner_corner_empty():
    l_shape = [
        {"x": 0, "y": 0}, {"x": 100, "y": 0}, {"x": 100, "y": 20},
        {"x": 20, "y": 20}, {"x": 20, "y": 100}, {"x": 0, "y": 100},
    ]
    other = box(90, 10, 40, 40)
    merged = union_polygons([l_shape, other])
    assert merged is not None
    assert abs(polygon_area(merged) - (polygon_area(l_shape) + polygon_area(other) - 100)) < 1
    # The large empty square inside the L must not be swallowed.
    assert not point_in_polygon({"x": 60, "y": 60}, merged)


def test_corner_only_contact_is_not_treated_as_touching():
    """Two boxes meeting at a single point share no area and must stay separate."""
    assert not polygons_touch(box(0, 0, 50, 50), box(50, 50, 50, 50))


def test_corner_only_contact_refuses_to_merge():
    """Bridging a pinch point would sweep in the empty space either side of it."""
    assert union_polygons([box(0, 0, 50, 50), box(50, 50, 50, 50)]) is None


def test_shape_spanning_a_notch_is_not_swallowed():
    """A bar whose corners touch the C but whose middle crosses the open notch
    must not be absorbed: doing so silently deletes it and fills the gap."""
    shape = c_shape()
    bar = box(20, 40, 40, 20)   # spans the notch, corners on the C's boundary
    merged = union_polygons([shape, bar])
    # The true union encloses a hole, which a single ring cannot express, so the
    # merge is refused rather than losing the bar or filling the notch.
    if merged is not None:
        assert polygon_area(merged) >= polygon_area(shape) + polygon_area(bar) - 1e-3


def test_union_never_exceeds_the_sum_of_its_parts():
    """A union larger than the inputs combined has invented area from a gap."""
    cases = [
        [box(0, 0, 100, 100), box(50, 0, 100, 100)],
        [c_shape(), box(50, 10, 40, 10)],
        [box(0, 0, 100, 100), box(70, 70, 100, 100)],
    ]
    for shapes in cases:
        merged = union_polygons(shapes)
        assert merged is not None
        assert polygon_area(merged) <= sum_of_areas(shapes) + 1e-3


# --- Cusp smoothing ------------------------------------------------------------
# A union leaves a sharp cusp where two shapes crossed. Those are rounded, but
# only on traced curves: corners the annotator drew as straight edges must stay
# exactly as they are. Angle alone cannot tell them apart, since a merged box's
# 90-degree corner is sharper than a typical blob cusp (~99 degrees).

def circle(cx, cy, r, n):
    return [
        {"x": cx + r * math.cos(i / n * 2 * math.pi),
         "y": cy + r * math.sin(i / n * 2 * math.pi)}
        for i in range(n)
    ]


def min_interior_angle(points):
    return min(interior_angle_at(points, i) for i in range(len(points)))


def test_blob_merge_cusps_are_rounded():
    merged = union_polygons([circle(100, 100, 50, 40), circle(160, 100, 50, 40)])
    assert merged is not None
    assert min_interior_angle(merged) < 120        # the crossing leaves a cusp
    smoothed = smooth_union_cusps(merged)
    assert min_interior_angle(smoothed) > 140      # and it is rounded away


def test_smoothing_barely_changes_blob_area():
    """Rounding must not meaningfully alter what the annotation covers."""
    merged = union_polygons([circle(100, 100, 50, 40), circle(160, 100, 50, 40)])
    smoothed = smooth_union_cusps(merged)
    before = polygon_area(merged)
    assert abs(polygon_area(smoothed) - before) / before < 0.001


def test_box_corners_are_never_smoothed():
    """90-degree corners between long straight edges are deliberate, not cusps."""
    for shapes in (
        [box(0, 0, 100, 100), box(50, 0, 100, 100)],
        [box(0, 0, 100, 40), box(0, 0, 40, 100)],
        [box(0, 0, 100, 100), box(70, 70, 100, 100)],
    ):
        merged = union_polygons(shapes)
        assert merged is not None
        assert smooth_union_cusps(merged) == merged


def test_sharp_corner_between_long_edges_is_preserved():
    """A deliberate spike drawn with long edges keeps its point."""
    spike = [{"x": 0, "y": 0}, {"x": 100, "y": 0}, {"x": 50, "y": 5}]
    assert smooth_union_cusps(spike) == spike


def test_smoothing_leaves_short_shapes_alone():
    assert smooth_union_cusps([]) == []
    assert smooth_union_cusps([{"x": 0, "y": 0}, {"x": 1, "y": 1}]) == [
        {"x": 0, "y": 0}, {"x": 1, "y": 1}
    ]


def test_union_always_contains_every_input_shape():
    """No part of a merged annotation may fall outside the resulting outline."""
    cases = [
        [box(0, 0, 100, 100), box(50, 0, 100, 100)],
        [c_shape(), box(50, 10, 40, 10)],
        [box(0, 0, 60, 40), box(50, 0, 60, 40), box(100, 0, 60, 40)],
    ]
    for shapes in cases:
        merged = union_polygons(shapes)
        assert merged is not None
        assert contains_all_shapes(merged, shapes)

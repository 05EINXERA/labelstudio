"""Unit tests for polygon self-intersection resolution.

Tests the geometry logic for detecting line segment intersections and deleting
intersected loops when drawing polygons.
"""
import math
import pytest


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

    return {
        "t": t,
        "u": u,
        "x": p1["x"] + t * dx1,
        "y": p1["y"] + t * dy1
    }


def add_polygon_point_resolving_intersections(points, new_point):
    if not points:
        return [{"x": round(new_point["x"], 2), "y": round(new_point["y"], 2)}]

    result = [{"x": p["x"], "y": p["y"]} for p in points]
    target = {"x": round(new_point["x"], 2), "y": round(new_point["y"], 2)}

    last = result[-1]
    if math.hypot(last["x"] - target["x"], last["y"] - target["y"]) >= 1e-3:
        result.append(target)

    return result


def resolve_polygon_closing_intersections(points):
    return resolve_closed_polygon_intersections(points)


def test_segment_intersection_crossing():
    p1 = {"x": 0, "y": 0}
    p2 = {"x": 100, "y": 100}
    p3 = {"x": 0, "y": 100}
    p4 = {"x": 100, "y": 0}

    hit = line_segment_intersection(p1, p2, p3, p4)
    assert hit is not None
    assert hit["x"] == pytest.approx(50)
    assert hit["y"] == pytest.approx(50)
    assert hit["t"] == pytest.approx(0.5)
    assert hit["u"] == pytest.approx(0.5)


def test_segment_intersection_parallel():
    p1 = {"x": 0, "y": 0}
    p2 = {"x": 100, "y": 0}
    p3 = {"x": 0, "y": 50}
    p4 = {"x": 100, "y": 50}

    hit = line_segment_intersection(p1, p2, p3, p4)
    assert hit is None


def test_add_point_no_intersection():
    pts = [
        {"x": 0, "y": 0},
        {"x": 100, "y": 0},
        {"x": 100, "y": 100}
    ]
    new_pt = {"x": 0, "y": 100}
    result = add_polygon_point_resolving_intersections(pts, new_pt)
    assert len(result) == 4
    assert result[-1] == {"x": 0, "y": 100}


def test_add_point_preserves_drawn_points():
    pts = [
        {"x": 0, "y": 0},
        {"x": 100, "y": 0},
        {"x": 100, "y": 100},
        {"x": 0, "y": 100}
    ]
    new_pt = {"x": 50, "y": -50}
    result = add_polygon_point_resolving_intersections(pts, new_pt)
    assert len(result) == 5
    assert result[-1] == {"x": 50, "y": -50}


def test_add_point_subpixel_high_zoom():
    pts = [
        {"x": 10.0, "y": 10.0}
    ]
    # At high zoom (e.g. 500x), a point 0.05 image pixels away (25 screen pixels)
    # must be added and not dropped as a duplicate
    new_pt = {"x": 10.05, "y": 10.0}
    result = add_polygon_point_resolving_intersections(pts, new_pt)
    assert len(result) == 2
    assert result[-1] == {"x": 10.05, "y": 10.0}


def polygon_area(points):
    if not points or len(points) < 3:
        return 0.0
    area = 0.0
    j = len(points) - 1
    for i in range(len(points)):
        area += (points[j]["x"] * points[i]["y"]) - (points[i]["x"] * points[j]["y"])
        j = i
    return abs(area) / 2.0


def filter_consecutive_duplicates(pts):
    if not pts:
        return []
    res = [{"x": round(pts[0]["x"], 2), "y": round(pts[0]["y"], 2)}]
    for i in range(1, len(pts)):
        prev = res[-1]
        curr = {"x": round(pts[i]["x"], 2), "y": round(pts[i]["y"], 2)}
        if math.hypot(prev["x"] - curr["x"], prev["y"] - curr["y"]) >= 1e-3:
            res.append(curr)
    if len(res) > 1:
        first = res[0]
        last = res[-1]
        if math.hypot(first["x"] - last["x"], first["y"] - last["y"]) < 1e-3:
            res.pop()
    return res


def resolve_closed_polygon_intersections(points):
    if not points or len(points) < 4:
        return points or []

    result = filter_consecutive_duplicates(points)
    max_iterations = 50

    while max_iterations > 0 and len(result) >= 4:
        max_iterations -= 1
        n = len(result)
        found = False

        for i in range(n):
            p1 = result[i]
            p2 = result[(i + 1) % n]

            for j in range(i + 2, n):
                if i == 0 and j == n - 1:
                    continue

                p3 = result[j]
                p4 = result[(j + 1) % n]

                hit = line_segment_intersection(p1, p2, p3, p4)
                if hit and hit["t"] > 1e-4 and hit["t"] < 1.0 - 1e-4 and hit["u"] > 1e-4 and hit["u"] < 1.0 - 1e-4:
                    ix = round(hit["x"])
                    iy = round(hit["y"])
                    intersection_point = {"x": ix, "y": iy}

                    loop_a = []
                    for k in range(i + 1):
                        loop_a.append(result[k])
                    loop_a.append(intersection_point)
                    for k in range(j + 1, n):
                        loop_a.append(result[k])

                    loop_b = [intersection_point]
                    for k in range(i + 1, j + 1):
                        loop_b.append(result[k])

                    clean_a = filter_consecutive_duplicates(loop_a)
                    clean_b = filter_consecutive_duplicates(loop_b)

                    area_a = polygon_area(clean_a)
                    area_b = polygon_area(clean_b)

                    if len(clean_a) >= 3 and (len(clean_b) < 3 or area_a >= area_b):
                        result = clean_a
                    elif len(clean_b) >= 3:
                        result = clean_b
                    else:
                        result = clean_a if len(clean_a) >= len(clean_b) else clean_b

                    found = True
                    break
            if found:
                break

        if not found:
            break

    return result


def test_closing_edge_removes_intermediate_loop():
    # Hourglass / self-intersecting closing edge
    pts = [
        {"x": 0, "y": 0},
        {"x": 100, "y": 0},
        {"x": 0, "y": 100},
        {"x": 100, "y": 100}
    ]
    # Closing segment connects (100,100) back to (0,0), crossing (100,0)->(0,100) at (50,50)
    result = resolve_polygon_closing_intersections(pts)
    assert len(result) == 3
    assert result[0] == {"x": 0, "y": 0}
    assert result[1] == {"x": 100, "y": 0}
    assert result[2] == {"x": 50, "y": 50}


def test_closing_edge_crossing_near_start_preserves_main_shape():
    # User draws a 5-point polygon outlining a large rectangle:
    # P0(0,0), P1(10,0), P2(100,0), P3(100,100), P4(0,100)
    # When closing, crossing P1(10,0)->P2(100,0) previously wiped out vertices P1..P4.
    # Now it preserves the large valid polygon area and does not disappear (< 3 points).
    pts = [
        {"x": 0, "y": 0},
        {"x": 10, "y": 0},
        {"x": 100, "y": 0},
        {"x": 100, "y": 100},
        {"x": 0, "y": 100}
    ]
    result = resolve_polygon_closing_intersections(pts)
    assert len(result) >= 3
    assert polygon_area(result) > 4000


def test_closed_polygon_editing_vertex_crossover():
    # Pentagon where vertex P3 is moved across bottom edge (P0->P1)
    # P0(0,0), P1(100,0), P2(100,100), P3(50,-50), P4(0,100)
    pts = [
        {"x": 0, "y": 0},
        {"x": 100, "y": 0},
        {"x": 100, "y": 100},
        {"x": 50, "y": -50},
        {"x": 0, "y": 100}
    ]
    result = resolve_closed_polygon_intersections(pts)
    # The crossover loop through P3(50, -50) is deleted, leaving a valid polygon
    assert len(result) >= 3
    # Check that all remaining points have y >= 0 (no negative loop below bottom edge)
    for p in result:
        assert p["y"] >= 0


def test_closed_polygon_hourglass_resolution():
    # Hourglass shape during vertex edit
    pts = [
        {"x": 0, "y": 0},
        {"x": 100, "y": 0},
        {"x": 0, "y": 100},
        {"x": 100, "y": 100}
    ]
    result = resolve_closed_polygon_intersections(pts)
    assert len(result) == 3
    assert result[0] == {"x": 0, "y": 0}
    assert result[1] == {"x": 100, "y": 0}
    assert result[2] == {"x": 50, "y": 50}


import { round } from "../utils.js?v=1";

export function annotationPoints(annotation) {
  if (Array.isArray(annotation?.points) && annotation.points.length >= 1) {
    return annotation.points.map((point) => ({
      x: Number(point.x) || 0,
      y: Number(point.y) || 0
    }));
  }

  const x = Number(annotation?.x) || 0;
  const y = Number(annotation?.y) || 0;
  const width = Math.max(1, Number(annotation?.width) || 1);
  const height = Math.max(1, Number(annotation?.height) || 1);
  return [
    { x, y },
    { x: x + width, y },
    { x: x + width, y: y + height },
    { x, y: y + height }
  ];
}

export function updateAnnotationBounds(annotation) {
  const points = annotationPoints(annotation);
  if (!points.length) return;

  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  annotation.x = round(Math.min(...xs));
  annotation.y = round(Math.min(...ys));
  annotation.width = round(Math.max(...xs) - annotation.x);
  annotation.height = round(Math.max(...ys) - annotation.y);
  annotation.points = points.map((point) => ({ x: round(point.x), y: round(point.y) }));
}

export function pointInPolygon(point, polygon) {
  if (!polygon?.length) return false;

  let inside = false;
  for (let index = 0, nextIndex = polygon.length - 1; index < polygon.length; nextIndex = index, index += 1) {
    const current = polygon[index];
    const previous = polygon[nextIndex];
    const intersects = ((current.y > point.y) !== (previous.y > point.y)) &&
      (point.x < ((previous.x - current.x) * (point.y - current.y) / (previous.y - current.y + Number.EPSILON)) + current.x);
    if (intersects) inside = !inside;
  }
  return inside;
}

export function hexToRgba(hex, alpha) {
  const clean = hex.replace("#", "");
  const value = parseInt(clean.length === 3 ? clean.split("").map((c) => c + c).join("") : clean, 16);
  const r = (value >> 16) & 255;
  const g = (value >> 8) & 255;
  const b = value & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

/**
 * Calculates intersection between segment (p1, p2) and segment (p3, p4).
 * Returns { x, y, t, u } if the segments intersect, or null otherwise.
 * t is normalized position along (p1, p2), u along (p3, p4).
 */
export function getLineSegmentsIntersection(p1, p2, p3, p4) {
  const dx1 = p2.x - p1.x;
  const dy1 = p2.y - p1.y;
  const dx2 = p4.x - p3.x;
  const dy2 = p4.y - p3.y;

  const denom = dx1 * dy2 - dy1 * dx2;
  if (Math.abs(denom) < 1e-9) {
    return null;
  }

  const dx31 = p3.x - p1.x;
  const dy31 = p3.y - p1.y;

  const t = (dx31 * dy2 - dy31 * dx2) / denom;
  const u = (dx31 * dy1 - dy31 * dx1) / denom;

  return {
    t,
    u,
    x: p1.x + t * dx1,
    y: p1.y + t * dy1
  };
}

/**
 * Appends a new point to an in-progress polygon, ensuring consecutive duplicates
 * are filtered out without destructively truncating user-drawn vertices mid-drawing.
 */
export function addPolygonPointResolvingIntersections(points, newPoint) {
  if (!Array.isArray(points) || points.length === 0) {
    return [{ x: round(newPoint.x), y: round(newPoint.y) }];
  }

  const result = points.map((p) => ({ x: Number(p.x) || 0, y: Number(p.y) || 0 }));
  const target = { x: round(newPoint.x), y: round(newPoint.y) };

  const last = result[result.length - 1];
  if (!last || Math.hypot(last.x - target.x, last.y - target.y) >= 1) {
    result.push(target);
  }

  return result;
}

/**
 * Resolves self-intersections when closing a polygon by finding loops and preserving
 * the primary valid polygon area using shoelace area comparison.
 */
export function resolvePolygonClosingIntersections(points) {
  return resolveClosedPolygonIntersections(points);
}

/**
 * Calculates 2D polygon area using the shoelace formula.
 */
export function polygonArea(points) {
  if (!Array.isArray(points) || points.length < 3) return 0;
  let area = 0;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    area += (points[j].x * points[i].y) - (points[i].x * points[j].y);
  }
  return Math.abs(area) / 2;
}

/**
 * Removes duplicate consecutive vertices within 1px distance.
 */
export function filterConsecutiveDuplicates(pts) {
  if (!Array.isArray(pts) || !pts.length) return [];
  const res = [{ x: round(pts[0].x), y: round(pts[0].y) }];
  for (let i = 1; i < pts.length; i++) {
    const prev = res[res.length - 1];
    const curr = { x: round(pts[i].x), y: round(pts[i].y) };
    if (Math.hypot(prev.x - curr.x, prev.y - curr.y) >= 1) {
      res.push(curr);
    }
  }
  if (res.length > 1) {
    const first = res[0];
    const last = res[res.length - 1];
    if (Math.hypot(first.x - last.x, first.y - last.y) < 1) {
      res.pop();
    }
  }
  return res;
}

/**
 * Resolves self-intersections on a finished/closed polygon (e.g. after moving a vertex).
 * When two non-adjacent sides intersect, the intersected loop is deleted and replaced
 * with the intersection vertex, keeping the primary valid polygon intact.
 */
export function resolveClosedPolygonIntersections(points, movedPointIndex = -1) {
  if (!Array.isArray(points) || points.length < 4) {
    return points || [];
  }

  let result = filterConsecutiveDuplicates(points);
  let maxIterations = 50;

  while (maxIterations-- > 0 && result.length >= 4) {
    const N = result.length;
    let foundIntersection = false;

    for (let i = 0; i < N; i++) {
      const p1 = result[i];
      const p2 = result[(i + 1) % N];

      for (let j = i + 2; j < N; j++) {
        // Skip adjacent edges in cyclical polygon
        if (i === 0 && j === N - 1) continue;

        const p3 = result[j];
        const p4 = result[(j + 1) % N];

        const hit = getLineSegmentsIntersection(p1, p2, p3, p4);
        if (hit && hit.t > 1e-4 && hit.t < 1.0 - 1e-4 && hit.u > 1e-4 && hit.u < 1.0 - 1e-4) {
          const ix = round(hit.x);
          const iy = round(hit.y);
          const intersectionPoint = { x: ix, y: iy };

          // Loop A: 0..i, intersectionPoint, (j+1)..N-1
          const loopA = [];
          for (let k = 0; k <= i; k++) loopA.push(result[k]);
          loopA.push(intersectionPoint);
          for (let k = j + 1; k < N; k++) loopA.push(result[k]);

          // Loop B: intersectionPoint, (i+1)..j
          const loopB = [intersectionPoint];
          for (let k = i + 1; k <= j; k++) loopB.push(result[k]);

          const cleanA = filterConsecutiveDuplicates(loopA);
          const cleanB = filterConsecutiveDuplicates(loopB);

          const areaA = polygonArea(cleanA);
          const areaB = polygonArea(cleanB);

          // Select the primary loop
          if (cleanA.length >= 3 && (cleanB.length < 3 || areaA >= areaB)) {
            result = cleanA;
          } else if (cleanB.length >= 3) {
            result = cleanB;
          } else {
            result = cleanA.length >= cleanB.length ? cleanA : cleanB;
          }

          foundIntersection = true;
          break;
        }
      }
      if (foundIntersection) break;
    }

    if (!foundIntersection) break;
  }

  return result;
}



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

export function pointInOrOnPolygon(point, polygon) {
  if (!polygon?.length) return false;

  let inside = false;
  for (let index = 0, nextIndex = polygon.length - 1; index < polygon.length; nextIndex = index, index += 1) {
    const current = polygon[index];
    const previous = polygon[nextIndex];
    
    // Check if on boundary
    const crossProduct = (point.y - current.y) * (previous.x - current.x) - (point.x - current.x) * (previous.y - current.y);
    if (Math.abs(crossProduct) < 1e-3) {
      const dotProduct = (point.x - current.x) * (previous.x - current.x) + (point.y - current.y) * (previous.y - current.y);
      if (dotProduct >= -1e-3) {
        const sqLen = (previous.x - current.x)**2 + (previous.y - current.y)**2;
        if (dotProduct <= sqLen + 1e-3) {
          return true;
        }
      }
    }

    const intersects = ((current.y > point.y) !== (previous.y > point.y)) &&
      (point.x < ((previous.x - current.x) * (point.y - current.y) / (previous.y - current.y + Number.EPSILON)) + current.x);
    if (intersects) inside = !inside;
  }
  return inside;
}

export function isPointInsideOtherGroupPolygons(point, currentAnn, groupAnns) {
  if (!groupAnns || groupAnns.length <= 1) return false;
  for (const otherAnn of groupAnns) {
    if (otherAnn === currentAnn) continue;
    const otherPoints = annotationPoints(otherAnn);
    if (pointInOrOnPolygon(point, otherPoints)) {
      return true;
    }
  }
  return false;
}

const UNION_EPSILON = 1e-6;

// An input vertex lies *on* the union boundary by construction, but the split /
// intersect / round pipeline moves it by a fraction of a pixel, so an exact test
// reads it as outside and rejects a correct merge. A 0.05px window is far below
// anything an annotator can see or draw, and far above the accumulated error
// (measured at ~0.005px).
const CONTAINMENT_TOLERANCE = 0.05;

/**
 * True only for points in a polygon's interior, never on its border.
 */
function pointStrictlyInside(point, polygon, tolerance = UNION_EPSILON) {
  if (pointOnPolygonBoundary(point, polygon, tolerance)) return false;
  return pointInPolygon(point, polygon);
}

/**
 * True when the two polygons share actual area or a stretch of border — the
 * precondition for merging them into a single outline.
 *
 * Contact at a single point (corner-to-corner) does NOT count. Bridging across
 * such a pinch would sweep the empty space on either side of it into the merged
 * shape, so those annotations must stay separate.
 */
export function polygonsTouch(a, b) {
  if (!a?.length || !b?.length) return false;

  // Edges that properly cross. Endpoint-only grazing is excluded, so two shapes
  // meeting at a single corner do not qualify.
  for (let i = 0; i < a.length; i++) {
    const p1 = a[i];
    const p2 = a[(i + 1) % a.length];
    for (let j = 0; j < b.length; j++) {
      const p3 = b[j];
      const p4 = b[(j + 1) % b.length];
      const hit = getLineSegmentsIntersection(p1, p2, p3, p4);
      if (hit &&
          hit.t > UNION_EPSILON && hit.t < 1 - UNION_EPSILON &&
          hit.u > UNION_EPSILON && hit.u < 1 - UNION_EPSILON) {
        return true;
      }
    }
  }

  // Shared area without crossing edges: one shape sits inside the other, or the
  // two lie along a common border. Sampling edge midpoints (rather than
  // vertices, which are ambiguous exactly at a corner touch) tells a real
  // overlap from two shapes that merely meet at a point.
  for (const [first, second] of [[a, b], [b, a]]) {
    for (let i = 0; i < first.length; i++) {
      const p1 = first[i];
      const p2 = first[(i + 1) % first.length];
      const mid = { x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 };
      if (pointStrictlyInside(mid, second)) return true;
      if (pointOnPolygonBoundary(mid, second, UNION_EPSILON)) return true;
    }
  }

  return false;
}

function samePoint(a, b, epsilon = 1e-3) {
  return Math.hypot(a.x - b.x, a.y - b.y) < epsilon;
}

/**
 * Splits every edge of `subject` at the points where it crosses any edge of the
 * other polygons, so each resulting vertex run is entirely inside or entirely
 * outside those polygons.
 */
function splitPolygonAtIntersections(subject, others) {
  const result = [];

  for (let i = 0; i < subject.length; i++) {
    const p1 = subject[i];
    const p2 = subject[(i + 1) % subject.length];
    result.push(p1);

    const cuts = [];
    for (const other of others) {
      for (let j = 0; j < other.length; j++) {
        const p3 = other[j];
        const p4 = other[(j + 1) % other.length];
        const hit = getLineSegmentsIntersection(p1, p2, p3, p4);
        if (!hit) continue;
        if (hit.t <= UNION_EPSILON || hit.t >= 1 - UNION_EPSILON) continue;
        if (hit.u < -UNION_EPSILON || hit.u > 1 + UNION_EPSILON) continue;
        cuts.push({ t: hit.t, x: hit.x, y: hit.y });
      }
    }

    cuts.sort((a, b) => a.t - b.t);
    for (const cut of cuts) {
      const last = result[result.length - 1];
      if (!samePoint(last, cut)) result.push({ x: cut.x, y: cut.y });
    }
  }

  return result;
}

/**
 * True when every shape is reachable from every other through a chain of
 * touching shapes (A touches B, B touches C -> connected).
 */
function shapesAreConnected(shapes) {
  const seen = new Set([0]);
  const queue = [0];

  while (queue.length) {
    const current = queue.pop();
    for (let i = 0; i < shapes.length; i++) {
      if (seen.has(i)) continue;
      // polygonsTouch requires shared area or a run of border, so shapes
      // separated by even a hairline gap are not treated as connected.
      if (!polygonsTouch(shapes[current], shapes[i])) continue;
      seen.add(i);
      queue.push(i);
    }
  }
  return seen.size === shapes.length;
}

/**
 * Drops vertices that sit on the straight line between their neighbours, so the
 * merged outline keeps only real corners instead of leftover split points.
 */
function dropCollinearVertices(points, tolerance = 1e-6) {
  if (points.length < 3) return points;

  const result = [];
  for (let i = 0; i < points.length; i++) {
    const previous = points[(i - 1 + points.length) % points.length];
    const current = points[i];
    const next = points[(i + 1) % points.length];

    const cross = (current.x - previous.x) * (next.y - previous.y) -
      (current.y - previous.y) * (next.x - previous.x);
    const scale = Math.hypot(next.x - previous.x, next.y - previous.y);
    if (scale > 0 && Math.abs(cross) / scale <= tolerance) continue;

    result.push(current);
  }
  return result.length >= 3 ? result : points;
}

/**
 * Interior angle at vertex `i`, in degrees.
 */
function interiorAngleAt(points, i) {
  const current = points[i];
  const previous = points[(i - 1 + points.length) % points.length];
  const next = points[(i + 1) % points.length];

  const toPrevious = Math.atan2(previous.y - current.y, previous.x - current.x);
  const toNext = Math.atan2(next.y - current.y, next.x - current.x);
  let delta = Math.abs(toPrevious - toNext);
  if (delta > Math.PI) delta = 2 * Math.PI - delta;
  return delta * 180 / Math.PI;
}

/**
 * Rounds the cusps a union leaves where two shapes crossed, without disturbing
 * corners the annotator actually drew.
 *
 * The two cases cannot be told apart by angle — a merged box's legitimate corner
 * (90°) is sharper than a typical blob cusp (~99°). What separates them is the
 * neighbourhood: a traced curve (a SAM mask, a hand-drawn polygon) arrives as
 * many sub-pixel segments, so a sharp turn between two *short* segments is an
 * artefact of the crossing. A sharp turn between long straight edges is a real
 * corner and is left exactly as it is.
 *
 * Replaces such a cusp with a small arc inset along its own two edges, so the
 * rounding never extends beyond the original outline.
 */
export function smoothUnionCusps(points, {
  angleThreshold = 150,
  maxSegmentLength = 3,
  arcSteps = 4
} = {}) {
  if (!Array.isArray(points) || points.length < 3) return points || [];

  const result = [];

  for (let i = 0; i < points.length; i++) {
    const current = points[i];
    const previous = points[(i - 1 + points.length) % points.length];
    const next = points[(i + 1) % points.length];

    const toPreviousLength = Math.hypot(current.x - previous.x, current.y - previous.y);
    const toNextLength = Math.hypot(next.x - current.x, next.y - current.y);

    const isSharp = interiorAngleAt(points, i) < angleThreshold;
    // Both neighbours short => this sits inside a densely traced curve.
    const onTracedCurve = toPreviousLength <= maxSegmentLength &&
      toNextLength <= maxSegmentLength;

    if (!isSharp || !onTracedCurve) {
      result.push(current);
      continue;
    }

    // Inset at most a third of each edge, so neighbouring vertices keep their
    // own geometry and the arc stays inside the original corner.
    const inset = Math.min(toPreviousLength, toNextLength) / 3;
    if (inset <= 1e-6) {
      result.push(current);
      continue;
    }

    const start = {
      x: current.x + (previous.x - current.x) * (inset / toPreviousLength),
      y: current.y + (previous.y - current.y) * (inset / toPreviousLength)
    };
    const end = {
      x: current.x + (next.x - current.x) * (inset / toNextLength),
      y: current.y + (next.y - current.y) * (inset / toNextLength)
    };

    // Quadratic Bézier through the corner: start -> current (control) -> end.
    for (let step = 0; step <= arcSteps; step++) {
      const t = step / arcSteps;
      const inverse = 1 - t;
      result.push({
        x: inverse * inverse * start.x + 2 * inverse * t * current.x + t * t * end.x,
        y: inverse * inverse * start.y + 2 * inverse * t * current.y + t * t * end.y
      });
    }
  }

  return result.length >= 3 ? result : points;
}

/**
 * True when the point lies on one of the polygon's edges (within tolerance),
 * as opposed to strictly inside or strictly outside it.
 */
function pointOnPolygonBoundary(point, polygon, tolerance = 1e-6) {
  for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
    const p1 = polygon[j];
    const p2 = polygon[i];
    const dx = p2.x - p1.x;
    const dy = p2.y - p1.y;
    const lengthSquared = dx * dx + dy * dy;
    if (lengthSquared === 0) continue;

    let t = ((point.x - p1.x) * dx + (point.y - p1.y) * dy) / lengthSquared;
    t = Math.max(0, Math.min(1, t));
    const distance = Math.hypot(point.x - (p1.x + t * dx), point.y - (p1.y + t * dy));
    if (distance <= tolerance) return true;
  }
  return false;
}

/**
 * Shoelace area keeping its sign: positive for counter-clockwise winding
 * in image coordinates, negative for clockwise.
 */
function signedArea(points) {
  let area = 0;
  for (let i = 0, j = points.length - 1; i < points.length; j = i++) {
    area += (points[j].x * points[i].y) - (points[i].x * points[j].y);
  }
  return area / 2;
}

/**
 * At a junction where several arcs leave the same vertex, picks the one that
 * continues most nearly straight ahead.
 *
 * Turning consistently outward is only right for a convex outline; on a concave
 * boundary the outermost turn cuts across the concavity and drops real edge, so
 * the walk must instead follow the smallest deviation from the incoming
 * direction. Every candidate here already survived the interior test, so any of
 * them lies on the union — this only decides the order they are visited in.
 */
function pickStraightestContinuation(previous, vertex, candidates) {
  if (!previous) return candidates[0];

  const incoming = Math.atan2(vertex.y - previous.y, vertex.x - previous.x);
  let best = candidates[0];
  let smallestDeviation = Infinity;

  for (const candidate of candidates) {
    const outgoing = Math.atan2(candidate.to.y - vertex.y, candidate.to.x - vertex.x);
    let turn = outgoing - incoming;
    while (turn <= -Math.PI) turn += 2 * Math.PI;
    while (turn > Math.PI) turn -= 2 * Math.PI;

    const deviation = Math.abs(turn);
    if (deviation < smallestDeviation) {
      smallestDeviation = deviation;
      best = candidate;
    }
  }
  return best;
}

/**
 * True when any vertex appears more than once in the ring — the signature of an
 * outline that pinches shut at a point instead of enclosing a single region.
 */
function hasRepeatedVertex(points) {
  for (let i = 0; i < points.length; i++) {
    for (let j = i + 1; j < points.length; j++) {
      if (samePoint(points[i], points[j])) return true;
    }
  }
  return false;
}

/**
 * Unifies endpoints that denote the same location, in place.
 *
 * A crossing point is computed independently while splitting each shape, so the
 * two copies can disagree slightly. Chaining compares endpoints with a small
 * tolerance, and a disagreement wider than that tolerance silently breaks the
 * ring, so every endpoint within `tolerance` of an earlier one is replaced by
 * that first representative.
 */
function snapSharedEndpoints(edges, tolerance = 1e-3) {
  const representatives = [];

  const canonical = (point) => {
    for (const candidate of representatives) {
      if (Math.hypot(candidate.x - point.x, candidate.y - point.y) <= tolerance) {
        return candidate;
      }
    }
    const fresh = { x: point.x, y: point.y };
    representatives.push(fresh);
    return fresh;
  };

  for (const edge of edges) {
    edge.from = canonical(edge.from);
    edge.to = canonical(edge.to);
  }
}

/**
 * Sum of the input areas. Overlaps are counted twice, so this is an upper bound
 * on a correct union — a result exceeding it has swallowed empty space.
 */
function sumOfAreas(shapes) {
  return shapes.reduce((total, shape) => total + polygonArea(shape), 0);
}

/**
 * True when the candidate outline covers every input shape, checked on each
 * shape's vertices and edge midpoints. Guards against a walk that closed early
 * and left part of an annotation outside the merged result.
 */
function containsAllShapes(outline, shapes, tolerance = CONTAINMENT_TOLERANCE) {
  const covered = (point) => (
    pointOnPolygonBoundary(point, outline, tolerance) || pointInPolygon(point, outline)
  );

  for (const shape of shapes) {
    for (let i = 0; i < shape.length; i++) {
      const current = shape[i];
      const next = shape[(i + 1) % shape.length];
      const mid = { x: (current.x + next.x) / 2, y: (current.y + next.y) / 2 };
      if (!covered(current)) return false;
      if (!covered(mid)) return false;
    }
  }
  return true;
}

/**
 * Merges a set of touching/overlapping polygons into a single outline.
 *
 * Walks the boundary of each polygon, keeps only the arcs that lie outside every
 * other polygon, and stitches those arcs together. The vertices that fall in the
 * overlap — the intersection coordinates the annotator no longer wants to see or
 * drag — are dropped entirely, so the merged shape is one real polygon rather
 * than several shapes painted to look like one.
 *
 * Returns null when the polygons do not form a single connected region, so the
 * caller can leave them as separate shapes instead of producing a broken outline.
 */
export function unionPolygons(polygons) {
  const shapes = (polygons || [])
    .map((pts) => filterConsecutiveDuplicates(pts))
    .filter((pts) => pts.length >= 3);

  if (shapes.length === 0) return null;
  if (shapes.length === 1) return shapes[0];

  // A union is only meaningful for one connected region; disjoint shapes must
  // stay separate rather than be stitched into a bogus outline.
  if (!shapesAreConnected(shapes)) return null;

  // Drop shapes fully swallowed by another one; they contribute no boundary.
  // Containment is checked on edge midpoints as well as vertices: a shape
  // spanning a concave notch can have every corner on the other's boundary
  // while its middle crosses open space, and absorbing it would swallow that gap.
  const kept = shapes.filter((shape, index) => !shapes.some((other, otherIndex) => (
    otherIndex !== index &&
    polygonArea(other) >= polygonArea(shape) &&
    containsAllShapes(other, [shape])
  )));
  const active = kept.length ? kept : [shapes[0]];
  if (active.length === 1) return active[0];

  // Ensure every shape is wound the same way, so the outline walk sees a
  // consistent traversal direction regardless of how the user drew each shape.
  const oriented = active.map((shape) => (signedArea(shape) > 0 ? [...shape].reverse() : shape));

  // Collect the boundary arcs that survive the union: an edge is on the outline
  // only when its midpoint lies outside all the other polygons.
  const edges = [];
  for (let index = 0; index < oriented.length; index++) {
    const others = oriented.filter((_, otherIndex) => otherIndex !== index);
    const split = splitPolygonAtIntersections(oriented[index], others);

    for (let i = 0; i < split.length; i++) {
      const from = split[i];
      const to = split[(i + 1) % split.length];
      if (samePoint(from, to)) continue;

      const mid = { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2 };
      // Strictly-interior arcs are dropped; an arc lying *on* another shape's
      // border is part of the shared outline and is kept. `pointStrictlyInside`
      // makes that one decision with a single tolerance — combining
      // `pointInOrOnPolygon` (which carries its own 1e-3 boundary slack) with a
      // separate boundary test let the two disagree, so a midpoint just inside
      // another shape could be read as "on the border" and wrongly kept. That
      // kept both shapes' long arcs and produced a ring that excluded parts of
      // its own inputs.
      if (others.some((other) => pointStrictlyInside(mid, other))) continue;

      // A border shared with another shape (collinear, not strictly inside) is
      // produced once per shape; keep a single copy or the walk forks.
      if (edges.some((edge) => samePoint(edge.from, from) && samePoint(edge.to, to))) continue;

      // `shape` records which polygon contributed this arc. At a junction the
      // walk continues along the same polygon whenever it can, which is what
      // keeps it on the true outline through a concave region — picking purely
      // by turn angle cuts a shortcut across the concavity and drops boundary.
      edges.push({ from, to, used: false, shape: index });
    }
  }

  if (!edges.length) return null;

  // Each crossing point is computed twice — once while splitting each shape —
  // and the two results can differ by more than the chaining tolerance. Snap
  // every endpoint to a shared representative so arcs from different shapes
  // meet exactly; otherwise the chain breaks at a crossing and reconnects to
  // the wrong arc, producing a ring that omits part of its own inputs.
  snapSharedEndpoints(edges);

  // Start from an edge that is guaranteed to be on the outer hull: the one
  // leaving the leftmost (then lowest) vertex of the whole set.
  let start = edges[0];
  for (const edge of edges) {
    if (edge.from.x < start.from.x - 1e-9 ||
        (Math.abs(edge.from.x - start.from.x) < 1e-9 && edge.from.y < start.from.y)) {
      start = edge;
    }
  }

  // Stitch the arcs head-to-tail into one closed ring.
  start.used = true;
  const ring = [start.from, start.to];
  let currentShape = start.shape;

  while (true) {
    const tail = ring[ring.length - 1];
    const candidates = edges.filter((edge) => !edge.used && samePoint(edge.from, tail));

    // Close only when back at the start with nothing left to walk from here;
    // closing early would cut off arcs that are still part of the outline.
    if (ring.length > 2 && samePoint(tail, ring[0]) && !candidates.length) {
      ring.pop();
      break;
    }

    if (!candidates.length) {
      // The arcs did not close: the shapes do not form one connected outline.
      return null;
    }

    // Stay on the polygon currently being traced. Its next arc was already
    // checked against every other shape when the edges were built, so if it
    // survived it genuinely is on the union boundary. Only when this polygon's
    // boundary is submerged here does the walk cross to another shape, which is
    // exactly what a crossing point means.
    const sameShape = candidates.filter((edge) => edge.shape === currentShape);
    const next = sameShape.length === 1
      ? sameShape[0]
      : (sameShape.length > 1
        ? pickStraightestContinuation(ring[ring.length - 2], tail, sameShape)
        : pickStraightestContinuation(ring[ring.length - 2], tail, candidates));

    next.used = true;
    currentShape = next.shape;
    ring.push(next.to);

    if (ring.length > edges.length + 2) return null;
  }

  const merged = dropCollinearVertices(filterConsecutiveDuplicates(ring));
  if (merged.length < 3) return null;

  // A vertex visited twice means the ring pinches shut at a point: the shapes
  // only met at a corner, and the outline doubles back rather than enclosing one
  // region. Merging there would sweep in the empty space either side of the pinch.
  if (hasRepeatedVertex(merged)) return null;

  // The union must contain every input shape. A walk that closed early — or a
  // region whose true union has a hole, which a single ring cannot express —
  // would silently drop part of an annotation, so refuse instead.
  if (!containsAllShapes(merged, active)) return null;

  // It must also not invent area: bridging across a pinch point would swallow
  // empty space that belongs to neither shape.
  if (polygonArea(merged) > sumOfAreas(active) + 1e-3) return null;

  // The exact union. Cusp smoothing is deliberately left to the caller so the
  // guards above always validate the true geometry.
  return merged;
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
  if (!last || Math.hypot(last.x - target.x, last.y - target.y) >= 1e-3) {
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
 * Removes duplicate consecutive vertices (within sub-pixel precision).
 */
export function filterConsecutiveDuplicates(pts) {
  if (!Array.isArray(pts) || !pts.length) return [];
  const res = [{ x: round(pts[0].x), y: round(pts[0].y) }];
  for (let i = 1; i < pts.length; i++) {
    const prev = res[res.length - 1];
    const curr = { x: round(pts[i].x), y: round(pts[i].y) };
    if (Math.hypot(prev.x - curr.x, prev.y - curr.y) >= 1e-3) {
      res.push(curr);
    }
  }
  if (res.length > 1) {
    const first = res[0];
    const last = res[res.length - 1];
    if (Math.hypot(first.x - last.x, first.y - last.y) < 1e-3) {
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



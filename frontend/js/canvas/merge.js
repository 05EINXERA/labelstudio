// Union of two or more overlapping annotation rings ("merge objects").
//
// The user selects N overlapping shapes and asks for one shape covering their
// combined area. This module is the geometry: given N rings it returns the ring
// tracing their outer boundary, or refuses.
//
// One ring in, one ring out — the same hard constraint untangle.js works under.
// `annotation.points` is a single flat array of vertices everywhere in the
// stack (geometry.js annotationPoints, formats/common.py polygon_points,
// schemas.py PointModel, the canvas draw loop). There is no hole and no
// multi-ring representation to put a second ring into, so:
//
//   - Union only. No intersection, difference or XOR — those routinely produce
//     several disjoint pieces or a hole, and there is nowhere to put them.
//   - Only *overlapping* rings merge. Two disjoint shapes have a two-piece
//     union, so the command refuses rather than inventing a bridge between them
//     or silently dropping one.
//   - Holes are impossible by construction: a ring enclosing a void would need
//     a second ring to describe it. A union that would produce one (an annulus,
//     e.g. a C-shape closed off by a bar) keeps the outer boundary only, and
//     the enclosed void is simply filled. An accepted limitation, not a bug.
//
// Never automatic. Overlapping shapes are a legal, untouched state on the
// canvas; nothing here runs unless the user presses M or the toolbar button.
// That is the difference from untangle.js, which fires on every committed hand
// edit — merge destroys N annotations to create 1, so it is always an explicit
// act, and always undoable in one step.
//
// Pure module: no state, no DOM, no canvas access. Imports only untangle.js,
// which is pure too. Note it does NOT reuse geometry.js's pointInPolygon: that
// predicate is undefined on the boundary, which merge depends on — see
// strictlyInside below. Unit-testable under plain Node — see
// tests/js/merge_spec.mjs.

import { segmentsIntersect, ringArea, isSimpleRing } from "./untangle.js?v=2";

// Coordinates are rounded to integers on every write (geometry.js
// updateAnnotationBounds), so exactly-shared vertices and exactly-collinear
// edges are ordinary cases here, not floating-point noise.
const EPS = 1e-9;

// Mirrors untangle.js's constant of the same name (module-private there). A
// union smaller than this is a sliver, not something an annotator drew.
const MIN_LOOP_AREA = 0.5;

const samePoint = (a, b) => Math.abs(a.x - b.x) < EPS && Math.abs(a.y - b.y) < EPS;

/** Clean a caller's ring: drop non-finite points and consecutive duplicates. */
function normaliseRing(points) {
  if (!Array.isArray(points)) return [];
  const out = [];
  for (const p of points) {
    const x = Number(p?.x);
    const y = Number(p?.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) continue;
    const point = { x, y };
    if (out.length && samePoint(out[out.length - 1], point)) continue;
    out.push(point);
  }
  // The ring is implicitly closed, so an explicit repeat of the first point at
  // the end is a duplicate vertex rather than a separate one.
  while (out.length > 1 && samePoint(out[0], out[out.length - 1])) out.pop();
  return out;
}

/**
 * Signed shoelace area sign: +1 counter-clockwise, -1 clockwise, 0 degenerate.
 *
 * untangle.js's ringArea is deliberately sign-free (it only ever compares
 * magnitudes), so orientation is computed here rather than changing it there.
 */
export function ringOrientation(points) {
  const ring = Array.isArray(points) ? points : [];
  const n = ring.length;
  if (n < 3) return 0;
  let total = 0;
  for (let i = 0; i < n; i += 1) {
    const a = ring[i];
    const b = ring[(i + 1) % n];
    total += a.x * b.y - b.x * a.y;
  }
  if (Math.abs(total) < EPS) return 0;
  return total > 0 ? 1 : -1;
}

/**
 * The ring wound counter-clockwise, reversing a copy when it is not.
 *
 * The walk below assumes both rings run the same way round; two shapes drawn in
 * opposite directions would otherwise trace each other backwards and produce a
 * self-crossing result. Callers hand us whatever the annotator drew, so this
 * runs on every input.
 */
export function ensureCCW(points) {
  const ring = Array.isArray(points) ? points : [];
  return ringOrientation(ring) < 0 ? ring.slice().reverse() : ring;
}

/** Every proper crossing between the two rings, as {i, j, x, y, t, u}. */
function collectCrossings(a, b) {
  const hits = [];
  for (let i = 0; i < a.length; i += 1) {
    const a1 = a[i];
    const a2 = a[(i + 1) % a.length];
    for (let j = 0; j < b.length; j += 1) {
      const b1 = b[j];
      const b2 = b[(j + 1) % b.length];
      const hit = segmentsIntersect(a1, a2, b1, b2);
      if (hit) hits.push({ i, j, x: hit.x, y: hit.y, t: hit.t, u: hit.u });
    }
  }
  return hits;
}

/** True when `point` lies on the segment a->b, endpoints included. */
function onSegment(point, a, b) {
  const cross = (b.x - a.x) * (point.y - a.y) - (b.y - a.y) * (point.x - a.x);
  if (Math.abs(cross) > EPS) return false;
  return point.x >= Math.min(a.x, b.x) - EPS && point.x <= Math.max(a.x, b.x) + EPS &&
         point.y >= Math.min(a.y, b.y) - EPS && point.y <= Math.max(a.y, b.y) + EPS;
}

/** True when `point` lies exactly on the ring's outline. */
function onBoundary(point, ring) {
  for (let i = 0; i < ring.length; i += 1) {
    if (onSegment(point, ring[i], ring[(i + 1) % ring.length])) return true;
  }
  return false;
}

/**
 * True when `point` is *strictly* inside the ring — boundary excluded.
 *
 * Deliberately not geometry.js's pointInPolygon. That one is a bare ray cast
 * whose result on the boundary itself is undefined: for a 10x10 square it
 * reports the corner (0,0) as inside and (10,10) as outside, which is fine for
 * its job (hit-testing a click, where a pixel either way is invisible) and
 * wrong for this one. Merge asks a different question — two shapes sharing only
 * an edge must read as *not* containing each other, and every vertex of a ring
 * lies on its own union's boundary — so boundary points are ruled out first and
 * the ray cast only decides genuine interior points.
 */
function strictlyInside(point, ring) {
  if (ring.length < 3) return false;
  if (onBoundary(point, ring)) return false;

  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const current = ring[i];
    const previous = ring[j];
    const straddles = (current.y > point.y) !== (previous.y > point.y);
    if (!straddles) continue;
    const x = ((previous.x - current.x) * (point.y - current.y)) /
      (previous.y - current.y) + current.x;
    if (point.x < x) inside = !inside;
  }
  return inside;
}

/** True when any vertex of `inner` lies strictly inside `outer`. */
function anyVertexInside(inner, outer) {
  return inner.some((p) => strictlyInside(p, outer));
}

/**
 * Do these rings overlap enough to have a single-ring union?
 *
 * Two ways to qualify: a proper crossing of their outlines, or containment of
 * one in the other. Shapes that merely *touch* along an edge or at a vertex
 * produce no proper crossing (segmentsIntersect rejects shared endpoints and
 * collinear overlap by design) and no interior vertex, so they read as
 * non-overlapping and merge refuses them. That is the honest answer: their
 * union is two shapes joined at a seam of zero width, and walking it would
 * depend entirely on rounding.
 */
export function ringsOverlap(ringA, ringB) {
  const a = normaliseRing(ringA);
  const b = normaliseRing(ringB);
  if (a.length < 3 || b.length < 3) return false;
  if (collectCrossings(a, b).length > 0) return true;
  return anyVertexInside(a, b) || anyVertexInside(b, a);
}

/**
 * Split every edge of `ring` at the crossings that fall on it.
 *
 * Returns a new vertex list with the crossing points spliced in as real
 * vertices, each tagged { crossing: true, key } so the walk can recognise the
 * same crossing on both rings by identity rather than by coordinate compare.
 *
 * Several crossings can land on one edge — the criss-cross case, e.g. a comb
 * laid across a bar — so the crossings on each edge are sorted by their edge
 * parameter before splicing. Sorting by anything else (distance from origin,
 * discovery order) reverses a pair on some edges and knots the ring. Edges are
 * emitted in index order with their crossings appended as they are passed, so
 * no insertion can invalidate an index not yet processed.
 */
function splitEdges(ring, hits, paramKey, indexKey) {
  const byEdge = new Map();
  for (const hit of hits) {
    const edge = hit[indexKey];
    if (!byEdge.has(edge)) byEdge.set(edge, []);
    byEdge.get(edge).push(hit);
  }

  const out = [];
  for (let i = 0; i < ring.length; i += 1) {
    out.push({ x: ring[i].x, y: ring[i].y, crossing: false, key: null });
    const onEdge = byEdge.get(i);
    if (!onEdge) continue;
    onEdge.sort((p, q) => p[paramKey] - q[paramKey]);
    for (const hit of onEdge) {
      const point = { x: hit.x, y: hit.y, crossing: true, key: hit.key };
      // A crossing landing exactly on a vertex would emit a zero-length edge;
      // promote the vertex to the crossing rather than carry both.
      if (out.length && samePoint(out[out.length - 1], point)) {
        out[out.length - 1] = point;
        continue;
      }
      out.push(point);
    }
  }
  return out;
}

/** Index of the entry in `list` carrying this crossing key, or -1. */
const indexOfKey = (list, key) => list.findIndex((p) => p.crossing && p.key === key);

/**
 * Union of two rings. Returns a new ring, or null when they do not properly
 * overlap or the walk cannot be trusted.
 *
 * The walk (Weiler-Atherton style, outer boundary only):
 *   1. Both rings are wound counter-clockwise and their crossings computed.
 *   2. Containment short-circuits: no crossing but one ring inside the other
 *      means the union is just the container.
 *   3. Crossings are spliced into both rings as shared vertices.
 *   4. Start from a vertex of A known to be outside B — guaranteed to be on the
 *      union's boundary — and walk forward.
 *   5. At each crossing, switch to the other ring and keep walking. Alternating
 *      at every crossing traces the outer boundary and never enters the
 *      overlapped interior.
 *   6. Stop on return to the start. A walk exceeding the total vertex count is
 *      knotted (a degenerate or tangential crossing), so refuse rather than
 *      emit a wrong ring — merge destroys its inputs, and a silently wrong
 *      union is unrecoverable except by undo.
 */
export function unionRings(ringA, ringB) {
  const a = ensureCCW(normaliseRing(ringA));
  const b = ensureCCW(normaliseRing(ringB));
  if (a.length < 3 || b.length < 3) return null;

  const hits = collectCrossings(a, b);

  // No proper crossing: either containment (the union is the container) or the
  // rings are disjoint / merely touching, which has no single-ring union.
  if (hits.length === 0) {
    if (anyVertexInside(a, b)) return b.map((p) => ({ x: p.x, y: p.y }));
    if (anyVertexInside(b, a)) return a.map((p) => ({ x: p.x, y: p.y }));
    return null;
  }

  // An odd crossing count means an outline entered without leaving — only
  // possible when a crossing was missed or double-counted at a vertex. Refuse.
  if (hits.length % 2 !== 0) return null;

  hits.forEach((hit, index) => { hit.key = index; });

  const splitA = splitEdges(a, hits, "t", "i");
  const splitB = splitEdges(b, hits, "u", "j");

  // Every crossing must have landed on both rings, or the walk has nowhere to
  // cross over to.
  for (const hit of hits) {
    if (indexOfKey(splitA, hit.key) < 0 || indexOfKey(splitB, hit.key) < 0) return null;
  }

  // Start outside the other ring, so the first step is on the outer boundary.
  const startIndex = splitA.findIndex((p) => !p.crossing && !strictlyInside(p, b) &&
    !onBoundary(p, b));
  if (startIndex < 0) return null;

  const limit = splitA.length + splitB.length + hits.length + 4;
  const result = [];
  let current = splitA;
  let other = splitB;
  let index = startIndex;
  let steps = 0;

  do {
    const point = current[index];
    const last = result[result.length - 1];
    if (!last || !samePoint(last, point)) result.push({ x: point.x, y: point.y });

    if (point.crossing) {
      // Hop to the same crossing on the other ring and continue there.
      const mirror = indexOfKey(other, point.key);
      if (mirror < 0) return null;
      const swap = current;
      current = other;
      other = swap;
      index = mirror;
    }

    index = (index + 1) % current.length;
    steps += 1;
    if (steps > limit) return null;
  } while (!(current === splitA && index === startIndex));

  // The ring closes implicitly; drop an explicit repeat of the start point.
  while (result.length > 1 && samePoint(result[0], result[result.length - 1])) result.pop();

  if (result.length < 3) return null;
  if (ringArea(result) < MIN_LOOP_AREA) return null;

  // The union of two simple rings is a simple ring. If what came out is not
  // simple then the walk went wrong, and emitting it would corrupt geometry.
  if (!isSimpleRing(result)) return null;

  // A correct union is never smaller than its largest input.
  if (ringArea(result) + EPS < Math.max(ringArea(a), ringArea(b))) return null;

  return result;
}

/**
 * Fold N rings into one. Returns { points, merged, skipped }:
 *   points  — the union ring, or null when nothing usable was given
 *   merged  — how many input rings were absorbed
 *   skipped — input indices that could not be absorbed
 *
 * Folded largest-area first, so the accumulator starts as the biggest shape —
 * the one most likely to overlap the rest. Repeated passes for the same reason:
 * a ring that misses the accumulator on one pass can be reachable once the
 * accumulator has grown by absorbing another, so a chain A-B-C where A and C
 * meet only through B still absorbs everything regardless of selection order.
 *
 * Partial folds are reported, never hidden: the caller refuses the whole
 * command when `skipped` is non-empty rather than committing some of it.
 */
export function unionAll(rings) {
  const input = Array.isArray(rings) ? rings : [];
  const prepared = input.map((ring, index) => {
    const points = normaliseRing(ring);
    return { index, points, area: points.length >= 3 ? ringArea(points) : -1 };
  });

  const usable = prepared.filter((entry) => entry.area >= 0);
  const skipped = prepared.filter((entry) => entry.area < 0).map((entry) => entry.index);

  if (usable.length === 0) return { points: null, merged: 0, skipped };
  if (usable.length === 1) {
    return {
      points: usable[0].points.map((p) => ({ x: p.x, y: p.y })),
      merged: 1,
      skipped
    };
  }

  const ordered = usable.slice().sort((p, q) => q.area - p.area);
  let accumulator = ordered[0].points;
  let merged = 1;
  const pending = ordered.slice(1);

  let progressed = true;
  while (progressed && pending.length) {
    progressed = false;
    for (let i = 0; i < pending.length; i += 1) {
      const union = unionRings(accumulator, pending[i].points);
      if (!union) continue;
      accumulator = union;
      merged += 1;
      pending.splice(i, 1);
      i -= 1;
      progressed = true;
    }
  }

  for (const entry of pending) skipped.push(entry.index);
  skipped.sort((p, q) => p - q);

  return { points: accumulator, merged, skipped };
}

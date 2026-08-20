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
//   - One ring per *annotation*, not per merge. The boundary of a union can
//     have several components — two shapes overlapping twice can fall into two
//     disjoint pieces — so unionComponents returns them all and the caller
//     creates one annotation per piece. That is the only faithful answer the
//     single-ring model can give, and it is what keeps a merge from claiming
//     the untouched space between two overlaps.
//   - Holes are refused, not filled. A ring enclosing a void would need a
//     second ring to describe it, and there is nowhere to put one. An earlier
//     version kept the outer boundary and let the void fill in; that silently
//     annotated image the user never marked, so a union with a hole now
//     refuses and leaves every input untouched. See .devnotes/fix-merge/.
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
 * Shared setup for the walks: normalise, orient, cross, and splice.
 *
 * Returns { a, b, splitA, splitB, hits }, or null when the pair is unusable.
 * `hits` is empty when the outlines never properly cross — the caller decides
 * whether that means containment or disjointness, because the two want
 * different answers.
 */
function prepareRings(ringA, ringB) {
  const a = ensureCCW(normaliseRing(ringA));
  const b = ensureCCW(normaliseRing(ringB));
  if (a.length < 3 || b.length < 3) return null;

  const hits = collectCrossings(a, b);
  if (hits.length === 0) return { a, b, splitA: null, splitB: null, hits };

  // An odd crossing count means an outline entered without leaving — only
  // possible when a crossing was missed or double-counted at a vertex. Refuse.
  if (hits.length % 2 !== 0) return null;

  hits.forEach((hit, index) => { hit.key = index; });

  const splitA = splitEdges(a, hits, "t", "i");
  const splitB = splitEdges(b, hits, "u", "j");

  // Every crossing must have landed on both rings, or a walk reaching it has
  // nowhere to cross over to.
  for (const hit of hits) {
    if (indexOfKey(splitA, hit.key) < 0 || indexOfKey(splitB, hit.key) < 0) return null;
  }

  return { a, b, splitA, splitB, hits };
}

/**
 * Trace one boundary component, starting from `startIndex` on `startRing`.
 *
 * The walk itself is unchanged from the original single-component version:
 * step forward, and at every crossing hop to the same crossing on the other
 * ring. What is new is `visited` — every slot the walk consumes is recorded as
 * "A:12" / "B:3" so the caller can tell which seeds are still unaccounted for
 * and start another component from one of them. That bookkeeping is the whole
 * fix: the boundary of a union of two rings can have several components, and
 * the original code walked exactly one of them and discarded the rest.
 *
 * Returns the traced ring, or null when the walk knots (a degenerate or
 * tangential crossing). Area and simplicity are judged by the caller, per
 * component.
 */
function walkFrom(splitA, splitB, startRing, startIndex, visited) {
  const limit = splitA.length + splitB.length + 4;
  const result = [];
  let current = startRing;
  let other = startRing === splitA ? splitB : splitA;
  let index = startIndex;
  let steps = 0;

  do {
    const point = current[index];
    visited.add(`${current === splitA ? "A" : "B"}:${index}`);

    const last = result[result.length - 1];
    if (!last || !samePoint(last, point)) result.push({ x: point.x, y: point.y });

    if (point.crossing) {
      // Hop to the same crossing on the other ring and continue there. The
      // mirror slot is the same point in space, so mark it consumed too —
      // otherwise it looks like an unvisited seed and yields a duplicate
      // component.
      const mirror = indexOfKey(other, point.key);
      if (mirror < 0) return null;
      visited.add(`${other === splitA ? "A" : "B"}:${mirror}`);
      const swap = current;
      current = other;
      other = swap;
      index = mirror;
    }

    index = (index + 1) % current.length;
    steps += 1;
    if (steps > limit) return null;
  } while (!(current === startRing && index === startIndex));

  // The ring closes implicitly; drop an explicit repeat of the start point.
  while (result.length > 1 && samePoint(result[0], result[result.length - 1])) result.pop();

  return result;
}

/** True when every vertex of `inner` is inside or on the boundary of `outer`. */
function ringInsideRing(inner, outer) {
  return inner.every((p) => strictlyInside(p, outer) || onBoundary(p, outer));
}

/**
 * Every boundary component of the union of two rings, classified.
 *
 * Returns { outer, holes } — arrays of rings — or null when the geometry
 * cannot be trusted. `outer` is never empty on success.
 *
 * The walk (Weiler-Atherton style):
 *   1. Both rings are wound counter-clockwise and their crossings computed.
 *   2. Containment short-circuits: no crossing but one ring inside the other
 *      means the union is just the container.
 *   3. Crossings are spliced into both rings as shared vertices.
 *   4. *Every* vertex of either ring that lies outside the other is a seed: it
 *      is on the union's boundary by definition. Walk from each seed not
 *      already consumed by an earlier walk, alternating rings at every
 *      crossing.
 *   5. Each completed walk is one boundary component. A component contained in
 *      another is a hole; the rest are outer pieces.
 *
 * Step 4 is where this differs from the original implementation, which took
 * only the *first* such seed and returned after one walk. That is correct
 * whenever the union happens to be a single hole-free piece, and silently wrong
 * otherwise: two shapes overlapping twice either enclose a void (the walk
 * returned the outer ring and the void was filled) or fall into two disjoint
 * pieces (the walk returned one and the other was dropped). Both cases now come
 * back complete, and the caller decides what to do with them.
 */
export function unionComponents(ringA, ringB) {
  const prepared = prepareRings(ringA, ringB);
  if (!prepared) return null;

  const { a, b, splitA, splitB, hits } = prepared;

  // No proper crossing: either containment (the union is the container) or the
  // rings are disjoint / merely touching, which has no union we will trace.
  if (hits.length === 0) {
    if (anyVertexInside(a, b)) return { outer: [b.map((p) => ({ x: p.x, y: p.y }))], holes: [] };
    if (anyVertexInside(b, a)) return { outer: [a.map((p) => ({ x: p.x, y: p.y }))], holes: [] };
    return null;
  }

  // Seeds: non-crossing vertices lying strictly outside the other ring. Taken
  // from both rings — a component can easily contain no vertex of A at all.
  const seeds = [];
  const outside = (point, ring) => !strictlyInside(point, ring) && !onBoundary(point, ring);
  splitA.forEach((point, index) => {
    if (!point.crossing && outside(point, b)) seeds.push({ ring: splitA, index, tag: `A:${index}` });
  });
  splitB.forEach((point, index) => {
    if (!point.crossing && outside(point, a)) seeds.push({ ring: splitB, index, tag: `B:${index}` });
  });
  if (seeds.length === 0) return null;

  const visited = new Set();
  const rings = [];

  for (const seed of seeds) {
    if (visited.has(seed.tag)) continue;
    const ring = walkFrom(splitA, splitB, seed.ring, seed.index, visited);
    // A knotted walk taints the whole result: merge destroys its inputs, so a
    // partially-trusted union is not worth emitting.
    if (!ring) return null;
    if (ring.length < 3) continue;
    if (ringArea(ring) < MIN_LOOP_AREA) continue;
    // The boundary of a union of two simple rings is a set of simple rings. If
    // what came out is not simple then the walk went wrong.
    if (!isSimpleRing(ring)) return null;
    rings.push(ring);
  }

  if (rings.length === 0) return null;

  // Classify: a ring wholly inside another is a hole in it.
  const outer = [];
  const holes = [];
  for (const ring of rings) {
    const enclosed = rings.some((other) => other !== ring && ringInsideRing(ring, other));
    (enclosed ? holes : outer).push(ring);
  }
  if (outer.length === 0) return null;

  // A correct union covers at least as much as its largest input.
  const covered = outer.reduce((sum, ring) => sum + ringArea(ring), 0) -
    holes.reduce((sum, ring) => sum + ringArea(ring), 0);
  if (covered + EPS < Math.max(ringArea(a), ringArea(b))) return null;

  return { outer, holes };
}

/**
 * Union of two rings as a single ring, or null.
 *
 * Kept for callers that can only hold one ring. It is deliberately strict: a
 * union that comes back as several pieces, or with a hole in it, has no
 * faithful single-ring answer, so it refuses rather than returning the outer
 * boundary and quietly swallowing the gap. Callers that can hold several shapes
 * should use unionComponents.
 */
export function unionRings(ringA, ringB) {
  const result = unionComponents(ringA, ringB);
  if (!result) return null;
  if (result.holes.length || result.outer.length !== 1) return null;
  return result.outer[0];
}

/**
 * Fold N rings into as few pieces as possible. Returns:
 *   components — the union's boundary rings, or null when nothing was usable
 *   merged     — how many input rings were absorbed
 *   skipped    — input indices that could not be absorbed
 *   holes      — true when some union enclosed a void it could not represent
 *
 * Folded largest-area first, so the accumulator starts as the biggest shape —
 * the one most likely to overlap the rest. Repeated passes for the same reason:
 * a ring that misses every accumulator component on one pass can be reachable
 * once a component has grown by absorbing another, so a chain A-B-C where A and
 * C meet only through B still absorbs everything regardless of selection order.
 *
 * The accumulator is a *list* of components rather than one ring. Absorbing a
 * ring can also bridge two existing components into one, so after a successful
 * union the ring being absorbed is re-tried against the remaining components
 * and any it also reaches are folded in.
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

  if (usable.length === 0) return { components: null, merged: 0, skipped, holes: false };
  if (usable.length === 1) {
    return {
      components: [usable[0].points.map((p) => ({ x: p.x, y: p.y }))],
      merged: 1,
      skipped,
      holes: false
    };
  }

  const ordered = usable.slice().sort((p, q) => q.area - p.area);
  let components = [ordered[0].points];
  let merged = 1;
  let sawHole = false;
  const pending = ordered.slice(1);

  // Union one ring against the component list. Returns the new list, or null
  // when the ring reaches none of them (or the union could not be trusted).
  const absorb = (component, ring) => {
    const result = unionComponents(component, ring);
    if (!result) return null;
    if (result.holes.length) {
      // A void the annotation model cannot express. Flagged for the caller and
      // treated as a refusal so nothing is committed over the gap.
      sawHole = true;
      return null;
    }
    return result.outer;
  };

  let progressed = true;
  while (progressed && pending.length) {
    progressed = false;
    for (let i = 0; i < pending.length; i += 1) {
      const ring = pending[i].points;
      const reached = [];
      const untouched = [];
      let grown = null;

      for (const component of components) {
        const union = grown === null ? absorb(component, ring) : null;
        if (union) {
          grown = union;
          reached.push(component);
        } else {
          untouched.push(component);
        }
      }
      if (grown === null) continue;

      // The absorbed ring may also bridge components it did not reach on the
      // first pass, now that `grown` covers more ground. Fold those in too.
      let bridged = true;
      while (bridged) {
        bridged = false;
        for (let j = 0; j < untouched.length; j += 1) {
          for (let k = 0; k < grown.length; k += 1) {
            const union = absorb(grown[k], untouched[j]);
            if (!union) continue;
            grown = grown.slice(0, k).concat(union, grown.slice(k + 1));
            untouched.splice(j, 1);
            j -= 1;
            bridged = true;
            break;
          }
        }
      }

      components = untouched.concat(grown);
      merged += 1;
      pending.splice(i, 1);
      i -= 1;
      progressed = true;
    }
  }

  // A ring left pending because its union enclosed a void is *not* a
  // non-overlapping input, and must not be reported as one — "does not overlap
  // the rest" would send the annotator looking for a gap between the shapes
  // when the problem is a gap inside them. The hole flag carries that case, and
  // the caller reports it separately.
  if (!sawHole) {
    for (const entry of pending) skipped.push(entry.index);
    skipped.sort((p, q) => p - q);
  }

  return { components, merged, skipped, holes: sawHole };
}

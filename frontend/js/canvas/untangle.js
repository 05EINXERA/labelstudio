// Polygon self-intersection resolution ("criss-cross").
//
// When an edit makes a polygon's outline cross itself, the ring encloses two
// loops joined at the crossing point. This module cuts the ring at that point,
// keeps the larger loop, and drops the smaller one — turning the crossing into
// a real vertex on the survivor.
//
// Why a ring-untangle and not a boolean/clipping operation: `annotation.points`
// is a single flat array of vertices, everywhere. There is no hole or multi-ring
// representation in the canvas, in geometry.js's annotationPoints(), in
// formats/common.py's polygon_points(), or in schemas.py's PointModel. A real
// boolean op can produce several rings and would have nowhere to put them, so
// the output here is always exactly one ring and no schema changes are needed.
//
// Deliberately NOT applied to model output (SAM / YOLO polygons arriving via
// ai/detect.js). A segmentation mask can legitimately have a complex boundary,
// and silently deleting a lobe of a model's prediction would be data loss, not
// a fix. This runs only on geometry a human just edited by hand.
//
// Pure module: no imports, no canvas or state access, so it is unit-testable
// under plain Node — see tests/js/untangle_spec.mjs.

// Coordinates are rounded to integers on every write (see geometry.js
// updateAnnotationBounds), so exactly-shared endpoints and exactly-collinear
// edges are common rather than rare. Everything below has to treat those as
// ordinary cases, not as floating-point noise.
const EPS = 1e-9;

// Loops smaller than this are treated as slivers with no annotatable content
// and are dropped rather than ever being chosen as the survivor. In image
// pixels; a sub-half-pixel loop is not something an annotator drew on purpose.
const MIN_LOOP_AREA = 0.5;

function cross(ox, oy, ax, ay, bx, by) {
  return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox);
}

/** Shoelace area of a ring. Sign-free, matching formats/common.py polygon_area. */
export function ringArea(points) {
  const n = points.length;
  if (n < 3) return 0;
  let total = 0;
  for (let i = 0; i < n; i += 1) {
    const a = points[i];
    const b = points[(i + 1) % n];
    total += a.x * b.y - b.x * a.y;
  }
  return Math.abs(total) / 2;
}

/**
 * Proper crossing of segments p1->p2 and p3->p4, or null.
 *
 * "Proper" means the segments cross in their interiors: 0 < t < 1 and
 * 0 < u < 1. Touching at a shared endpoint is not a crossing — adjacent edges
 * of any ring always share one, so counting those would report every polygon
 * as self-intersecting.
 *
 * Collinear overlap returns null too. Two edges lying along each other enclose
 * zero area, so there is no "outside part" to remove; untangling there would
 * delete real geometry to fix a cosmetic degeneracy. They are left alone.
 */
export function segmentsIntersect(p1, p2, p3, p4) {
  const d1 = cross(p3.x, p3.y, p4.x, p4.y, p1.x, p1.y);
  const d2 = cross(p3.x, p3.y, p4.x, p4.y, p2.x, p2.y);
  const d3 = cross(p1.x, p1.y, p2.x, p2.y, p3.x, p3.y);
  const d4 = cross(p1.x, p1.y, p2.x, p2.y, p4.x, p4.y);

  // Any zero determinant means an endpoint lies on the other segment's line:
  // touching or collinear, neither of which is a proper crossing.
  if (Math.abs(d1) < EPS || Math.abs(d2) < EPS || Math.abs(d3) < EPS || Math.abs(d4) < EPS) {
    return null;
  }
  if ((d1 > 0) === (d2 > 0) || (d3 > 0) === (d4 > 0)) return null;

  const denom = (p2.x - p1.x) * (p4.y - p3.y) - (p2.y - p1.y) * (p4.x - p3.x);
  if (Math.abs(denom) < EPS) return null;
  const t = ((p3.x - p1.x) * (p4.y - p3.y) - (p3.y - p1.y) * (p4.x - p3.x)) / denom;

  return {
    x: p1.x + t * (p2.x - p1.x),
    y: p1.y + t * (p2.y - p1.y)
  };
}

/**
 * First proper self-intersection of a ring or open polyline, or null if it
 * is simple.
 *
 * Returns { i, j, point } where edge i->i+1 crosses edge j->j+1 and i < j.
 * Adjacent edge pairs are skipped (they share a vertex).
 *
 * `closed` (default true) controls whether edge n-1 -> 0 exists at all. A
 * finished polygon's ring really does close there, so every other call site
 * (finalize, point removal, vertex drag) leaves it true. A polygon that is
 * still being drawn by hand is an open polyline — the segment back to
 * points[0] is only a dashed preview the canvas draws to the cursor, not a
 * committed edge — so the mid-draw call site passes `closed: false` and the
 * last real edge is n-2 -> n-1, not a fabricated closing edge back to the
 * start point.
 */
export function findFirstSelfIntersection(points, closed = true) {
  const n = points.length;
  if (n < 4) return null;
  const edgeCount = closed ? n : n - 1;
  for (let i = 0; i < edgeCount; i += 1) {
    const a1 = points[i];
    const a2 = points[(i + 1) % n];
    for (let j = i + 1; j < edgeCount; j += 1) {
      // Skip edges sharing a vertex with edge i: j === i + 1, and — only
      // when the ring is actually closed — the wrap-around case where edge j
      // ends exactly where edge i begins.
      if (j === i + 1) continue;
      if (closed && i === 0 && j === n - 1) continue;
      const b1 = points[j];
      const b2 = points[(j + 1) % n];
      const hit = segmentsIntersect(a1, a2, b1, b2);
      if (hit) return { i, j, point: hit };
    }
  }
  return null;
}

/** True when the ring/polyline has no proper self-intersection. */
export function isSimpleRing(points, closed = true) {
  return findFirstSelfIntersection(points, closed) === null;
}

/**
 * Resolve every self-intersection in a ring, keeping the largest loop.
 *
 * At a crossing of edges i->i+1 and j->j+1 the ring splits into exactly two
 * loops that meet at the crossing point P:
 *
 *   loop A = P, points[i+1] .. points[j], (P closes it)
 *   loop B = P, points[j+1] .. points[i], (P closes it)
 *
 * The smaller-area loop is the "outside part" and is discarded; P survives as
 * a genuine vertex of the winner. Whichever vertex the annotator just placed
 * or dragged is an endpoint of one of the two crossing edges, so it is always
 * an interior vertex of one loop or the other and can never be the thing that
 * gets cut away.
 *
 * Repeats until simple: a figure-eight can have several crossings, and cutting
 * one can leave others. Each pass keeps strictly fewer than n vertices plus one,
 * so it converges; `n` passes is a hard ceiling against a degenerate input
 * looping forever.
 *
 * `anchor` is the vertex the annotator just committed — the clicked point, or
 * the one being dragged — passed as {x, y}. Area alone decides which loop wins,
 * except on an exact tie: a symmetric bowtie splits into two equal halves, and
 * there area carries no signal at all about which half the user meant to keep.
 * The anchor breaks that tie towards the half they were working in. Optional;
 * without it an exact tie falls back to keeping the first loop.
 *
 * `closed` (default true) must match what `findFirstSelfIntersection` was
 * told: a finished polygon is a real ring, so a crossing splits it into two
 * loops (A: the slice between the crossing edges; B: the rest, wrapping
 * through index 0) and the larger survives.
 *
 * An in-progress polyline (`closed: false`) has no wrap-around edge, but the
 * same "keep the bigger piece" rule still applies — there are still two ways
 * to cut a self-crossing open path at the crossing point P, and one of them
 * is the small stray loop the annotator did not mean to keep:
 *
 *   head = points[0..i], P       (drops points[i+1..end] — the loop plus tail)
 *   tail = P, points[j+1..end]   (drops points[0..i] — the loop plus head)
 *
 * Both are closed off at P for the area comparison (an open path encloses
 * nothing on its own); whichever side is bigger keeps drawing from P as its
 * new open end. This mirrors the closed-ring case exactly, just without a
 * wrap-around candidate.
 *
 * Returns { points, changed }. `points` is a new array when changed, and the
 * caller's original array when not — callers must not assume identity either way.
 */
export function untangleRing(input, anchor, closed = true) {
  const original = input || [];
  if (original.length < 4) return { points: original, changed: false };

  const holdsAnchor = (loop) => (
    anchor
      ? loop.some((p) => Math.abs(p.x - anchor.x) < EPS && Math.abs(p.y - anchor.y) < EPS)
      : false
  );

  let points = original;
  let changed = false;

  for (let pass = 0; pass < original.length; pass += 1) {
    const hit = findFirstSelfIntersection(points, closed);
    if (!hit) break;

    const { i, j, point } = hit;

    if (!closed) {
      // Open polyline — no wrap-around edge, but "keep the biggest piece" still
      // applies. At crossing point P there are three natural segments:
      //
      //   head = points[0..i], P            (path before the first crossing edge)
      //   loop = P, points[i+1..j]          (the enclosed middle)
      //   tail = P, points[j+1..end]        (path after the second crossing edge)
      //
      // There is also a fourth candidate that matters when the crossing is on
      // the very first edge (i === 0): the annotator drew the entire body
      // (points[0..j]) and the last click's stray tail crossed back across
      // edge 0→1. In that case neither head (just [points[0], P]) nor loop
      // (which starts at P, not points[0]) correctly preserves the full body
      // including the starting vertex:
      //
      //   body = points[0..j], P            (only added when i === 0)
      //
      // body = head_interior + loop_interior + [P]; it always has one more
      // vertex than loop, so it wins whenever the crossing is on edge 0→1
      // and the tail is smaller — covering both the "S-shape returns near
      // start" and "fish/nearly-complete oval" scenarios. When i > 0 the
      // head already contains substantive early vertices, so head/loop/tail
      // comparison is sufficient.
      //
      // Winner selection: most vertices, then most enclosed area on a tie,
      // then head on a double tie (never silently discard the starting work).
      const head = [...points.slice(0, i + 1), point];
      const loop = [point, ...points.slice(i + 1, j + 1)];
      const tail = [point, ...points.slice(j + 1)];

      // body candidate is only valid (and only needed) when i === 0
      const body = i === 0 ? [...points.slice(0, j + 1), point] : null;

      const score = (seg) => ({
        len: seg.length,
        area: seg.length >= 3 ? ringArea(seg) : 0,
      });
      const sh = score(head);
      const sl = score(loop);
      const st = score(tail);
      const sb = body ? score(body) : { len: -1, area: -1 };

      const beats = (a, b) =>
        a.len > b.len || (a.len === b.len && a.area > b.area);

      // Pick the candidate that beats all others. body beats loop by definition
      // (same vertices plus P), so we only need to compare body vs tail.
      let winner;
      if (body && !beats(sh, sb) && !beats(sl, sb) && !beats(st, sb)) winner = body;
      else if (!beats(sl, sh) && !beats(st, sh)) winner = head;
      else if (!beats(sh, sl) && !beats(st, sl)) winner = loop;
      else winner = tail;

      // When the crossing was on edge 0→1 (i === 0) and the body candidate won,
      // points[0] sits on the crossed edge itself and must be removed. The edge
      // points[0]→points[1] was the one that got cut; retaining points[0] would
      // leave a dangling line from the old start to the crossing point. Drop it
      // so the result begins at points[1] and ends at P.
      if (winner === body) winner = winner.slice(1);

      // A degenerate cut that leaves fewer than 2 points is not useful — stop.
      if (winner.length < 2) break;
      points = winner;
      changed = true;
      continue;
    }

    // Slice between the crossing edges. i < j is guaranteed by the scan order.
    const loopA = [point, ...points.slice(i + 1, j + 1)];
    const loopB = [point, ...points.slice(j + 1), ...points.slice(0, i + 1)];

    const areaA = loopA.length >= 3 ? ringArea(loopA) : 0;
    const areaB = loopB.length >= 3 ? ringArea(loopB) : 0;

    // Both loops degenerate: the crossing encloses nothing worth keeping on
    // either side. Leave the ring as it stands rather than reducing it to
    // something with fewer than 3 points, which would break every downstream
    // consumer that guards on points.length >= 3 (hitTestLine, polygon_points).
    if (areaA < MIN_LOOP_AREA && areaB < MIN_LOOP_AREA) break;

    // Area decides, except when the two loops are the same size to within
    // rounding — then keep whichever holds the anchor.
    let winner;
    if (Math.abs(areaA - areaB) < EPS && anchor && holdsAnchor(loopA) !== holdsAnchor(loopB)) {
      winner = holdsAnchor(loopA) ? loopA : loopB;
    } else {
      winner = areaA >= areaB ? loopA : loopB;
    }
    if (winner.length < 3) break;

    points = winner;
    changed = true;
  }

  return { points, changed };
}

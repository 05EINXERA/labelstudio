/**
 * Behaviour spec for polygon self-intersection resolution.
 *
 * Run: node tests/js/untangle_spec.mjs  (or via tests/test_polygon_untangle.py)
 *
 * `frontend/js/canvas/untangle.js` rewrites geometry an annotator drew, so the
 * two things worth guarding are that it fires when it should and — far more
 * importantly — that it does nothing at all when it should not. A false
 * positive here silently deletes part of someone's label.
 */
const url = new URL('../../frontend/js/canvas/untangle.js', import.meta.url);
const u = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

const P = (x, y) => ({ x, y });
const near = (a, b, tol = 1e-6) => Math.abs(a - b) < tol;
const hasVertex = (pts, x, y, tol = 1e-6) =>
  pts.some((p) => near(p.x, x, tol) && near(p.y, y, tol));

// --- 1. Simple rings are left completely alone --------------------------

const square = [P(0, 0), P(10, 0), P(10, 10), P(0, 10)];
ok('a square is simple', u.isSimpleRing(square));
ok('a square is unchanged', u.untangleRing(square).changed === false);
ok('an unchanged ring is returned as-is', u.untangleRing(square).points === square);

const concave = [P(0, 0), P(10, 0), P(10, 10), P(5, 4), P(0, 10)];
ok('a concave ring is simple', u.isSimpleRing(concave));
ok('a concave ring is unchanged', u.untangleRing(concave).changed === false);

// A ring whose edges meet only at shared vertices (every polygon) must never
// be reported as crossing — this is the failure mode that would untangle
// every shape in the workspace.
ok('adjacent edges are not a crossing', u.findFirstSelfIntersection(square) === null);
ok('a triangle is simple', u.isSimpleRing([P(0, 0), P(10, 0), P(5, 10)]));

// --- 2. The classic bowtie ----------------------------------------------

// Vertex order 0,0 -> 10,10 -> 10,0 -> 0,10 crosses at (5,5): two triangles
// of equal area meeting at a point.
const bowtie = [P(0, 0), P(10, 10), P(10, 0), P(0, 10)];
const found = u.findFirstSelfIntersection(bowtie);
ok('bowtie is detected as self-intersecting', found !== null);
ok('bowtie is not simple', !u.isSimpleRing(bowtie));
ok('crossing point is (5,5)', found && near(found.point.x, 5) && near(found.point.y, 5));

const fixed = u.untangleRing(bowtie);
ok('bowtie is changed', fixed.changed === true);
ok('bowtie result is simple', u.isSimpleRing(fixed.points));
ok('crossing became a real vertex', hasVertex(fixed.points, 5, 5));
ok('bowtie result keeps >= 3 points', fixed.points.length >= 3);

// --- 3. The asymmetric case: the smaller loop is the one that goes -------

// A square whose top-right corner was dragged out past the right-hand edge.
// That flick makes the outline cross at (100, 64.29), leaving a big body and a
// small triangular ear hanging outside. The ear is what must disappear.
const withEar = [P(0, 0), P(100, 0), P(100, 100), P(140, 50), P(0, 100)];
const earFix = u.untangleRing(withEar);
ok('eared ring is detected', !u.isSimpleRing(withEar));
ok('eared ring is resolved', earFix.changed && u.isSimpleRing(earFix.points));
ok('the large body survives', u.ringArea(earFix.points) > u.ringArea(withEar) * 0.5);
// Both the dragged-out vertex and the corner it swung past belong to the ear.
ok('the dragged-out vertex is gone', !hasVertex(earFix.points, 140, 50));
ok('the ear corner is gone', !hasVertex(earFix.points, 100, 100));
// The body's remaining corners are untouched, and the crossing is now a vertex.
ok('body corner (0,0) kept', hasVertex(earFix.points, 0, 0));
ok('body corner (0,100) kept', hasVertex(earFix.points, 0, 100));
ok('body corner (100,0) kept', hasVertex(earFix.points, 100, 0));
ok('crossing is a vertex of the body', hasVertex(earFix.points, 100, 64.28571428571429, 1e-6));

// --- 4. The just-placed vertex always survives ---------------------------
//
// The vertex the annotator just clicked or dragged is an endpoint of one of
// the two crossing edges, so it must end up inside the surviving loop. If this
// ever fails, the feature eats the very point the user was placing.

// Each ring here genuinely crosses itself; `placed` is the vertex standing in
// for the one the annotator just committed.
const placedRuns = [
  { ring: [P(0, 0), P(100, 0), P(100, 100), P(140, 50), P(0, 100)], placed: P(0, 100) },
  { ring: [P(0, 0), P(100, 100), P(100, 0), P(0, 100)], placed: P(0, 100) },
  { ring: [P(10, 10), P(90, 10), P(90, 90), P(120, 40), P(50, 95), P(10, 90)], placed: P(10, 90) },
];
placedRuns.forEach(({ ring, placed }, n) => {
  ok(`run ${n}: input really does cross`, !u.isSimpleRing(ring));
  const out = u.untangleRing(ring, placed);
  ok(`run ${n}: resolves to a simple ring`, u.isSimpleRing(out.points));
  ok(`run ${n}: keeps at least 3 points`, out.points.length >= 3);
  ok(`run ${n}: encloses real area`, u.ringArea(out.points) > 0);
  ok(`run ${n}: the just-placed vertex survives`, hasVertex(out.points, placed.x, placed.y));
});

// The anchor only breaks exact ties; it must never override a real size
// difference, or a stray click would keep the ear and delete the body.
const earAnchoredAtEar = u.untangleRing(withEar, P(140, 50));
ok('anchor does not override a real area difference',
   !hasVertex(earAnchoredAtEar.points, 140, 50));
ok('anchor-on-ear still keeps the body',
   u.ringArea(earAnchoredAtEar.points) > u.ringArea(withEar) * 0.5);

// A symmetric bowtie crossing at (5,5) has two equal triangles: a left half
// holding (0,0) and (0,10), and a right half holding (10,0) and (10,10). Area
// alone cannot choose between them, so each must be reachable by anchoring in
// it — that is the whole point of the tie-break.
const tieLeft = u.untangleRing(bowtie, P(0, 0));
const tieRight = u.untangleRing(bowtie, P(10, 0));
ok('tie keeps the left half when anchored there', hasVertex(tieLeft.points, 0, 0));
ok('the left half excludes the right vertices', !hasVertex(tieLeft.points, 10, 0));
ok('tie keeps the right half when anchored there', hasVertex(tieRight.points, 10, 0));
ok('the right half excludes the left vertices', !hasVertex(tieRight.points, 0, 0));
ok('the two tie outcomes really differ',
   JSON.stringify(tieLeft.points) !== JSON.stringify(tieRight.points));
ok('both tie outcomes are simple',
   u.isSimpleRing(tieLeft.points) && u.isSimpleRing(tieRight.points));
// The other vertex of each half comes along with it.
ok('left half carries (0,10)', hasVertex(tieLeft.points, 0, 10));
ok('right half carries (10,10)', hasVertex(tieRight.points, 10, 10));
// Without an anchor a tie must still resolve to something valid.
ok('an unanchored tie still resolves', u.isSimpleRing(u.untangleRing(bowtie).points));

// --- 5. Multiple crossings converge --------------------------------------

// A star-ish ring that crosses itself more than once: one pass is not enough,
// and the loop must still terminate.
const multi = [P(0, 0), P(100, 100), P(100, 0), P(0, 100), P(50, -50), P(50, 150)];
const multiFix = u.untangleRing(multi);
ok('multi-crossing ring is resolved fully', u.isSimpleRing(multiFix.points));
ok('multi-crossing ring stays valid', multiFix.points.length >= 3);

// --- 6. Degeneracies must not throw and must not mangle ------------------

ok('empty input is safe', u.untangleRing([]).changed === false);
ok('null input is safe', u.untangleRing(null).points.length === 0);
ok('two points are safe', u.untangleRing([P(0, 0), P(5, 5)]).changed === false);
ok('three points are never changed', u.untangleRing([P(0, 0), P(5, 0), P(0, 5)]).changed === false);

// Duplicate consecutive points: zero-length edges. These arise from the 1px
// dedupe in interactions.js not catching an exactly-repeated click.
const dupes = [P(0, 0), P(0, 0), P(10, 0), P(10, 10), P(0, 10)];
ok('duplicate points do not throw', u.isSimpleRing(dupes));
ok('duplicate points are not a crossing', u.untangleRing(dupes).changed === false);

// Collinear overlap: edges lying along one another enclose no area, so there
// is no outside part to remove and the ring is deliberately left alone.
const collinear = [P(0, 0), P(10, 0), P(5, 0), P(5, 10)];
ok('collinear overlap is not treated as a crossing', u.untangleRing(collinear).changed === false);

// A vertex sitting exactly ON a non-adjacent edge — touching, not crossing.
// Integer rounding makes this common, and cutting here would remove geometry
// over what is visually a closed shape.
const touching = [P(0, 0), P(10, 0), P(10, 10), P(5, 10), P(5, 5), P(0, 5)];
ok('a vertex touching an edge is not a crossing', u.untangleRing(touching).changed === false);

// --- 7. Area helper agrees with formats/common.py polygon_area -----------

ok('unit square area', near(u.ringArea([P(0, 0), P(1, 0), P(1, 1), P(0, 1)]), 1));
ok('area is winding-independent', near(u.ringArea([P(0, 1), P(1, 1), P(1, 0), P(0, 0)]), 1));
ok('degenerate area is zero', u.ringArea([P(0, 0), P(1, 1)]) === 0);

// --- 8. Idempotence -------------------------------------------------------
//
// Untangling an already-untangled ring must be a no-op. Every call site runs
// this on geometry that may already have passed through it.
const twice = u.untangleRing(u.untangleRing(bowtie).points);
ok('untangle is idempotent', twice.changed === false);

// --- 9. Starting-point (index-0) preservation ----------------------------
//
// After untangleIfPolygon rewrites annotation.points, the original first vertex
// must still be at index 0 when it survives into the winning loop.  The canvas
// draws the closing-vertex handle at points[0]; if it drifts to the crossing
// point the annotator can no longer close the polygon at the intended spot.
// See .devnotes/bugs/polygon-untangle-startpoint-drift.md for the full analysis.
//
// These tests exercise the rotation logic that lives in untangleIfPolygon
// (interactions.js) via a thin wrapper defined here:

function untangleIfPolygonSimulation(pts, anchor) {
  // Mirrors the rotation logic added to untangleIfPolygon in interactions.js.
  if (!Array.isArray(pts) || pts.length < 4) return { points: pts, changed: false };
  const originalFirst = pts[0];
  const result = u.untangleRing(pts, anchor);
  if (!result.changed) return result;

  let outPts = result.points;
  const EPS = 1e-9;
  const idx = outPts.findIndex(
    (p) => Math.abs(p.x - originalFirst.x) < EPS && Math.abs(p.y - originalFirst.y) < EPS
  );
  if (idx > 0) {
    outPts = [...outPts.slice(idx), ...outPts.slice(0, idx)];
  }
  return { points: outPts, changed: true };
}

// The "eared" ring from section 3: points[0] is (0,0) which is in the large
// body that wins. After untangle + rotation it must be back at index 0.
const earOut = untangleIfPolygonSimulation(withEar);
ok('index-0 preserved after eared-ring untangle',
   near(earOut.points[0].x, 0) && near(earOut.points[0].y, 0));
ok('ring is still valid after rotation', u.isSimpleRing(earOut.points));

// Verify the ring is actually the same ring (just rotated): all body vertices
// still present.
ok('(100,0) still present after rotation', hasVertex(earOut.points, 100, 0));
ok('(0,100) still present after rotation', hasVertex(earOut.points, 0, 100));

// When the original first vertex is in the DROPPED loop, we cannot restore it.
// The bowtie's first vertex (0,0) goes to the left triangle; if we anchor to
// the right side, the right triangle wins and (0,0) is in the dropped loop.
// In that case the output must still be a valid simple ring — just don't assert
// where index 0 ended up.
const bowtieDrop = untangleIfPolygonSimulation([P(0, 0), P(10, 10), P(10, 0), P(0, 10)], P(10, 0));
ok('dropped-loop case still produces a valid ring',
   u.isSimpleRing(bowtieDrop.points) && bowtieDrop.points.length >= 3);

// Multi-pass convergence: if original points[0] survives in the winner,
// it must be at index 0. If it's in the dropped loop, just assert validity.
const multiOut = untangleIfPolygonSimulation(multi);
const multiFirst = multi[0];
const multiFirstSurvived = multiOut.points.some(
  (p) => near(p.x, multiFirst.x) && near(p.y, multiFirst.y)
);
if (multiFirstSurvived) {
  ok('multi-crossing: original first vertex at index 0 if it survived',
     near(multiOut.points[0].x, multiFirst.x) && near(multiOut.points[0].y, multiFirst.y));
} else {
  ok('multi-crossing: original first vertex in dropped loop — ring is still valid',
     u.isSimpleRing(multiOut.points) && multiOut.points.length >= 3);
}

// No-op: already-simple ring must not be touched at all.
const noopOut = untangleIfPolygonSimulation(square);
ok('no-op ring: changed is false', noopOut.changed === false);
ok('no-op ring: points array is identity-equal', noopOut.points === square);

// --- 10. Open polyline (mid-draw) — closed: false ------------------------
//
// While a polygon is still being drawn, `annotation.points` is an open
// polyline: the dashed line back to points[0] the canvas shows is only a
// preview (draw.js), not a committed edge. `findFirstSelfIntersection` and
// `untangleRing` must not fabricate that closing edge when told `closed:
// false` — the bug this section guards against is exactly that: the "next
// edge" after the last real edge being treated as [points[0], points[n-1]]
// instead of there being no next edge at all.

// A plain zig-zag with no real crossing must not be touched, even though it
// would "close" into a self-intersecting ring if a phantom edge back to
// points[0] were drawn (it would cross the first edge). This is the direct
// regression case for the reported bug.
const zigzagNoRealCrossing = [P(0, 0), P(10, 0), P(0, 5), P(10, 10)];
ok('open zig-zag: closed=true (buggy) wrongly reports a crossing via the phantom edge',
   u.findFirstSelfIntersection(zigzagNoRealCrossing, true) !== null);
ok('open zig-zag: closed=false correctly finds no real crossing',
   u.findFirstSelfIntersection(zigzagNoRealCrossing, false) === null);
ok('open zig-zag: untangleRing(closed=false) leaves it alone',
   u.untangleRing(zigzagNoRealCrossing, null, false).changed === false);

// --- 10a. Open polyline: head wins (annotator drew a big body first) -----
//
// Path: 0:(0,0)->1:(100,0)->2:(50,100)->3:(150,50)->4:(30,60)
// Edge 1->2 crosses edge 3->4 at ~(71.7, 56.5).
// head = [P(0,0), P(100,0), P~71.7]  len=3, area≈2826  ← most area
// loop = [P~71.7,  P(50,100), P(150,50)]  len=3, area≈1630
// tail = [P~71.7,  P(30,60)]          len=2, area=0
// head ties loop on length, but has more area — head wins.
const headWinsPath = [P(0, 0), P(100, 0), P(50, 100), P(150, 50), P(30, 60)];
const headWinsHit = u.findFirstSelfIntersection(headWinsPath, false);
ok('head-wins: crossing detected', headWinsHit !== null);
const headWinsFixed = u.untangleRing(headWinsPath, P(30, 60), false);
ok('head-wins: resolves to a simple open path', u.isSimpleRing(headWinsFixed.points, false));
ok('head-wins: the big body (head) is kept — points[0] survives',
   hasVertex(headWinsFixed.points, 0, 0));
ok('head-wins: the big body (head) is kept — points[1] survives',
   hasVertex(headWinsFixed.points, 100, 0));
ok('head-wins: the stray vertex (30,60) is dropped',
   !hasVertex(headWinsFixed.points, 30, 60));
ok('head-wins: the loop vertex (50,100) is dropped',
   !hasVertex(headWinsFixed.points, 50, 100));
ok('head-wins: crossing point is the new last vertex',
   headWinsHit && near(headWinsFixed.points[headWinsFixed.points.length - 1].x, headWinsHit.point.x) &&
   near(headWinsFixed.points[headWinsFixed.points.length - 1].y, headWinsHit.point.y));

// --- 10b. Open polyline: tail wins (annotator drew a big body after the crossing) ---
//
// Path: 0:(50,0)->1:(50,100)->2:(0,50)->3:(100,50)->4:(100,200)->5:(0,200)->6:(0,150)
// Edge 0->1 (x=50) crosses edge 2->3 (y=50) at (50,50).
// head = [P(50,0), P(50,50)]                                        len=2, area=0
// loop = [P(50,50), P(50,100), P(0,50)]                             len=3, area=1250
// tail = [P(50,50), P(100,50), P(100,200), P(0,200), P(0,150)]     len=5, area=12500 ← most
// tail has the most vertices — tail wins.
const tailWinsPath = [P(50,0), P(50,100), P(0,50), P(100,50), P(100,200), P(0,200), P(0,150)];
const tailWinsHit = u.findFirstSelfIntersection(tailWinsPath, false);
ok('tail-wins: crossing detected', tailWinsHit !== null);
const tailWinsFixed = u.untangleRing(tailWinsPath, tailWinsPath[tailWinsPath.length - 1], false);
ok('tail-wins: resolves to a simple open path', u.isSimpleRing(tailWinsFixed.points, false));
ok('tail-wins: the large body (tail) is kept — P(100,200) survives',
   hasVertex(tailWinsFixed.points, 100, 200));
ok('tail-wins: the large body (tail) is kept — P(0,200) survives',
   hasVertex(tailWinsFixed.points, 0, 200));
ok('tail-wins: the tiny head vertex P(50,0) is dropped',
   !hasVertex(tailWinsFixed.points, 50, 0));
ok('tail-wins: the loop vertex P(50,100) is dropped',
   !hasVertex(tailWinsFixed.points, 50, 100));
ok('tail-wins: crossing point is the new first vertex',
   tailWinsHit &&
   near(tailWinsFixed.points[0].x, tailWinsHit.point.x) &&
   near(tailWinsFixed.points[0].y, tailWinsHit.point.y));

// --- 10c. Open polyline: body wins (fish / nearly-complete body, i=0 crossing) ---
//
// The annotator has drawn most of a closed shape (7-point oval body) and the
// last two clicks cross the very first edge (i=0). When i=0 a fourth candidate
// is considered: body = points[0..j] + P — the whole path from start through
// j, ending at P. body has one more vertex than loop and always preserves
// points[0], so it beats loop; tail (len=3) is smaller than body (len=8).
//
//   head = [P(0,0), P_cross]                                  len=2, area=0
//   loop = [P_cross, P(100,0)..P(0,50)]                       len=7, area≈30179
//   tail = [P_cross, P(130,-20), P(170,40)]                   len=3, area≈1514
//   body = [P(0,0), P(100,0)..P(0,50), P_cross]              len=8  ← wins
//
// body wins; points[0] is preserved as first vertex, P is the new last vertex.
const fishPath = [
  P(0, 0), P(100, 0), P(200, 50), P(200, 150), P(100, 200),
  P(0, 150), P(0, 50), P(130, -20), P(170, 40),
];
const fishHit = u.findFirstSelfIntersection(fishPath, false);
ok('body-wins (fish): crossing detected', fishHit !== null);
const fishFixed = u.untangleRing(fishPath, fishPath[fishPath.length - 1], false);
ok('body-wins (fish): resolves to a simple open path', u.isSimpleRing(fishFixed.points, false));
ok('body-wins (fish): body vertex P(200,50) survives',
   hasVertex(fishFixed.points, 200, 50));
ok('body-wins (fish): body vertex P(100,200) survives',
   hasVertex(fishFixed.points, 100, 200));
ok('body-wins (fish): body vertex P(0,150) survives',
   hasVertex(fishFixed.points, 0, 150));
ok('body-wins (fish): stray approach P(130,-20) is dropped',
   !hasVertex(fishFixed.points, 130, -20));
ok('body-wins (fish): stray approach P(170,40) is dropped',
   !hasVertex(fishFixed.points, 170, 40));
ok('body-wins (fish): points[0] is dropped (it was on the crossed edge)',
   !hasVertex(fishFixed.points, 0, 0));
ok('body-wins (fish): points[1] (100,0) is now the first vertex',
   near(fishFixed.points[0].x, 100) && near(fishFixed.points[0].y, 0));
ok('body-wins (fish): crossing point is the new last vertex',
   fishHit && near(fishFixed.points[fishFixed.points.length - 1].x, fishHit.point.x) &&
   near(fishFixed.points[fishFixed.points.length - 1].y, fishHit.point.y));

// --- 10d. Open polyline: S-shape crossing back near start (i=0, body wins) ---
//
// Annotator draws a full S-curve body (5 points) then the last click crosses
// back across edge 0->1. body = points[0..j] + P wins over loop and tail.
// points[0] stays as first vertex; P becomes the new last vertex.
const sPath = [P(100, 0), P(200, 0), P(200, 100), P(0, 100), P(0, 50), P(150, -20)];
const sHit = u.findFirstSelfIntersection(sPath, false);
ok('S-shape: crossing on edge 0->1 detected', sHit !== null);
const sFixed = u.untangleRing(sPath, sPath[sPath.length - 1], false);
ok('S-shape: resolves to a simple open path', u.isSimpleRing(sFixed.points, false));
ok('S-shape: points[0] (100,0) dropped (it was on the crossed edge)',
   !hasVertex(sFixed.points, 100, 0));
ok('S-shape: points[1] (200,0) is now the first vertex',
   near(sFixed.points[0].x, 200) && near(sFixed.points[0].y, 0));
ok('S-shape: full body P(200,100) kept', hasVertex(sFixed.points, 200, 100));
ok('S-shape: full body P(0,100) kept', hasVertex(sFixed.points, 0, 100));
ok('S-shape: stray (150,-20) dropped', !hasVertex(sFixed.points, 150, -20));
ok('S-shape: crossing point is new last vertex',
   sHit && near(sFixed.points[sFixed.points.length - 1].x, sHit.point.x) &&
   near(sFixed.points[sFixed.points.length - 1].y, sHit.point.y));

// Adjacent-edge and short-input guards must hold for the open case too.
ok('open: three points never change', u.untangleRing([P(0, 0), P(5, 0), P(0, 5)], null, false).changed === false);
ok('open: a simple polyline is untouched',
   u.untangleRing([P(0, 0), P(10, 0), P(10, 10), P(20, 10)], null, false).changed === false);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

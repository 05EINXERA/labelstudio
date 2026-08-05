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

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

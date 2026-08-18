/**
 * Behaviour spec for the polygon union kernel behind "merge objects".
 *
 * Run: node tests/js/merge_spec.mjs  (or via tests/test_merge_objects.py)
 *
 * `frontend/js/canvas/merge.js` destroys N annotations to create 1, so a wrong
 * union is unrecoverable except by undo — worse than the untangle case, which
 * at least only reshapes one label. The two things worth guarding are that it
 * produces the *right* ring when it acts, and that it **refuses** rather than
 * guessing whenever the geometry is ambiguous. Every case below that asserts
 * `null` is guarding a refusal, and those matter more than the happy paths.
 */
const url = new URL('../../frontend/js/canvas/merge.js', import.meta.url);
const m = await import(url);
const u = await import(new URL('../../frontend/js/canvas/untangle.js', import.meta.url));

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

const P = (x, y) => ({ x, y });
const near = (a, b, tol = 1e-6) => Math.abs(a - b) < tol;
const hasVertex = (pts, x, y, tol = 1e-6) =>
  pts.some((p) => near(p.x, x, tol) && near(p.y, y, tol));

// A square with corner (x, y) and the given side length.
const square = (x, y, side) => [P(x, y), P(x + side, y), P(x + side, y + side), P(x, y + side)];

// --- 1. The basic overlap: two squares crossing at two points -----------

const A = square(0, 0, 10);          // area 100
const B = square(5, 5, 10);          // area 100, overlapping A by 5x5 = 25
const venn = m.unionRings(A, B);

ok('overlapping rings produce a ring', Array.isArray(venn) && venn.length >= 3);
ok('union is a simple ring', u.isSimpleRing(venn));
ok('union area is inputs minus the overlap', near(u.ringArea(venn), 175));
ok('union area exceeds either input', u.ringArea(venn) > u.ringArea(A) &&
   u.ringArea(venn) > u.ringArea(B));
ok('union area is below the sum of inputs',
   u.ringArea(venn) < u.ringArea(A) + u.ringArea(B));
ok('union contains the first crossing point', hasVertex(venn, 10, 5));
ok('union contains the second crossing point', hasVertex(venn, 5, 10));
ok('union keeps the outer corner of A', hasVertex(venn, 0, 0));
ok('union keeps the outer corner of B', hasVertex(venn, 15, 15));
ok('union drops the corner buried inside B', !hasVertex(venn, 10, 10));
ok('union drops the corner buried inside A', !hasVertex(venn, 5, 5));

// The overlapped interior must not be traced: 8 vertices, not more.
ok('union has exactly the expected vertex count', venn.length === 8);

// --- 2. Criss-cross: several crossings on a single edge (PLAN 5.4) ------
//
// A comb of three teeth laid across a horizontal bar. The bar's top edge is
// crossed six times, all on one edge — the case that fails if crossings are
// spliced in without sorting by their edge parameter.

const bar = [P(0, 4), P(30, 4), P(30, 8), P(0, 8)];
const comb = [
  P(2, 0), P(6, 0), P(6, 6), P(10, 6), P(10, 0), P(14, 0),
  P(14, 6), P(18, 6), P(18, 0), P(22, 0), P(22, 6), P(2, 6)
];
const combined = m.unionRings(bar, comb);

ok('comb over bar merges', Array.isArray(combined) && combined.length >= 3);
ok('comb over bar is simple', u.isSimpleRing(combined));
ok('comb over bar covers more than the bar alone', u.ringArea(combined) > u.ringArea(bar));
ok('comb over bar covers more than the comb alone', u.ringArea(combined) > u.ringArea(comb));
ok('comb over bar keeps the bar right end', hasVertex(combined, 30, 4) && hasVertex(combined, 30, 8));

// Multiple crossings on one edge, verified directly: the vertical tooth edges
// each cross the bar's edges, so there are strictly more than two crossings.
const teeth = m.unionRings(bar, comb);
ok('multi-crossing union is not a two-point case', teeth.length > 8);

// --- 3. Containment (PLAN 5.7) ------------------------------------------

const outer = square(0, 0, 20);
const inner = square(5, 5, 5);
const contained = m.unionRings(outer, inner);

ok('containment returns a ring', Array.isArray(contained));
ok('containment yields the container area', near(u.ringArea(contained), u.ringArea(outer)));
ok('containment yields the container vertex count', contained.length === outer.length);
ok('containment is order-independent',
   near(u.ringArea(m.unionRings(inner, outer)), u.ringArea(outer)));
ok('containment counts as overlap', m.ringsOverlap(outer, inner));

// --- 4. Touching only, no proper crossing (PLAN 5.2) --------------------
//
// Decision recorded in the plan: shapes sharing only an edge or a vertex are
// NOT mergeable. Their union is two shapes joined by a seam of zero width, and
// which way the walk turns there depends entirely on rounding.

const left = square(0, 0, 10);
const right = square(10, 0, 10);       // shares the entire edge x=10
ok('edge-touching rings do not count as overlapping', m.ringsOverlap(left, right) === false);
ok('edge-touching rings refuse to merge', m.unionRings(left, right) === null);

const corner = square(10, 10, 10);     // shares only the vertex (10,10)
ok('corner-touching rings do not count as overlapping', m.ringsOverlap(left, corner) === false);
ok('corner-touching rings refuse to merge', m.unionRings(left, corner) === null);

// --- 5. Disjoint (PLAN 5.5) ---------------------------------------------

const far = square(100, 100, 10);
ok('disjoint rings do not overlap', m.ringsOverlap(A, far) === false);
ok('disjoint rings refuse to merge', m.unionRings(A, far) === null);

const disjointFold = m.unionAll([A, far]);
ok('unionAll reports the disjoint input as skipped', disjointFold.skipped.length === 1);
ok('unionAll skipped index points at the disjoint ring', disjointFold.skipped[0] === 1);
ok('unionAll merged only the accumulator', disjointFold.merged === 1);

// --- 6. N = 3 (PLAN 4.1) ------------------------------------------------

const C = square(10, 10, 10);          // overlaps B, not A
const chain = m.unionAll([A, B, C]);
ok('three chained rings all merge', chain.merged === 3);
ok('three chained rings skip nothing', chain.skipped.length === 0);
ok('three chained rings give a simple ring', u.isSimpleRing(chain.points));
ok('three chained rings exceed any one input', u.ringArea(chain.points) > u.ringArea(B));

// A and C touch only through B, so this also proves the repeated-pass fold:
// a naive single left-to-right pass would strand one of them.
ok('chained fold covers the full extent',
   hasVertex(chain.points, 0, 0) && hasVertex(chain.points, 20, 20));

// N=3 where one is disjoint (PLAN 5.6)
const partial = m.unionAll([A, B, far]);
ok('a disjoint member is reported', partial.skipped.length === 1);
ok('the other two still merged', partial.merged === 2);

// --- 7. Winding order (PLAN: opposite winding must not matter) ----------

const clockwise = B.slice().reverse();
const mixedWinding = m.unionRings(A, clockwise);
ok('opposite winding still merges', Array.isArray(mixedWinding));
ok('opposite winding gives the same area', near(u.ringArea(mixedWinding), u.ringArea(venn)));
ok('opposite winding gives a simple ring', u.isSimpleRing(mixedWinding));

ok('ringOrientation reads counter-clockwise as +1', m.ringOrientation(A) === 1);
ok('ringOrientation reads clockwise as -1', m.ringOrientation(A.slice().reverse()) === -1);
ok('ringOrientation reads a degenerate ring as 0', m.ringOrientation([P(0, 0), P(1, 1)]) === 0);
ok('ensureCCW leaves a CCW ring alone', m.ringOrientation(m.ensureCCW(A)) === 1);
ok('ensureCCW flips a CW ring', m.ringOrientation(m.ensureCCW(A.slice().reverse())) === 1);
ok('ensureCCW does not mutate its input',
   (() => { const cw = A.slice().reverse(); const before = cw[0].x; m.ensureCCW(cw); return cw[0].x === before; })());

// --- 8. Degenerate crossings on integer coordinates (PLAN 5.3) ----------
//
// Coordinates are integer-snapped on every write, so a crossing landing exactly
// on a vertex is an ordinary case. The kernel must refuse rather than emit a
// ring that is subtly wrong.

const vertexTouch = [P(10, 0), P(20, 0), P(20, 10), P(10, 10)];  // left edge on A's right edge
ok('a ring meeting exactly along an edge refuses', m.unionRings(A, vertexTouch) === null);

// A vertex of one landing exactly on an edge of the other, no proper crossing.
const vertexOnEdge = [P(10, 5), P(20, 2), P(20, 8)];
const vertexResult = m.unionRings(A, vertexOnEdge);
ok('a vertex exactly on an edge either refuses or stays simple',
   vertexResult === null || u.isSimpleRing(vertexResult));

// --- 9. Degenerate inputs never throw -----------------------------------

const degenerate = [null, undefined, [], [P(0, 0)], [P(0, 0), P(1, 1)],
                    [P(0, 0), P(1, 0), P(0, 1)]];
let threw = false;
for (const bad of degenerate) {
  try {
    m.unionRings(bad, A);
    m.unionRings(A, bad);
    m.ringsOverlap(bad, A);
    m.ringOrientation(bad);
    m.ensureCCW(bad);
    m.unionAll([bad, A]);
  } catch (err) {
    threw = true;
  }
}
ok('degenerate inputs never throw', threw === false);
ok('an empty ring cannot merge', m.unionRings([], A) === null);
ok('a two-point ring cannot merge', m.unionRings([P(0, 0), P(1, 1)], A) === null);
ok('a null ring cannot merge', m.unionRings(null, A) === null);
ok('unionAll of nothing reports nothing merged', m.unionAll([]).merged === 0);
ok('unionAll of nothing has null points', m.unionAll([]).points === null);
ok('unionAll of one ring returns it', near(u.ringArea(m.unionAll([A]).points), 100));
ok('unionAll ignores a non-array argument', m.unionAll(null).merged === 0);
// A triangle is a perfectly legal input — 3 points is the minimum real ring.
// Offset from the origin on purpose: a triangle at (0,0) would share a corner
// and run collinear along both axes with A, making it a touching case that
// section 4 already covers as a refusal.
const triangle = [P(2, 2), P(20, 2), P(2, 20)];
const triangleUnion = m.unionRings(triangle, A);
ok('a triangle is a legal input', Array.isArray(triangleUnion));
ok('a triangle union is simple', u.isSimpleRing(triangleUnion));
ok('a triangle union exceeds the square', u.ringArea(triangleUnion) > u.ringArea(A));

// --- 10. Idempotence ----------------------------------------------------
//
// Merging a merged shape with one of its own inputs must add nothing: the
// input is already contained, so the result is the merged shape again.

const again = m.unionRings(venn, A);
ok('re-merging a former input changes nothing', near(u.ringArea(again), u.ringArea(venn)));
ok('re-merging stays simple', u.isSimpleRing(again));
ok('re-merging is stable a second time',
   near(u.ringArea(m.unionRings(again, A)), u.ringArea(venn)));

// --- 11. The invariant, asserted across every ring produced above -------
//
// The union of simple rings is a simple ring. Anything else means the walk
// went wrong, and the caller would write corrupt geometry into an annotation.

const produced = [venn, combined, contained, chain.points, mixedWinding, again,
                  partial.points, teeth, triangleUnion];
ok('every produced ring is simple', produced.every((r) => r === null || u.isSimpleRing(r)));
ok('every produced ring has at least 3 vertices',
   produced.every((r) => r === null || r.length >= 3));
ok('every produced ring has finite coordinates',
   produced.every((r) => r === null ||
     r.every((p) => Number.isFinite(p.x) && Number.isFinite(p.y))));
ok('every produced ring has positive area',
   produced.every((r) => r === null || u.ringArea(r) > 0));
ok('no produced ring repeats its first point at the end',
   produced.every((r) => r === null || r.length < 2 ||
     !(near(r[0].x, r[r.length - 1].x) && near(r[0].y, r[r.length - 1].y))));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

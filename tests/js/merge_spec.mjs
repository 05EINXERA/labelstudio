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

// Ray cast, for asking whether a merged piece covers a point the annotator
// never marked. Boundary behaviour is irrelevant here: every point tested is
// well inside or well outside.
const pointInside = (point, ring) => {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
    const c = ring[i];
    const d = ring[j];
    if ((c.y > point.y) !== (d.y > point.y) &&
        point.x < ((d.x - c.x) * (point.y - c.y)) / (d.y - c.y) + c.x) {
      inside = !inside;
    }
  }
  return inside;
};

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
ok('three chained rings give one component', chain.components.length === 1);
ok('three chained rings give a simple ring', u.isSimpleRing(chain.components[0]));
ok('three chained rings exceed any one input',
   u.ringArea(chain.components[0]) > u.ringArea(B));

// A and C touch only through B, so this also proves the repeated-pass fold:
// a naive single left-to-right pass would strand one of them.
ok('chained fold covers the full extent',
   hasVertex(chain.components[0], 0, 0) && hasVertex(chain.components[0], 20, 20));

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
ok('unionAll of nothing has null components', m.unionAll([]).components === null);
ok('unionAll of one ring returns it',
   near(u.ringArea(m.unionAll([A]).components[0]), 100));
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

// --- 10b. The reported bug: two overlaps must not swallow the gap -------
//
// A horizontal bar crossing the two legs of an inverted-U. The outlines cross
// four times, the overlap happens in two separate lobes, and the untouched
// space between the legs sits enclosed between them. The original walk started
// at one outside vertex, traced back to it, and returned that single ring —
// which is the outer boundary with the gap filled in. It passed every guard
// (simple, above the area floor, larger than either input), so a merge silently
// annotated image the user never marked.

const legs = [P(10, 0), P(50, 0), P(50, 50), P(38, 50),
              P(38, 10), P(22, 10), P(22, 50), P(10, 50)];
const crossbar = [P(0, 20), P(60, 20), P(60, 30), P(0, 30)];
const twoLobes = m.unionComponents(crossbar, legs);

ok('two-lobe overlap yields components', twoLobes !== null);
ok('two-lobe overlap has one outer ring', twoLobes.outer.length === 1);
ok('two-lobe overlap reports the enclosed gap as a hole', twoLobes.holes.length === 1);
ok('the hole is the gap between the legs', near(u.ringArea(twoLobes.holes[0]), 160));
ok('every reported ring is simple',
   [...twoLobes.outer, ...twoLobes.holes].every((r) => u.isSimpleRing(r)));

// The point that proves the bug is gone. (30, 15) is dead centre of the gap:
// inside the outer boundary, but inside neither input and inside no part of the
// union. Anything that reports it as covered has re-introduced the defect.
const gapPoint = P(30, 15);
ok('the gap is enclosed by the outer boundary, not part of it',
   m.unionRings(crossbar, legs) === null);
ok('the hole contains the gap centre',
   twoLobes.holes.some((r) => pointInside(gapPoint, r)));

// unionRings can only answer with one ring, so it must refuse here rather than
// hand back the outer boundary. That refusal is the fix at the kernel level.
ok('a holed union has no single-ring answer', m.unionRings(crossbar, legs) === null);
ok('a holed union is refused by unionAll and flagged',
   m.unionAll([crossbar, legs]).holes === true);

// ... and the refusal must not be mistaken for "these do not overlap", which
// would send the caller down the wrong message path.
ok('a holed union is not reported as non-overlapping',
   m.unionAll([crossbar, legs]).skipped.length === 0);
ok('the rings genuinely do overlap', m.ringsOverlap(crossbar, legs) === true);

// The same pair the other way round must reach the same conclusion.
const flipped = m.unionComponents(legs, crossbar);
ok('hole detection is order-independent',
   flipped !== null && flipped.holes.length === 1 && flipped.outer.length === 1);
ok('the hole is the same either way', near(u.ringArea(flipped.holes[0]), 160));

// A C-shape crossed at both tips is the same topology drawn differently.
const cShape = [P(0, 0), P(40, 0), P(40, 12), P(10, 12),
                P(10, 28), P(40, 28), P(40, 40), P(0, 40)];
const capBar = [P(35, -5), P(45, -5), P(45, 45), P(35, 45)];
const capped = m.unionComponents(cShape, capBar);
ok('a capped C also reports a hole', capped !== null && capped.holes.length === 1);
ok('a capped C has a single outer ring', capped.outer.length === 1);

// --- 10c. A plain two-crossing overlap still gives exactly one ring -----
//
// The regression fence for the rewrite: enumerating components must not turn
// the ordinary case into several pieces or a spurious hole.

const plain = m.unionComponents(A, B);
ok('a simple overlap has one outer component', plain.outer.length === 1);
ok('a simple overlap has no hole', plain.holes.length === 0);
ok('the single component is the ring unionRings returns',
   near(u.ringArea(plain.outer[0]), u.ringArea(venn)));
ok('a simple overlap still merges through unionAll',
   m.unionAll([A, B]).components.length === 1);
ok('a simple overlap flags no hole', m.unionAll([A, B]).holes === false);

// Containment and the comb both stay single-component too.
ok('containment is one component',
   m.unionComponents(square(0, 0, 100), A).outer.length === 1);
ok('containment has no hole',
   m.unionComponents(square(0, 0, 100), A).holes.length === 0);
ok('the comb over the bar is one component',
   m.unionComponents(bar, comb).outer.length === 1);
ok('the comb over the bar has no hole',
   m.unionComponents(bar, comb).holes.length === 0);

// --- 11. The invariant, asserted across every ring produced above -------
//
// The union of simple rings is a simple ring. Anything else means the walk
// went wrong, and the caller would write corrupt geometry into an annotation.

const produced = [venn, combined, contained, chain.components[0], mixedWinding,
                  again, partial.components[0], teeth, triangleUnion];
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

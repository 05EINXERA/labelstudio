/**
 * Regression spec for the polygon edge-highlight index guards in
 * `frontend/js/canvas/draw.js`.
 *
 * Run: node tests/js/edge_highlight_spec.mjs
 *
 * The bug: drawAnnotation() highlighted a hovered polygon edge with
 *
 *     if (view.hoveredLineIndex !== -1 && view.hoveredLineIndex !== view.selectedLineIndex)
 *       const p1 = screenPoints[view.hoveredLineIndex];   // undefined
 *
 * — no bounds check, unlike the selected-edge branch right below it, which had
 * always carried `< screenPoints.length`. `hoveredLineIndex` is measured against
 * whichever shape the cursor was last over and outlives it: right-clicking a
 * different annotation (context-menu.js changes the selection and re-renders),
 * or deleting points mid-hover, leaves the index pointing past the end of the
 * newly drawn shape's points. The result was a hard render crash —
 * "Cannot read properties of undefined (reading 'x')" — which kills the whole
 * canvas frame, not just the highlight.
 *
 * draw.js imports the canvas DOM modules, so rather than shim a canvas this
 * spec mirrors the guard predicate and asserts the property that matters: for
 * every index the rest of the app can produce, the guard admits an index only
 * when both endpoints it will dereference actually exist. A drift in the real
 * condition is caught by the source assertions at the bottom.
 */
import { readFileSync } from 'node:fs';

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

// The two guards exactly as draw.js states them.
const hoveredGuard = (view, screenPoints) =>
  view.hoveredLineIndex !== -1 &&
  view.hoveredLineIndex !== view.selectedLineIndex &&
  view.hoveredLineIndex < screenPoints.length;

const selectedGuard = (view, screenPoints) =>
  view.selectedLineIndex !== -1 &&
  view.selectedLineIndex < screenPoints.length;

// What the guarded body then dereferences (draw.js:266-267 / 279-280).
const endpoints = (index, screenPoints) => [
  screenPoints[index],
  screenPoints[(index + 1) % screenPoints.length]
];

const shape = (n) => Array.from({ length: n }, (_, i) => ({ x: i, y: i }));

const box = shape(4);          // 4 points — a bounding box
const poly = shape(12);        // the 12-point polygon the hover was measured on
const triangle = shape(3);

// 1. The reported crash: hover edge 7 of a 12-gon, then right-click a box.
console.log('\nstale index from a larger shape');
{
  const view = { hoveredLineIndex: 7, selectedLineIndex: -1 };
  ok('index 7 is valid on the 12-gon it was measured on', hoveredGuard(view, poly));
  ok('guard rejects index 7 against a 4-point box', !hoveredGuard(view, box));
  ok('guard rejects index 7 against a triangle', !hoveredGuard(view, triangle));
  // The precise failure mode of the old code.
  const [p1] = endpoints(view.hoveredLineIndex, box);
  ok('unguarded, that index really is undefined', p1 === undefined);
}

// 2. Points deleted while an edge is hovered shrinks the same annotation.
console.log('\nshape shrinks under a live hover');
{
  const view = { hoveredLineIndex: 5, selectedLineIndex: -1 };
  ok('valid at 6 points', hoveredGuard(view, shape(6)));
  ok('rejected once the shape drops to 5 points', !hoveredGuard(view, shape(5)));
  ok('rejected at 3 points', !hoveredGuard(view, shape(3)));
}

// 3. Exhaustive: no admitted index ever dereferences undefined. This is the
//    property the fix exists to establish.
console.log('\nadmitted indices always dereference');
{
  let bad = 0, admitted = 0;
  for (const n of [0, 1, 2, 3, 4, 5, 8, 12]) {
    const pts = shape(n);
    for (let hovered = -1; hovered < 20; hovered++) {
      for (let selected = -1; selected < 20; selected++) {
        const view = { hoveredLineIndex: hovered, selectedLineIndex: selected };
        if (hoveredGuard(view, pts)) {
          admitted++;
          const [p1, p2] = endpoints(hovered, pts);
          if (!p1 || !p2) bad++;
        }
        if (selectedGuard(view, pts)) {
          admitted++;
          const [p1, p2] = endpoints(selected, pts);
          if (!p1 || !p2) bad++;
        }
      }
    }
  }
  ok('the sweep actually admitted indices', admitted > 0);
  ok('no admitted index yields an undefined endpoint', bad === 0);
}

// 4. The guard must not over-reject: real hovers still highlight.
console.log('\nvalid hovers still draw');
{
  ok('first edge of a box', hoveredGuard({ hoveredLineIndex: 0, selectedLineIndex: -1 }, box));
  ok('last edge of a box wraps to point 0',
    hoveredGuard({ hoveredLineIndex: 3, selectedLineIndex: -1 }, box) &&
    endpoints(3, box)[1] === box[0]);
  ok('last edge of the 12-gon', hoveredGuard({ hoveredLineIndex: 11, selectedLineIndex: -1 }, poly));
  ok('no hover is not drawn', !hoveredGuard({ hoveredLineIndex: -1, selectedLineIndex: -1 }, poly));
  ok('hovered edge equal to the selected edge defers to the selected style',
    !hoveredGuard({ hoveredLineIndex: 2, selectedLineIndex: 2 }, poly));
}

// 5. Drift guard: the real source must keep both bounds checks. If someone
//    removes one, the mirrored predicates above stop describing the shipped
//    code and every assertion here becomes meaningless.
console.log('\ndraw.js still carries both bounds checks');
{
  const src = readFileSync(new URL('../../frontend/js/canvas/draw.js', import.meta.url), 'utf8');
  const normalised = src.replace(/\s+/g, ' ');
  ok('hovered branch bounds-checks against screenPoints.length',
    normalised.includes('view.hoveredLineIndex < screenPoints.length'));
  ok('selected branch bounds-checks against screenPoints.length',
    normalised.includes('view.selectedLineIndex < screenPoints.length'));
}

// 6. The source of the stale index: context-menu.js changes the selection and
//    re-renders, so it must clear both indices first.
console.log('\ncontext-menu.js clears edge state before re-rendering');
{
  const src = readFileSync(new URL('../../frontend/js/canvas/context-menu.js', import.meta.url), 'utf8');
  const normalised = src.replace(/\s+/g, ' ');
  ok('clears hoveredLineIndex', normalised.includes('view.hoveredLineIndex = -1'));
  ok('clears selectedLineIndex', normalised.includes('view.selectedLineIndex = -1'));
  // Ordering matters: clearing after render() would still draw one bad frame.
  const clearAt = normalised.indexOf('view.hoveredLineIndex = -1');
  const renderAt = normalised.indexOf('render()', clearAt);
  ok('clears before it calls render()', clearAt !== -1 && renderAt > clearAt);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);

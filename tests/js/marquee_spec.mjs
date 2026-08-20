/**
 * Behaviour spec for marquee (Shift + left-drag) selection.
 *
 * Run: node tests/js/marquee_spec.mjs
 *
 * `frontend/js/canvas/marquee.js` decides which annotations a dragged
 * rectangle selects. Two things are guarded here:
 *
 *  1. The hit rule — intersect rather than contain, and the three polygon
 *     cases (vertex inside, rect corner inside, edge crossing) that between
 *     them stop a box dragged across a large shape from selecting nothing.
 *
 *  2. **That the rule cannot reach the saved annotation set.** The module is
 *     pure precisely so this is cheap to assert: a selection gesture that
 *     mutated `state.annotations` would let a user drag a box and then save
 *     the damage (.devnotes/drag-selection/01_DESIGN.md § 4.4).
 *
 * The module imports only the pure `geometry.js`, so no DOM shim is needed.
 */
const url = new URL('../../frontend/js/canvas/marquee.js', import.meta.url);
const { normalizeRect, rectIsDegenerate, annotationIntersectsRect, marqueeHits } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

const box = (id, x, y, w, h) => ({
  id, type: 'bbox', x, y, width: w, height: h,
  points: [{ x, y }, { x: x + w, y }, { x: x + w, y: y + h }, { x, y: y + h }]
});

const poly = (id, points) => {
  const xs = points.map((p) => p.x), ys = points.map((p) => p.y);
  return {
    id, type: 'polygon', points,
    x: Math.min(...xs), y: Math.min(...ys),
    width: Math.max(...xs) - Math.min(...xs), height: Math.max(...ys) - Math.min(...ys)
  };
};

const rect = (x1, y1, x2, y2) => ({ x1, y1, x2, y2 });

// 1. Rect normalisation — the drag direction must not matter.
console.log('\nnormalizeRect');
{
  const target = rect(10, 20, 50, 60);
  const directions = [
    [{ x: 10, y: 20 }, { x: 50, y: 60 }],   // top-left -> bottom-right
    [{ x: 50, y: 60 }, { x: 10, y: 20 }],   // bottom-right -> top-left
    [{ x: 50, y: 20 }, { x: 10, y: 60 }],   // top-right -> bottom-left
    [{ x: 10, y: 60 }, { x: 50, y: 20 }]    // bottom-left -> top-right
  ];
  ok('every drag direction yields the same rect', directions.every(
    ([a, b]) => JSON.stringify(normalizeRect(a, b)) === JSON.stringify(target)
  ));
}

// 2. Degenerate rects — a twitch during a Shift+click must not count as a drag.
console.log('\nrectIsDegenerate');
{
  ok('a 1px box at scale 1 is degenerate', rectIsDegenerate(rect(10, 10, 11, 11), 1));
  ok('a 40px box at scale 1 is not', !rectIsDegenerate(rect(10, 10, 50, 50), 1));
  ok('a null rect is degenerate', rectIsDegenerate(null, 1));
  ok('a thin but long box is a real drag', !rectIsDegenerate(rect(10, 10, 200, 11), 1));
  // Zoomed in 10x, 1 image px is 10 screen px — a deliberate drag.
  ok('the threshold is screen px, not image px', !rectIsDegenerate(rect(10, 10, 11, 11), 10));
  // Zoomed out, 2 image px is well under 3 screen px.
  ok('zoomed out, a small image-space box is still a twitch', rectIsDegenerate(rect(10, 10, 12, 12), 0.1));
}

// 3. Boxes — the AABB path.
console.log('\nbbox hits');
{
  const marquee = rect(100, 100, 200, 200);
  ok('box fully inside is a hit', annotationIntersectsRect(box('b1', 120, 120, 20, 20), marquee));
  ok('box fully outside is a miss', !annotationIntersectsRect(box('b2', 400, 400, 20, 20), marquee));
  ok('box straddling an edge is a hit (intersect, not contain)',
    annotationIntersectsRect(box('b3', 180, 120, 100, 20), marquee));
  ok('box enclosing the whole marquee is a hit',
    annotationIntersectsRect(box('b4', 0, 0, 500, 500), marquee));
  ok('box touching only at a corner is a hit',
    annotationIntersectsRect(box('b5', 200, 200, 50, 50), marquee));
}

// 4. Polygons — the three cases the bbox test alone gets wrong.
console.log('\npolygon hits');
{
  // A big diamond spanning 0..400. Its vertices are far from a small central
  // marquee, and no vertex lands inside it.
  const diamond = poly('p1', [
    { x: 200, y: 0 }, { x: 400, y: 200 }, { x: 200, y: 400 }, { x: 0, y: 200 }
  ]);
  ok('small marquee inside a large polygon is a hit (rect corner in polygon)',
    annotationIntersectsRect(diamond, rect(190, 190, 210, 210)));

  // A horizontal bar crossing a tall thin marquee side to side: no vertex of
  // either shape lies inside the other.
  const bar = poly('p2', [
    { x: 0, y: 100 }, { x: 500, y: 100 }, { x: 500, y: 140 }, { x: 0, y: 140 }
  ]);
  ok('polygon spanning the marquee with no vertex inside is a hit (edge crossing)',
    annotationIntersectsRect(bar, rect(200, 0, 240, 300)));

  ok('polygon vertex inside the marquee is a hit',
    annotationIntersectsRect(diamond, rect(190, -10, 260, 30)));

  // An L: its bounding box covers the top-right quadrant, its geometry does not.
  const ell = poly('p3', [
    { x: 0, y: 0 }, { x: 40, y: 0 }, { x: 40, y: 160 },
    { x: 200, y: 160 }, { x: 200, y: 200 }, { x: 0, y: 200 }
  ]);
  ok('bounding box overlaps but geometry does not is a miss',
    !annotationIntersectsRect(ell, rect(100, 20, 150, 80)));
  ok('the same L is a hit where its arm actually is',
    annotationIntersectsRect(ell, rect(100, 170, 150, 190)));
}

// 5. The whole pass over the annotation list.
console.log('\nmarqueeHits');
{
  const annotations = [
    box('a1', 10, 10, 20, 20),
    box('a2', 100, 100, 20, 20),
    box('a3', 400, 400, 20, 20)
  ];
  const hits = marqueeHits(annotations, rect(0, 0, 150, 150), { isHidden: () => false });
  ok('returns the covered ids in annotation order', hits.join(',') === 'a1,a2');
  ok('an empty region selects nothing',
    marqueeHits(annotations, rect(250, 250, 300, 300), {}).length === 0);
  ok('a non-array yields an empty list', marqueeHits(null, rect(0, 0, 10, 10)).length === 0);
  ok('a missing rect yields an empty list', marqueeHits(annotations, null).length === 0);

  const both = [
    marqueeHits(annotations, normalizeRect({ x: 0, y: 0 }, { x: 150, y: 150 }), {}),
    marqueeHits(annotations, normalizeRect({ x: 150, y: 150 }, { x: 0, y: 0 }), {})
  ];
  ok('drag direction does not change the hit set', both[0].join(',') === both[1].join(','));
}

// 6. Hidden objects are never selectable — the rule hitTest already applies to
//    clicks. Handing back an invisible object to then group, merge or delete
//    is how work disappears without the user seeing what went.
console.log('\nhidden objects');
{
  const annotations = [box('v1', 10, 10, 20, 20), box('h1', 40, 10, 20, 20)];
  const hits = marqueeHits(annotations, rect(0, 0, 200, 200), { isHidden: (a) => a.id === 'h1' });
  ok('a hidden annotation is never returned', hits.join(',') === 'v1');
  ok('with everything hidden, nothing is selected',
    marqueeHits(annotations, rect(0, 0, 200, 200), { isHidden: () => true }).length === 0);
}

// 7. Comments are decided in screen space, from what is actually painted —
//    never from their stored 20x20 box, which nothing draws.
console.log('\ncomments');
{
  const comment = { id: 'c1', type: 'comment', x: 50, y: 50, width: 20, height: 20, text: 'hi' };
  // Screen geometry deliberately far from the stored image-space box, so a
  // rule reading x/y/width/height would give the wrong answer here.
  const geometry = { dot: { cx: 900, cy: 900, r: 8 }, pill: { x: 912, y: 888, width: 60, height: 24 } };

  const covering = marqueeHits([comment], rect(0, 0, 200, 200), {
    commentRect: () => geometry, screenRect: rect(880, 880, 940, 940)
  });
  ok('a comment is selected when the screen rect covers its dot', covering.join(',') === 'c1');

  const missing = marqueeHits([comment], rect(0, 0, 200, 200), {
    commentRect: () => geometry, screenRect: rect(0, 0, 100, 100)
  });
  ok('the stored 20x20 box does not select a comment', missing.length === 0);

  const pillOnly = marqueeHits([comment], rect(0, 0, 200, 200), {
    commentRect: () => geometry, screenRect: rect(950, 890, 960, 900)
  });
  ok('the pill is part of the target', pillOnly.join(',') === 'c1');

  ok('without screen geometry a comment is skipped, not guessed at',
    marqueeHits([comment], rect(0, 0, 200, 200), {}).length === 0);

  const hiddenComment = marqueeHits([comment], rect(0, 0, 200, 200), {
    isHidden: () => true, commentRect: () => geometry, screenRect: rect(880, 880, 940, 940)
  });
  ok('a hidden comment is skipped before its geometry is asked for', hiddenComment.length === 0);
}

// 8. THE important one: the rule must never touch the annotation set.
console.log('\npurity');
{
  const annotations = [
    box('a1', 10, 10, 20, 20),
    poly('p1', [{ x: 200, y: 0 }, { x: 400, y: 200 }, { x: 200, y: 400 }, { x: 0, y: 200 }]),
    { id: 'c1', type: 'comment', x: 50, y: 50, width: 20, height: 20, text: 'hi' }
  ];
  const before = JSON.stringify(annotations);

  const rects = [rect(0, 0, 500, 500), rect(190, 190, 210, 210), rect(-10, -10, 5, 5)];
  for (const r of rects) {
    marqueeHits(annotations, r, {
      isHidden: () => false,
      commentRect: () => ({ dot: { cx: 0, cy: 0, r: 8 }, pill: null }),
      screenRect: r
    });
    annotationIntersectsRect(annotations[1], r);
  }

  ok('leaves every annotation untouched', JSON.stringify(annotations) === before);
  ok('annotation count is unchanged', annotations.length === 3);

  const source = rect(10, 20, 50, 60);
  const copy = JSON.stringify(source);
  marqueeHits(annotations, source, {});
  ok('leaves the rect untouched', JSON.stringify(source) === copy);

  const a = { x: 1, y: 2 }, b = { x: 3, y: 4 };
  normalizeRect(a, b);
  ok('normalizeRect does not mutate its corners',
    JSON.stringify([a, b]) === JSON.stringify([{ x: 1, y: 2 }, { x: 3, y: 4 }]));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

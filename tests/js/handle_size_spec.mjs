/**
 * Behaviour spec for zoom-responsive vertex handle sizing.
 *
 * Run: node tests/js/handle_size_spec.mjs
 *
 * `frontend/js/canvas/handle-size.js` decides how big a vertex handle is drawn
 * at a given zoom. Handles used to be a constant 4.5 screen px at every zoom,
 * which hid the very pixels being annotated at high magnification. They now
 * shrink along a clamped sub-linear curve. Four things are guarded here:
 *
 *  1. **The floor.** The whole point of clamping is that a handle never
 *     shrinks to the point of being lost visually. If the floor stops
 *     applying, handles become sub-pixel at high zoom and vertices are
 *     effectively invisible while still being draggable.
 *
 *  2. **The handle is never drawn larger than its own grab radius.** A handle
 *     wider than `vertexGrabRadius` would be visible but unclickable around
 *     its rim — the user aims at what they can see and nothing happens.
 *
 *  3. **Fit zoom is unchanged.** viewZoom === 1 must still be 4.5px, so this
 *     is not a silent restyle of every screenshot and habit at normal zoom.
 *
 *  4. **No NaN escapes.** A NaN radius makes ctx.arc() throw, which aborts the
 *     overlay draw and blanks every annotation on screen — a bad zoom number
 *     must degrade to a default, never take the canvas down.
 *
 * The module imports only feature-flags.js, which is itself dependency-free,
 * so no DOM shim is needed.
 */
// The `?v=` pin is load-bearing *here*, not just in the browser: node keys its
// module cache on the full specifier, so importing `feature-flags.js` without
// the query string yields a second, unrelated copy of `annotationSettings` —
// one that handle-size.js never reads. Test 6 mutates the flag, so an unpinned
// import would silently assert against the wrong object. The pin must match
// the one handle-size.js itself imports -- so when feature-flags.js is bumped,
// bump it HERE too. A pin sweep over frontend/ alone misses this file and the
// falloff test starts failing for a reason that looks nothing like a pin.
const flagsUrl = new URL('../../frontend/js/feature-flags.js?v=4', import.meta.url);
const sizeUrl = new URL('../../frontend/js/canvas/handle-size.js?v=1', import.meta.url);
const { annotationSettings } = await import(flagsUrl);
const { vertexHandleRadius, vertexHandleLineWidth } = await import(sizeUrl);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};
const near = (a, b) => Math.abs(a - b) < 1e-9;

// setZoom clamps viewZoom to [0.1, 500], so this sweep spans the real range
// plus both endpoints.
const ZOOMS = [0.1, 0.25, 0.5, 1, 2, 4, 6, 10, 50, 100, 500];
const { vertexHandleRadius: BASE, vertexHandleMinRadius: MIN,
        vertexHandleMaxRadius: MAX, vertexGrabRadius: GRAB } = annotationSettings;

// 1. Fit zoom is the documented, unchanged baseline.
console.log('\nfit zoom');
{
  ok('viewZoom 1 gives exactly the configured base radius',
     near(vertexHandleRadius(1), BASE));
}

// 2. The curve only ever shrinks as zoom increases.
console.log('\nmonotonicity');
{
  let monotonic = true;
  for (let i = 1; i < ZOOMS.length; i++) {
    if (vertexHandleRadius(ZOOMS[i]) > vertexHandleRadius(ZOOMS[i - 1])) monotonic = false;
  }
  ok('never grows as zoom increases', monotonic);
  ok('zoomed in is strictly smaller than fit', vertexHandleRadius(4) < vertexHandleRadius(1));
  ok('zoomed out is strictly larger than fit', vertexHandleRadius(0.25) > vertexHandleRadius(1));
}

// 3. Both clamps. The floor is the "not lost visually" guarantee.
console.log('\nclamping');
{
  ok('never below the floor anywhere on the sweep',
     ZOOMS.every((z) => vertexHandleRadius(z) >= MIN));
  ok('never above the ceiling anywhere on the sweep',
     ZOOMS.every((z) => vertexHandleRadius(z) <= MAX));
  ok('sits exactly on the floor at maximum zoom', near(vertexHandleRadius(500), MIN));
  ok('still on the floor far past maximum zoom', near(vertexHandleRadius(1e6), MIN));
  ok('clamped to the ceiling at extreme zoom-out', near(vertexHandleRadius(1e-6), MAX));
}

// 4. The handle must never outgrow the click target that catches it.
console.log('\nhandle versus grab radius');
{
  ok('drawn radius never exceeds vertexGrabRadius',
     ZOOMS.every((z) => vertexHandleRadius(z) <= GRAB));
  // Guards a future edit to either constant, not just the current values.
  ok('configured ceiling itself does not exceed vertexGrabRadius', MAX <= GRAB);
  ok('floor is below the base radius', MIN < BASE);
}

// 5. Degenerate input must degrade to the base size, never to NaN.
console.log('\ndegenerate input');
{
  for (const bad of [0, -1, -0.5, NaN, Infinity, -Infinity, undefined, null, 'abc', {}]) {
    const r = vertexHandleRadius(bad);
    ok(`${String(bad)} yields a finite default radius`, Number.isFinite(r) && near(r, BASE));
  }
  // A numeric string is a real number and should be honoured, not defaulted.
  ok('a numeric string is coerced, not rejected', near(vertexHandleRadius('4'), vertexHandleRadius(4)));
}

// 6. falloff 0 is the documented off switch (previous constant-size behaviour).
console.log('\nfalloff disabled');
{
  const original = annotationSettings.vertexHandleFalloff;
  annotationSettings.vertexHandleFalloff = 0;
  const constant = ZOOMS.every((z) => near(vertexHandleRadius(z), BASE));
  annotationSettings.vertexHandleFalloff = original;
  ok('falloff 0 restores a constant radius at every zoom', constant);
  ok('restoring the flag restores the curve', near(vertexHandleRadius(1), BASE)
     && vertexHandleRadius(4) < BASE);
}

// 7. The ring scales with the disc so the white centre survives.
console.log('\nline width');
{
  ok('is 2px at the base radius', near(vertexHandleLineWidth(BASE), 2));
  ok('never drops below 1px', [MIN, 0.5, 0].every((r) => vertexHandleLineWidth(r) >= 1));
  ok('stays under half the radius, so a white centre remains',
     ZOOMS.every((z) => {
       const r = vertexHandleRadius(z);
       return vertexHandleLineWidth(r) < r;
     }));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

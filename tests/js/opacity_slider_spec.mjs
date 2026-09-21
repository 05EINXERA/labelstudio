/**
 * Behaviour spec for the toolbar Opacity slider's conversion logic.
 *
 * Run: node tests/js/opacity_slider_spec.mjs
 *
 * `frontend/js/opacity-scale.js` maps the slider's 0-100 integer to the
 * 0-1 alpha that draw.js feeds hexToRgba(). Three things are guarded:
 *
 *  1. **The markup/default drift guard.** app.html hardcodes `value="60"` and
 *     a `60%` label as the no-JS fallback; feature-flags.js holds the real
 *     default. Change one without the other and the toolbar lies about the
 *     opacity actually in effect until the first drag. This spec fails first.
 *
 *  2. **No NaN reaches the canvas.** A NaN alpha makes hexToRgba() emit an
 *     invalid colour string, which canvas silently ignores — every selected
 *     shape loses its fill with nothing logged to explain why. Degenerate
 *     input must fall back to the default instead.
 *
 *  3. **0% is genuinely 0.** The whole point of the control is seeing the
 *     pixels under a shape, so a falsy-check bug that treated 0 as "unset"
 *     and substituted the default would defeat the feature at exactly the
 *     value users reach for.
 *
 * Only the pure conversion is covered, which is why it lives in its own
 * module: opacity-controls.js imports draw.js -> dom.js, which touches
 * `document` at load and cannot be imported under bare node. The wiring it
 * holds is verified by hand (plan § 5.4).
 *
 * The `?v=` pins are load-bearing here, not just in the browser: node keys its
 * module cache on the full specifier, so an unpinned import of feature-flags
 * yields a second copy of annotationOpacity that opacity-scale.js never reads
 * — and test 7 would mutate a value nothing under test can see. The pins must
 * match the ones opacity-scale.js itself imports.
 */
const flagsUrl = new URL('../../frontend/js/feature-flags.js?v=3', import.meta.url);
const ctrlUrl = new URL('../../frontend/js/opacity-scale.js?v=1', import.meta.url);
const { annotationOpacity } = await import(flagsUrl);
const { pctToOpacity, opacityToPct, defaultPct } = await import(ctrlUrl);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};
const near = (a, b) => Math.abs(a - b) < 1e-9;

// Mirrors the hardcoded fallback in frontend/app.html. If you change the
// default in feature-flags.js, change it there too — that is what this guards.
const MARKUP_FALLBACK_PCT = 60;

// 1. The default, and its agreement with the markup.
console.log('\ndefault');
{
  ok('defaultPct matches the hardcoded fallback in app.html',
     defaultPct() === MARKUP_FALLBACK_PCT);
  ok('defaultPct is derived from annotationOpacity.selected',
     defaultPct() === Math.round(annotationOpacity.selected * 100));
  ok('the default is a whole percentage', Number.isInteger(defaultPct()));
}

// 2. Conversion both ways.
console.log('\nconversion');
{
  ok('0% -> 0', near(pctToOpacity(0), 0));
  ok('100% -> 1', near(pctToOpacity(100), 1));
  ok('60% -> 0.6', near(pctToOpacity(60), 0.6));
  ok('50% -> 0.5', near(pctToOpacity(50), 0.5));
  ok('0 -> 0%', opacityToPct(0) === 0);
  ok('1 -> 100%', opacityToPct(1) === 100);
  ok('0.6 -> 60%', opacityToPct(0.6) === 60);
  ok('a numeric string is coerced', near(pctToOpacity('40'), 0.4));
}

// 3. Round-trips across the full slider travel.
console.log('\nround-trip');
{
  let stable = true;
  for (let pct = 0; pct <= 100; pct++) {
    if (opacityToPct(pctToOpacity(pct)) !== pct) stable = false;
  }
  ok('every integer percentage survives a round-trip', stable);
}

// 4. Clamping. Out-of-range input is coerced, not rejected — an alpha above 1
//    or below 0 is not a valid canvas value.
console.log('\nclamping');
{
  ok('negative percentage clamps to 0', near(pctToOpacity(-10), 0));
  ok('over-100 percentage clamps to 1', near(pctToOpacity(150), 1));
  ok('negative opacity clamps to 0%', opacityToPct(-0.5) === 0);
  ok('over-1 opacity clamps to 100%', opacityToPct(2) === 100);
  let inRange = true;
  for (const p of [-1e6, -1, 0, 37, 100, 101, 1e6]) {
    const v = pctToOpacity(p);
    if (!(v >= 0 && v <= 1)) inRange = false;
  }
  ok('output is always a valid alpha', inRange);
}

// 5. Degenerate input must not produce NaN — see the header.
console.log('\ndegenerate input');
{
  for (const bad of [NaN, Infinity, -Infinity, undefined, 'abc', {}, []]) {
    const v = pctToOpacity(bad);
    ok(`${String(bad)} yields a finite alpha`,
       Number.isFinite(v) && v >= 0 && v <= 1);
  }
  ok('NaN falls back to the default', near(pctToOpacity(NaN), annotationOpacity.selected));
  ok('opacityToPct(NaN) falls back to the default percent',
     opacityToPct(NaN) === defaultPct());
}

// 6. 0 must not be swallowed by a falsy check.
console.log('\nzero is not falsy');
{
  ok('pctToOpacity(0) is 0, not the default', pctToOpacity(0) === 0);
  ok('opacityToPct(0) is 0, not the default', opacityToPct(0) === 0);
  ok("the string '0' is 0 too", pctToOpacity('0') === 0);
}

// 7. The default is captured at module load, so later mutation of the live
//    value (what the slider does) must not move the fallback.
console.log('\ndefault is captured, not live');
{
  const original = annotationOpacity.selected;
  const before = defaultPct();
  annotationOpacity.selected = 0.05;          // simulate a slider drag
  const after = defaultPct();
  annotationOpacity.selected = original;
  ok('defaultPct is unaffected by a slider edit', before === after);
  ok('and still matches the markup fallback', after === MARKUP_FALLBACK_PCT);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

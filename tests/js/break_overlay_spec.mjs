/**
 * Behaviour spec for the declared-break overlay.
 *
 * Run: node tests/js/break_overlay_spec.mjs
 *      (or via tests/test_frontend_break_overlay.py)
 *
 * The assertion that matters most is that this overlay is **not dismissible**.
 * `modal.js` wires Escape and a backdrop click, which is right for every other
 * modal in the app and wrong for this one: dismissing it leaves a break open
 * with no visible way to end it — the exact failure this feature exists to
 * prevent, reachable by a stray keypress.
 *
 * `break-overlay.js` imports `api.js` and `timer.js`, both of which touch
 * browser globals at import time, so this spec asserts against the module
 * *source* plus the page markup rather than executing it. That is a weaker
 * test than driving the DOM, and worth naming: it catches the dismissal
 * handlers being added back, which is the regression that matters, but it
 * would not catch a behavioural bug inside `endBreak`. Those are covered
 * server-side in tests/test_attendance_api.py.
 */
import { readFileSync } from 'node:fs';

const moduleSrc = readFileSync(
  new URL('../../frontend/js/components/break-overlay.js', import.meta.url), 'utf8'
);
const pageSrc = readFileSync(
  new URL('../../frontend/app.html', import.meta.url), 'utf8'
);
const timerSrc = readFileSync(
  new URL('../../frontend/js/components/timer.js', import.meta.url), 'utf8'
);

/**
 * The module with comments stripped.
 *
 * The comments in break-overlay.js explain at length what it deliberately does
 * NOT do -- they name `createModal`, `Escape` and `style.display` precisely
 * because those are the things to avoid. Asserting against the raw source
 * therefore matches the explanation and reports a failure for prose that is
 * doing its job. These assertions are about code, so they read code.
 */
const code = moduleSrc
  .replace(/\/\*[\s\S]*?\*\//g, '')          // block comments
  // Line comments, including trailing ones. The `[^:]` guard keeps this from
  // eating the `//` inside a URL string such as "/api/attendance/...": a
  // scheme-relative match would truncate the very lines being asserted on.
  .replace(/(^|[^:"'`])\/\/.*$/gm, '$1');

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

// --- Not dismissible --------------------------------------------------------

ok('the overlay does not use createModal, which wires both dismissals',
   !code.includes('createModal'));

ok('no Escape handler is registered',
   !/['"]keydown['"]/.test(code) && !code.includes('Escape'));

ok('no backdrop-click dismissal is registered',
   !/overlay\.addEventListener\(\s*['"]click['"]/.test(code));

ok('the only close path is the End Break button',
   code.includes('endBtn?.addEventListener("click", endBreak)'));

ok('the reason it is not dismissible is written down, not just implemented',
   /strand|stranded|strands/i.test(moduleSrc));

// --- Rule 15: the class, never style.display --------------------------------

ok('the overlay toggles is-active rather than style.display',
   code.includes('classList.add("is-active")') &&
   code.includes('classList.remove("is-active")'));

ok('style.display is never touched',
   !code.includes('style.display'));

// --- Timer coordination (Q15: "both") ---------------------------------------

ok('starting a break pauses the timer',
   code.includes('pauseTimerForBreak()'));

ok('ending a break resumes the timer',
   code.includes('resumeTimerAfterBreak()'));

ok('the timer exports the two break hooks',
   /export function pauseTimerForBreak/.test(timerSrc) &&
   /export function resumeTimerAfterBreak/.test(timerSrc));

ok('resume only fires for a timer the break itself paused',
   /pausedByBreak/.test(timerSrc) &&
   /if \(pausedByBreak\)/.test(timerSrc));

// --- Robustness -------------------------------------------------------------

ok('the overlay closes even when ending the break fails',
   /finally\s*\{[\s\S]*hide\(\);[\s\S]*resumeTimerAfterBreak\(\);/.test(code));

ok('a double-click cannot fire two ends',
   code.includes('if (ending) return'));

ok('a failed start shows no overlay',
   /catch[\s\S]*Could not start a break/.test(moduleSrc));

ok('an open break is restored after a reload',
   code.includes('restoreOpenBreak') &&
   code.includes('/api/attendance/break/open'));

ok('closing the tab mid-break ends it',
   /pagehide/.test(code) && /keepalive/.test(code));

ok('the server start time wins over the click time',
   code.includes('new Date(body.started_at).getTime()'));

// --- Markup -----------------------------------------------------------------

ok('the page carries the overlay', pageSrc.includes('id="breakOverlay"'));
ok('the page carries the trigger', pageSrc.includes('id="takeBreakBtn"'));
ok('the page carries the elapsed readout', pageSrc.includes('id="breakElapsed"'));
ok('the page carries the End Break button', pageSrc.includes('id="endBreakBtn"'));

ok('the elapsed readout is announced as it changes',
   /id="breakElapsed"[^>]*aria-live/.test(pageSrc));

ok('the overlay is marked up as a dialog',
   /id="breakOverlay"[^>]*role="dialog"/.test(pageSrc));

ok('the overlay markup says why it is not dismissible',
   /NOT dismissible|not dismissible/.test(pageSrc));

// --- Pins (rule 13) ---------------------------------------------------------

ok('the module pins its imports',
   /api\.js\?v=\d+/.test(code) && /timer\.js\?v=\d+/.test(code));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

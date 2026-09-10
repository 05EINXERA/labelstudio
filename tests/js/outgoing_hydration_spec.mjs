/**
 * Behaviour spec for `openTaskWasHydrated()` — the outgoing-task check that
 * stops the gallery-switch flush shipping an empty annotation set.
 *
 * Run: node tests/js/outgoing_hydration_spec.mjs (or via
 * tests/test_outgoing_hydration.py)
 *
 * ## What this closes
 *
 * switchImage() flushes the *outgoing* task before opening the next one, and
 * does so deliberately outside the hydration gate: the gate is already shut for
 * the incoming task by then, and the work being rescued belongs to the one
 * being left. That rescue is real and must keep working.
 *
 * The bug is what it copies. `prevTask.annotations = [...state.annotations]`
 * trusts the canvas to describe the outgoing task — but when that task's own
 * hydration never landed (superseded by a faster switch, or still in flight),
 * the canvas holds `[]`. An empty array is a perfectly valid annotation
 * payload: it reaches the server as "this task is now empty", where only the
 * clear-guard's 422 stops it.
 *
 * That is the shape of all 67 empty-payload wipe attempts logged across eight
 * days, on six users and a fast server — open a task, page away before it
 * settles, and an empty save leaves within seconds of the open
 * (.devnotes/wipe-guard-bypass-fix/04_VERIFICATION.md §C).
 *
 * ## Why a separate predicate from hydrationOk()
 *
 * `hydrationOk()` answers "is the task open *right now* hydrated?". By the time
 * the outgoing task is flushed, `beginHydration()` has already moved the
 * generation to the incoming task, so hydrationOk() describes the wrong task.
 * The question has to be asked *before* the generation moves, which is exactly
 * what the caller does and what these cases pin.
 *
 * state.js touches window/localStorage at import time and pulls in view.js,
 * which constructs an Image(); all three are stubbed, as in
 * hydration_gate_spec.mjs.
 */
globalThis.window = { location: { origin: 'http://test' } };
globalThis.Image = class { constructor() { this.naturalWidth = 0; this.naturalHeight = 0; } };
globalThis.localStorage = {
  _d: new Map(),
  getItem(k) { return this._d.has(k) ? this._d.get(k) : null; },
  setItem(k, v) { this._d.set(k, String(v)); },
  removeItem(k) { this._d.delete(k); },
};

const url = new URL('../../frontend/js/state.js', import.meta.url);
const {
  beginHydration, completeHydration, failHydration,
  hydrationOk, openTaskWasHydrated,
} = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

// --- the rescue path must keep working -------------------------------------

{
  // A task that hydrated, then the user pages away. This is the case the flush
  // exists for: the canvas genuinely holds that task's work, unsaved.
  const g = beginHydration();
  completeHydration(g);
  ok('a hydrated open task reports hydrated', openTaskWasHydrated() === true);

  // Read before the next beginHydration(), which is the contract.
  const outgoingWasHydrated = openTaskWasHydrated();
  beginHydration();
  ok('the answer is captured before the generation moves', outgoingWasHydrated === true);
  ok('hydrationOk() now describes the INCOMING task, not the outgoing one',
     hydrationOk() === false);
}

// --- the bug this closes ----------------------------------------------------

{
  // Task opened, hydration never completed, user pages away. The canvas is `[]`
  // and says nothing about this task. Flushing it would send "make it empty".
  beginHydration();
  ok('an unhydrated open task reports NOT hydrated', openTaskWasHydrated() === false);
}

{
  // Hydration was attempted and failed outright (fetch error). Same conclusion:
  // the canvas never received this task's work.
  const g = beginHydration();
  failHydration(g);
  ok('a failed hydration reports NOT hydrated', openTaskWasHydrated() === false);
}

{
  // Rapid paging: a superseded switch's completeHydration() is refused, so the
  // stale generation must not make the open task look hydrated. This is the
  // property that makes the check safe under exactly the fast-switching that
  // produces the bug in the first place.
  const stale = beginHydration();
  beginHydration();                     // a newer switch supersedes it
  ok('a superseded completion is refused', completeHydration(stale) === false);
  ok('a superseded generation does not report hydrated',
     openTaskWasHydrated() === false);
}

// --- fail-closed, like the gate it complements ------------------------------

{
  // The very first switch of a session: nothing has hydrated yet. Must be false
  // — the predicate is positive, so "unknown" is never "yes". A fail-open
  // version of the original hydration gate is what let a fast Ctrl+S wipe a
  // task, and the same reasoning applies here.
  beginHydration();
  ok('a fresh generation with no completion is NOT hydrated',
     openTaskWasHydrated() === false);
}

{
  // Completing a generation other than the current one must not open it.
  beginHydration();
  ok('completing a foreign generation does not open the check',
     completeHydration(-1) === false && openTaskWasHydrated() === false);
}

{
  // And the positive case still works after all the negatives, i.e. the checks
  // above did not leave the module wedged shut.
  const g = beginHydration();
  completeHydration(g);
  ok('a later genuine hydration still reports hydrated',
     openTaskWasHydrated() === true);
}

// --- agreement with hydrationOk() while the task is still open --------------

{
  // Before any switch, the two answer the same question and must agree.
  // They only diverge once beginHydration() has moved on, which is the whole
  // reason this predicate exists.
  const g = beginHydration();
  completeHydration(g);
  ok('the two agree while the task is open (hydrated)',
     openTaskWasHydrated() === hydrationOk());
  beginHydration();
  ok('the two agree while the task is open (unhydrated)',
     openTaskWasHydrated() === hydrationOk());
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

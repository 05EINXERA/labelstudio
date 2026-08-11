/**
 * Behaviour spec for the Completed → In Progress demotion rule.
 *
 * Run: node tests/js/status_demotion_spec.mjs
 *
 * The rule: saving a Completed task demotes it to In Progress, so a reviewer
 * re-reviews work that was amended after completion rather than silently
 * passing it as already-approved.
 *
 * The bug this guards: the rule fired on *every* save, but not every save is
 * an edit. The 30s time drain, the gallery-switch flush and the
 * visibilitychange beacon all save having touched no annotations. So merely
 * opening a Completed task reverted it to In Progress, and "Save as Complete"
 * appeared to work until the next drain undid it.
 *
 * The fix keys the demotion on the annotation set actually differing from what
 * the server sent, and it must FAIL SAFE: when change cannot be proven (no
 * fingerprint — hydration failed, or an older bundle), it reports "changed" so
 * the demotion still happens exactly as it did before.
 *
 * state.js touches window/localStorage at import time, so those are stubbed.
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
const s = await import(url);
const {
  beginHydration, completeHydration,
  noteHydratedAnnotations, annotationsChangedSinceHydration,
} = s;

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

const box = (id, x) => ({ id, type: 'box', labelId: 'L1', x, y: 0, width: 10, height: 10 });

// --- 1. Fail-safe: no fingerprint means "assume edited" -------------------
//
// This is the property that keeps the fix from ever being *less* safe than the
// unconditional rule it replaces. A task whose hydration failed has no
// baseline, and must still demote.

const g1 = beginHydration();
ok('no fingerprint reports changed (fail-safe)',
   annotationsChangedSinceHydration([box('a', 0)]) === true);

// --- 2. An untouched set is not a change ---------------------------------
//
// The core of the bug: opening a Completed task and saving (time drain,
// gallery switch) must not look like an edit.

completeHydration(g1);
const server = [box('a', 0), box('b', 50)];
noteHydratedAnnotations(server);

ok('the identical set is unchanged',
   annotationsChangedSinceHydration(server) === false);
ok('a structurally equal copy is unchanged',
   annotationsChangedSinceHydration([box('a', 0), box('b', 50)]) === false);

// --- 3. Real edits are still detected ------------------------------------
//
// Each of these must still demote a Completed task. The count-only check that
// already existed cannot see the first two.

ok('moving a box is a change',
   annotationsChangedSinceHydration([box('a', 999), box('b', 50)]) === true);
ok('relabelling is a change',
   annotationsChangedSinceHydration([
     { ...box('a', 0), labelId: 'L2' }, box('b', 50),
   ]) === true);
ok('adding an annotation is a change',
   annotationsChangedSinceHydration([...server, box('c', 90)]) === true);
ok('deleting an annotation is a change',
   annotationsChangedSinceHydration([box('a', 0)]) === true);
ok('deleting everything is a change',
   annotationsChangedSinceHydration([]) === true);

// --- 4. A new task does not inherit the previous fingerprint -------------
//
// Carrying it across a switch would make the incoming task's untouched
// annotations look edited, or worse, an edited set look untouched.

beginHydration();
ok('a new switch clears the fingerprint',
   annotationsChangedSinceHydration(server) === true);

// --- 5. Re-baselining after a save ---------------------------------------
//
// Once the server has taken a write, that payload is the new baseline. Without
// this the fingerprint stays pinned to the original hydration and the next
// drain still counts as an edit — demoting the task that was just completed.

const g3 = beginHydration();
completeHydration(g3);
noteHydratedAnnotations(server);
const edited = [...server, box('c', 90)];
ok('an edit is a change before saving',
   annotationsChangedSinceHydration(edited) === true);
noteHydratedAnnotations(edited);          // what syncToBackend does on success
ok('after re-baselining the same set is unchanged',
   annotationsChangedSinceHydration(edited) === false);

// --- 6. null/undefined are treated as the empty set ----------------------

const g4 = beginHydration();
completeHydration(g4);
noteHydratedAnnotations([]);
ok('null matches an empty baseline',
   annotationsChangedSinceHydration(null) === false);
ok('undefined matches an empty baseline',
   annotationsChangedSinceHydration(undefined) === false);

console.log(`\n${pass} passed, ${fail} failed`);
if (fail > 0) process.exit(1);

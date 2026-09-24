/**
 * Behaviour spec for the annotation hydration gate.
 *
 * Run: node tests/js/hydration_gate_spec.mjs
 *
 * Guards the rules established by the annotation-wipe investigation
 * (.agents/annotation-wipe-fix/). Annotations are stored as one JSON blob and
 * every save is a wholesale replacement, so a save issued while the canvas
 * holds the empty array that resetWorkspaceForNewImage() just installed —
 * before the server copy has landed — writes `[]` over real work.
 *
 * The gate closes that window, and three properties make it safe:
 *
 *   1. It is POSITIVE. Only a confirmed hydration permits a save. The first
 *      cut of the fix tested `task._hydrated === false`, which let the
 *      `undefined` of a not-yet-touched task pass every guard — fail-open on a
 *      destructive operation. A fast Ctrl+S right after clicking a task still
 *      wiped it.
 *   2. It is GENERATION-KEYED, not a per-task boolean. Rapid paging re-enters
 *      switchImage before earlier awaits resolve; a superseded call must not
 *      be able to mark the newly-open task hydrated.
 *   3. A failed fetch keeps it SHUT until a later attempt succeeds.
 *
 * state.js touches window/localStorage at import time, and pulls in view.js
 * which constructs an Image(), so all three browser globals are stubbed.
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
  beginHydration, completeHydration, failHydration,
  hydrationOk, hydrationFailed, hydrationSaveBlock, currentHydrationGeneration,
  noteHydratedAnnotationCount, getHydratedAnnotationCount, pendingDeletedIds,
} = s;

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

// --- 1. The gate is positive: unopened state blocks ----------------------

// This is the regression that mattered: before any hydration completes there
// is no "hydrated" marker anywhere, and the gate must read that as "block",
// not as "nothing says otherwise, so allow".
const g1 = beginHydration();
ok('a fresh switch blocks saving', hydrationOk() === false);
ok('and gives a loading reason', /loading/.test(hydrationSaveBlock()));

// --- 2. A confirmed fetch opens it ---------------------------------------

completeHydration(g1);
ok('a completed hydration permits saving', hydrationOk() === true);
ok('and reports no block', hydrationSaveBlock() === null);

// --- 3. The next switch re-closes it immediately --------------------------

// Ctrl+S pressed between clicking a task and its fetch landing must be
// refused, even though the *previous* task was fully hydrated.
const g2 = beginHydration();
ok('switching tasks re-blocks saving at once', hydrationOk() === false);

// --- 4. A superseded generation cannot open the gate ----------------------

// The re-entrancy rule: switchImage(A) is still in flight when the user pages
// to B. A's fetch resolves late and calls completeHydration with A's token.
// Honouring it would declare B hydrated on the strength of A's fetch, with
// B's canvas still empty.
ok('a stale completion is refused', completeHydration(g1) === false);
ok('and the gate stays shut', hydrationOk() === false);
ok('the current generation is the newer one', currentHydrationGeneration() === g2);

completeHydration(g2);
ok('the current generation can still open it', hydrationOk() === true);

// --- 5. A failed fetch keeps it shut --------------------------------------

const g3 = beginHydration();
failHydration(g3);
ok('a failed hydration blocks saving', hydrationOk() === false);
ok('and is distinguishable from still-loading', hydrationFailed() === true);
ok('with a reason naming the recovery', /reload|failed/i.test(hydrationSaveBlock()));

// A stale failure must not poison a later attempt either.
ok('a stale failure is refused', failHydration(g1) === false);

// --- 6. Retrying clears the failure ---------------------------------------

const g4 = beginHydration();
ok('a retry clears the failed state', hydrationFailed() === false);
ok('but still blocks until it succeeds', hydrationOk() === false);
completeHydration(g4);
ok('a successful retry reopens the gate', hydrationOk() === true);
ok('and clears the block message', hydrationSaveBlock() === null);

// --- 7. A delete-all is recorded, never inferred ---------------------------

// The server's wipe guard refuses a save that drops shapes the client did not
// name in `deleted_ids`. The client used to *infer* a delete-all instead —
// `clearIsUserIntent()` set `allow_clear` whenever a hydrated task's canvas was
// empty — which switched the guard off for exactly the faulted empty canvas it
// exists to catch. Deletions now come only from explicit removal paths
// (noteUserRemoved); hydration, emptiness and task switches never create one.
// The removal bookkeeping itself is pinned in wipe_guard_spec.mjs.

ok('the inference helper is gone', s.clearIsUserIntent === undefined);

s.state.gallery = [{ id: 501 }, { id: 502 }];
s.state.galleryIndex = 0;
const g5 = beginHydration();
noteHydratedAnnotationCount(500);
completeHydration(g5);
ok('the hydrated count is recorded', getHydratedAnnotationCount() === 500);
s.state.annotations = [];
ok('an emptied canvas is not a recorded deletion', pendingDeletedIds(501).length === 0);

// The count must not survive a task switch.
s.state.galleryIndex = 1;
const g7 = beginHydration();
ok('the count resets on switch', getHydratedAnnotationCount() === 0);
failHydration(g7);
ok('a failed hydration records no deletion either', pendingDeletedIds(502).length === 0);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

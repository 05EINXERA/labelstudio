/**
 * Behaviour spec for the no-edit gate in `frontend/js/components/timer.js`.
 *
 * Run: node tests/js/timer_noop_drain_spec.mjs  (or via tests/test_frontend_timer_noop.py)
 *
 * The bug (.devnotes/unwanted-time-change/01_DIAGNOSIS.md): the session timer
 * auto-starts on the first canvas pointerdown, and pan (pointerdown) and zoom
 * (wheel) both count as activity, so a reviewer who only *looks* at a task
 * accrues seconds and the 5-minute idle rollback never trips. Draining those
 * seconds grew `Task.time_spent` and moved `Task.updated_at` — and `updated_at`
 * is the optimistic-concurrency token (CLAUDE.md rule 11), not merely a display
 * column, so a look-only visit made the task appear freshly edited to everyone.
 *
 * What must NOT regress, and is therefore asserted here rather than assumed:
 *
 *   * The gate is scoped to a *pure* time drain. A caller that supplied an
 *     annotation set, or an explicit status ("Save as Complete", Approve,
 *     Reject), is making a real save and must always go through.
 *   * It fails safe. When the edit state cannot be determined the drain
 *     proceeds — a redundant save costs a request, a wrongly suppressed one
 *     costs annotation work.
 *   * A suppressed drain sends nothing AND queues nothing. It is not a failed
 *     save, so it must not reach the offline outbox.
 *   * The failure paths are untouched: a rejected save still preserves its
 *     seconds (via the queue), and a 409 still hands off to the conflict
 *     handler with the delta intact.
 */

// --- shims ----------------------------------------------------------------
// Just enough browser for timer.js and its import graph to load unmodified.
// Testing the real module matters more here than a tidy harness: the whole
// point is that the shipped drain behaves correctly.

const store = new Map();
// apiFetch returns early (undefined) when this is falsy — it treats a missing
// flag as "logged out" and redirects. Every drain under test is a logged-in
// annotator's, so the session flag has to be present or the module under test
// never reaches fetch at all.
store.set('logged_in', 'true');
globalThis.localStorage = {
  getItem: k => store.has(k) ? store.get(k) : null,
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: k => store.delete(k),
  get length() { return store.size; },
  key: i => [...store.keys()][i],
};

const noopEl = () => ({
  textContent: '', innerHTML: '', title: '',
  addEventListener() {}, removeEventListener() {},
  classList: { add() {}, remove() {}, contains: () => false },
  getContext: () => ({}),
  querySelector: () => null, querySelectorAll: () => [],
  style: {}, dataset: {},
});

globalThis.document = {
  getElementById: () => noopEl(),
  querySelector: () => noopEl(),
  querySelectorAll: () => [],
  createElement: () => noopEl(),
  addEventListener() {}, removeEventListener() {},
  visibilityState: 'visible',
  body: noopEl(),
  cookie: '',
};
globalThis.window = {
  addEventListener() {}, removeEventListener() {},
  location: { origin: 'http://x', href: 'http://x/app.html', search: '' },
  setTimeout, clearTimeout, setInterval, clearInterval,
};
globalThis.Blob = class { constructor(parts) { this.parts = parts; } };
// Node 22 defines `navigator` as a getter-only global, so it has to be
// redefined rather than assigned. Beacons are not exercised here (every
// case below uses the normal fetch path), but timer.js reads
// `navigator.sendBeacon` on the unload branch and must find something.
Object.defineProperty(globalThis, 'navigator', {
  value: { sendBeacon: () => true }, configurable: true, writable: true,
});

// Records every request the module attempts, so "sent nothing" is a positive
// assertion rather than an absence of errors.
let fetchCalls = [];
// `clone()` is present because apiFetch calls it on a 403 to sniff the body.
const okResponse = () => ({
  ok: true, status: 200,
  json: async () => ({ updated_at: '2026-08-19T10:00:00+00:00' }),
  clone() { return this; },
});
let fetchImpl = async () => okResponse();
globalThis.fetch = async (url, opts) => {
  fetchCalls.push({ url, opts });
  return fetchImpl(url, opts);
};

const timerUrl = new URL('../../frontend/js/components/timer.js', import.meta.url);
const timer = await import(timerUrl);
// The `?v=` pins are load-bearing *here*, not just in the browser: node keys
// its module cache on the full specifier, so importing `timer-state.js` without
// the query string yields a second, unrelated module instance whose
// `taskSessionSeconds` timer.js never touches. The pins must match the ones
// timer.js itself imports (frontend/js/components/timer.js).
const stateUrl = new URL('../../frontend/js/timer-state.js?v=3', import.meta.url);
const { timerState } = await import(stateUrl);
const queueUrl = new URL('../../frontend/js/offline-queue.js?v=4', import.meta.url);
const queue = await import(queueUrl);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

function reset({ edited = false, frozen = false, seconds = 30 } = {}) {
  fetchCalls = [];
  fetchImpl = async () => okResponse();
  timerState.taskSessionSeconds = seconds;
  timer.setEditedResolver(() => edited);
  timer.setFrozenResolver(() => frozen);
  timer.setConflictHandler(null);
  for (const id of [1, 2, 3]) queue.discardWrite(id);
}

const task = () => ({ id: 1, status: 'In Progress', updated_at: '2026-08-19T09:00:00+00:00', time_spent: 100 });

// --- 1. the bug: a pure time drain with no edit is suppressed --------------

reset({ edited: false });
let t = task();
let res = await timer.drainTaskTime(t);
ok('no-edit drain sends nothing', fetchCalls.length === 0);
ok('no-edit drain discards the accrued seconds', timerState.taskSessionSeconds === 0);
ok('no-edit drain returns the skip sentinel', res === timer.DRAIN_SKIPPED);
ok('skip sentinel is distinct from success', timer.DRAIN_SKIPPED !== true);
ok('skip sentinel is distinct from failure', timer.DRAIN_SKIPPED !== false);
ok('no-edit drain queues nothing', queue.peekWrite(1) === undefined || queue.peekWrite(1) === null);
ok('no-edit drain does not touch stored time_spent', t.time_spent === 100);
ok('no-edit drain leaves the concurrency token alone', t.updated_at === '2026-08-19T09:00:00+00:00');

// --- 2. a real edit still saves, with the delta intact ---------------------

reset({ edited: true });
t = task();
res = await timer.drainTaskTime(t);
ok('edited drain sends the save', fetchCalls.length === 1);
ok('edited drain reports success', res === true);
{
  const body = JSON.parse(fetchCalls[0].opts.body);
  ok('edited drain carries the full delta', body.time_spent_delta === 30);
  ok('edited drain omits annotations when none supplied', body.annotations === undefined);
  ok('edited drain banks the delta locally', t.time_spent === 130);
}

// --- 3. scope: an explicit save always goes through -----------------------
// These are the paths that must never be swallowed, even with no annotation
// change: the user stated an intent.

reset({ edited: false });
t = task();
res = await timer.drainTaskTime(t, { status: 'Completed' });
ok('explicit status drain is never suppressed', fetchCalls.length === 1);
ok('explicit status drain sends that status',
   JSON.parse(fetchCalls[0].opts.body).status === 'Completed');

reset({ edited: false });
t = task();
await timer.drainTaskTime(t, { annotations: [{ id: 'a' }] });
ok('drain carrying annotations is never suppressed', fetchCalls.length === 1);
ok('supplied annotations are sent',
   JSON.parse(fetchCalls[0].opts.body).annotations === JSON.stringify([{ id: 'a' }]));

reset({ edited: false });
t = task();
await timer.drainTaskTime(t, { annotations: [], allowClear: true });
ok('deliberate delete-all is never suppressed', fetchCalls.length === 1);
ok('deliberate delete-all still declares allow_clear',
   JSON.parse(fetchCalls[0].opts.body).allow_clear === true);

// --- 4. fail safe ---------------------------------------------------------

reset({ edited: false });
timer.setEditedResolver(() => { throw new Error('state unavailable'); });
t = task();
await timer.drainTaskTime(t);
ok('a throwing edit resolver falls back to saving', fetchCalls.length === 1);

reset({ edited: false });
timer.setEditedResolver(null);      // ignored — keeps the previous resolver
timer.setEditedResolver(undefined); // ditto
t = task();
await timer.drainTaskTime(t);
ok('setEditedResolver ignores a non-function', fetchCalls.length === 0);

// --- 5. a frozen task is still refused outright ---------------------------

reset({ edited: true, frozen: true });
t = task();
res = await timer.drainTaskTime(t);
ok('frozen task sends nothing even when edited', fetchCalls.length === 0);
ok('frozen task keeps its seconds rather than discarding them',
   timerState.taskSessionSeconds === 30);

// --- 6. failure paths unchanged -------------------------------------------

reset({ edited: true });
fetchImpl = async () => ({ ok: false, status: 500, json: async () => ({}), clone() { return this; } });
t = task();
res = await timer.drainTaskTime(t);
ok('a refused save reports failure', res === false);
ok('a refused save queues the payload for replay', !!queue.peekWrite(1));
ok('a refused save does not double-count into the accumulator',
   timerState.taskSessionSeconds === 0);
queue.discardWrite(1);

reset({ edited: true });
let conflicted = null;
timer.setConflictHandler((task) => { conflicted = task; });
fetchImpl = async () => ({ ok: false, status: 409, json: async () => ({}), clone() { return this; } });
t = task();
res = await timer.drainTaskTime(t);
ok('a 409 reports failure', res === false);
ok('a 409 hands off to the conflict handler', conflicted !== null);
ok('a 409 returns the seconds to the accumulator', timerState.taskSessionSeconds === 30);

// --- 7. a drain with no task is a no-op -----------------------------------

reset({ edited: true });
ok('drain with no task returns false', (await timer.drainTaskTime(null)) === false);
ok('drain with an unsaved task returns false',
   (await timer.drainTaskTime({ id: null })) === false);
ok('drain with no task sends nothing', fetchCalls.length === 0);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);

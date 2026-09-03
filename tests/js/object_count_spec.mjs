/**
 * Behaviour spec for the `object_count` field in a save payload.
 *
 * Run: node tests/js/object_count_spec.mjs
 *
 * `object_count` is the Objects panel's own row count, sent so the service log
 * can record what the annotator was looking at when they saved
 * (.devnotes/logging/02_PLAN.md §5). It is diagnostic only: the server counts
 * the blob itself and never acts on this number.
 *
 * Three properties are guarded, all of which would otherwise be easy to break
 * while "improving" the payload builder:
 *
 *  1. It is sent whenever an annotation set is sent, and equals that set's
 *     length — the log is useless if the count describes a different write.
 *  2. It is NOT sent on a time-only save. Those carry no annotations, so any
 *     count would describe some earlier moment and would read on the line as
 *     though the annotator had that many objects at that instant.
 *  3. It never changes what is saved. Adding a diagnostic field must not
 *     perturb `annotations`, `allow_clear`, or the delete-all path — the
 *     logging work exists to explain annotation loss, not to cause it.
 */

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


// The harness above (shims, module imports, `ok`, `reset`, `task`) is shared
// with timer_noop_drain_spec.mjs, which exercises the same builder from the
// other direction — what it suppresses rather than what it sends.
const bodyOf = () => JSON.parse(fetchCalls[0].opts.body);
const box = i => ({ id: `a${i}`, type: 'rect', label: 'car' });

// --- 1. sent with an annotation set, and matching its length --------------

reset({ edited: true });
await timer.drainTaskTime(task(), { annotations: [box(1), box(2), box(3)] });
ok('a save carrying annotations sends object_count', bodyOf().object_count === 3);
ok('object_count equals the set actually sent',
   bodyOf().object_count === JSON.parse(bodyOf().annotations).length);

reset({ edited: true });
await timer.drainTaskTime(task(), { annotations: [box(1)] });
ok('object_count follows the set, not a stale value', bodyOf().object_count === 1);

// --- 2. absent on a time-only save ---------------------------------------
// The 30s tick, the visibilitychange beacon and the gallery-switch flush all
// drain time without annotations. A count there would describe a moment other
// than this write's.

// `edited: true` so the drain is not suppressed by the no-edit gate — the
// point here is the payload's shape, not whether it is sent.
reset({ edited: true });
await timer.drainTaskTime(task());
ok('a time-only drain sends no annotations', bodyOf().annotations === undefined);
ok('a time-only drain sends no object_count', bodyOf().object_count === undefined);

// --- 3. a deliberate delete-all reports zero, and still declares itself ---
// This is the one save where the count is most worth having: it is exactly the
// event an annotator later reports as lost work.

reset({ edited: true });
await timer.drainTaskTime(task(), { annotations: [], allowClear: true });
ok('a delete-all reports zero objects', bodyOf().object_count === 0);
ok('a delete-all still declares allow_clear', bodyOf().allow_clear === true);

// --- 4. the diagnostic field changes nothing about the save ---------------

reset({ edited: true });
await timer.drainTaskTime(task(), { annotations: [box(1), box(2)], status: 'Completed' });
{
  const b = bodyOf();
  ok('annotations are unchanged by the added field',
     b.annotations === JSON.stringify([box(1), box(2)]));
  ok('the status still rides along', b.status === 'Completed');
  ok('the time delta is untouched', b.time_spent_delta === 30);
  ok('a non-clearing save does not set allow_clear', b.allow_clear === undefined);
}

console.log(`
${pass} passed, ${fail} failed`);
if (fail) process.exit(1);

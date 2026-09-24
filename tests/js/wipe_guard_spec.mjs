/**
 * Behaviour spec for the client half of the wipe guard.
 *
 * Run: node tests/js/wipe_guard_spec.mjs
 *
 * The server refuses a save that removes shapes the client did not name in
 * `deleted_ids` (api/routers/tasks.py, tests/test_bulk_loss_guard.py). The
 * client's job is to name them honestly, and to stop a doomed save before it
 * leaves. This pins:
 *
 *   1. wipe-guard.js mirrors the server: same thresholds as config.py, same
 *      verdict on the same cases (task 660, task 691, the owner cleanups).
 *   2. state.js records deliberate removals per task, keeps them across a task
 *      switch, and settles only what an accepted save actually carried.
 *   3. timer.js sends them, and settles them only on a 2xx — never on a 422 or
 *      a beacon, whose outcome is unknown.
 *   4. workspace.js holds back a save the server would refuse, and will not
 *      restore a draft that drops shapes nobody deleted.
 *
 * Imports use the same `?v=` pins the modules use for each other: node keys
 * its module cache on the full specifier, so an unpinned import would load a
 * second, unrelated copy of state.js.
 */
import { readFileSync } from 'node:fs';

const store = new Map();
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k),
  get length() { return store.size; },
  key: (i) => [...store.keys()][i],
};
store.set('logged_in', 'true');
const noopEl = () => ({
  textContent: '', innerHTML: '', title: '', value: '',
  addEventListener() {}, removeEventListener() {}, appendChild() {}, setAttribute() {},
  classList: { add() {}, remove() {}, contains: () => false, toggle() {} },
  getContext: () => new Proxy({}, { get: () => () => {} }),
  querySelector: () => noopEl(), querySelectorAll: () => [],
  style: {}, dataset: {},
  getBoundingClientRect: () => ({ width: 0, height: 0, left: 0, top: 0 }),
});
globalThis.document = {
  getElementById: () => noopEl(), querySelector: () => noopEl(), querySelectorAll: () => [],
  createElement: () => noopEl(), addEventListener() {}, removeEventListener() {},
  visibilityState: 'visible', body: noopEl(), cookie: '',
};
globalThis.window = {
  addEventListener() {}, removeEventListener() {},
  location: { origin: 'http://x', href: 'http://x/app.html', search: '' },
  setTimeout, clearTimeout, setInterval, clearInterval, devicePixelRatio: 1,
};
globalThis.Image = class { constructor() { this.naturalWidth = 0; this.naturalHeight = 0; } };
globalThis.Blob = class { constructor(parts) { this.parts = parts; } };
globalThis.requestAnimationFrame = (f) => setTimeout(f, 0);
let beaconBodies = [];
Object.defineProperty(globalThis, 'navigator', {
  value: { sendBeacon: (_url, blob) => { beaconBodies.push(blob.parts[0]); return true; } },
  configurable: true, writable: true,
});

let fetchCalls = [];
let nextStatus = 200;
globalThis.fetch = async (url, opts) => {
  fetchCalls.push({ url, opts });
  const status = nextStatus;
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => (status === 200 ? { updated_at: '2026-09-24T10:00:00+00:00' } : { detail: 'refused' }),
    clone() { return this; },
  };
};

const js = (p) => new URL(`../../frontend/js/${p}`, import.meta.url);
const guard = await import(js('wipe-guard.js?v=1'));
const st = await import(js('state.js?v=12'));
const timer = await import(js('components/timer.js?v=10'));
const ws = await import(js('components/workspace.js?v=29'));
const { timerState } = await import(js('timer-state.js?v=3'));

let pass = 0, fail = 0;
const ok = (name, cond, extra = '') => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name, extra));
};
const ids = (n, prefix = 'a') => Array.from({ length: n }, (_, i) => `${prefix}${i}`);
const shapes = (list) => list.map((id) => ({ id, type: 'box' }));
const bodyOf = (i = fetchCalls.length - 1) => JSON.parse(fetchCalls[i].opts.body);

// --- 1. The mirror agrees with the server --------------------------------

{
  const cfg = readFileSync(new URL('../../config.py', import.meta.url), 'utf-8');
  const minLost = cfg.match(/WIPE_GUARD_MIN_LOST", "(\d+)"/);
  const ratio = cfg.match(/WIPE_GUARD_RATIO", "([\d.]+)"/);
  ok('the floor matches config.py', minLost && Number(minLost[1]) === guard.WIPE_GUARD_MIN_LOST);
  ok('the ratio matches config.py', ratio && Number(ratio[1]) === guard.WIPE_GUARD_RATIO);
}

// Each case: stored count, kept ids, deleted ids, expected verdict. The same
// table as tests/test_bulk_loss_guard.py.
const verdict = (storedN, kept, deleted) => {
  const stored = new Set(ids(storedN));
  const { unexplained } = guard.unexplainedRemovals(stored, shapes(kept), deleted);
  return guard.isRefusedLoss({ stored: stored.size, unexplained, empty: kept.length === 0 });
};
ok('task 660 (976 -> one new box) is refused', verdict(976, ['new-box'], []) === true);
ok('the same payload with every id deleted passes', verdict(976, ['new-box'], ids(976)) === false);
ok('task 691 (2631 -> 1194, 55%) is refused', verdict(2631, ids(2631).slice(0, 1194), []) === true);
for (const [before, after] of [[221, 97], [548, 237], [153, 69], [1117, 773]]) {
  ok(`owner cleanup ${before} -> ${after} with its deletions passes`,
     verdict(before, ids(before).slice(0, after), ids(before).slice(after)) === false);
}
ok('3 confirmed deletes cannot carry 87 silent losses',
   verdict(100, ids(10), ids(100).slice(10, 13)) === true);
ok('padding with unknown ids explains nothing',
   verdict(100, ['x'], ids(500, 'zz')) === true);
ok('below the floor (8 -> 2) passes', verdict(8, ids(2), []) === false);
ok('under the ratio (100 -> 75) passes', verdict(100, ids(75), []) === false);
ok('exactly at the ratio (100 -> 70) passes', verdict(100, ids(70), []) === false);
ok('just over the ratio (100 -> 69) is refused', verdict(100, ids(69), []) === true);
ok('emptying 3 with nothing named is refused', verdict(3, [], []) === true);
ok('emptying 3 with 2 named is still refused', verdict(3, [], ids(2)) === true);
ok('emptying 3 with all named passes', verdict(3, [], ids(3)) === false);
{
  const stored = new Set(['7', '8']);
  const { removed } = guard.unexplainedRemovals(stored, [{ id: 7 }, { id: 8 }], []);
  ok('numeric ids match their stored string form', removed === 0);
}
ok('a task with nothing stored is never refused',
   guard.isRefusedLoss({ stored: 0, unexplained: 0, empty: true }) === false);

// --- 2. When to ask ------------------------------------------------------

ok('deleting 2 of 3 does not ask', guard.needsDeleteConfirm(2, 3) === false);
ok('deleting 9 of 10 does not ask (under the floor)', guard.needsDeleteConfirm(9, 10) === false);
ok('deleting 30 of 100 does not ask (at the ratio)', guard.needsDeleteConfirm(30, 100) === false);
ok('deleting 31 of 100 asks', guard.needsDeleteConfirm(31, 100) === true);
ok('deleting 975 of 976 asks', guard.needsDeleteConfirm(975, 976) === true);

// --- 3. The deletion record in state.js ------------------------------------

st.state.gallery = [{ id: 1 }, { id: 2 }];
st.state.galleryIndex = 0;
{
  const before = shapes(['p', 'q', 'r']);
  st.noteUserRemoved(before, shapes(['q']));
  ok('removed ids are recorded against the open task',
     JSON.stringify(st.pendingDeletedIds(1).sort()) === '["p","r"]');
  ok('other tasks are untouched', st.pendingDeletedIds(2).length === 0);

  // The gallery-switch flush saves the outgoing task after the switch began.
  st.state.galleryIndex = 1;
  st.beginHydration();
  ok('a task switch keeps the outgoing task\'s deletions',
     st.pendingDeletedIds(1).length === 2);

  st.acknowledgeDeletedIds(1, ['p']);
  ok('an accepted save settles only the ids it carried',
     JSON.stringify(st.pendingDeletedIds(1)) === '["r"]');
  st.acknowledgeDeletedIds(1, ['r']);
  ok('and the task drops out once all are settled', st.pendingDeletedIds(1).length === 0);

  st.restorePendingDeletions(2, [5, 'six']);
  ok('draft-recovered ids are merged as strings',
     JSON.stringify(st.pendingDeletedIds(2).sort()) === '["5","six"]');
  st.acknowledgeDeletedIds(2, ['5', 'six']);

  st.noteUserRemoved(shapes(['x']), shapes(['x']), 2);
  ok('a no-op removal records nothing', st.pendingDeletedIds(2).length === 0);

  st.noteServerAnnotationIds(2, [{ id: 7 }, { id: 'b' }, {}]);
  const baseline = st.serverAnnotationIds(2);
  ok('the server baseline stores string ids, skipping id-less shapes',
     baseline.size === 2 && baseline.has('7') && baseline.has('b'));
}

// --- 4. timer.js sends them and settles only on success --------------------

// Wired exactly as init.js wires it.
timer.setDeletionTracker({
  pending: (taskId) => st.pendingDeletedIds(taskId),
  accepted: (taskId, sentIds, annotations) => {
    st.acknowledgeDeletedIds(taskId, sentIds);
    st.noteServerAnnotationIds(taskId, annotations);
  },
});
timer.setEditedResolver(() => true);
timer.setFrozenResolver(() => false);

{
  const task = { id: 2, status: 'In Progress', updated_at: null };
  st.noteServerAnnotationIds(2, shapes(ids(20)));
  st.noteUserRemoved(shapes(ids(20)), shapes(ids(5)), 2);

  fetchCalls = []; nextStatus = 422; timerState.taskSessionSeconds = 0;
  await timer.drainTaskTime(task, { annotations: shapes(ids(5)) });
  ok('the deletions ride the save', bodyOf().deleted_ids?.length === 15);
  ok('allow_clear is never sent', bodyOf().allow_clear === undefined);
  ok('a refused save leaves them pending', st.pendingDeletedIds(2).length === 15);
  ok('and the baseline unchanged', st.serverAnnotationIds(2).size === 20);

  beaconBodies = [];
  await timer.drainTaskTime(task, { annotations: shapes(ids(5)), useBeacon: true });
  ok('a beacon carries them too', JSON.parse(beaconBodies[0]).deleted_ids?.length === 15);
  ok('but settles nothing (its outcome is unknown)', st.pendingDeletedIds(2).length === 15);

  fetchCalls = []; nextStatus = 200;
  st.noteUserRemoved(shapes(ids(5)), shapes(ids(4)), 2);   // a4 deleted mid-flight: sent now
  await timer.drainTaskTime(task, { annotations: shapes(ids(4)) });
  ok('an accepted save settles what it sent', st.pendingDeletedIds(2).length === 0);
  ok('and moves the baseline to what it sent', st.serverAnnotationIds(2).size === 4);

  fetchCalls = [];
  await timer.drainTaskTime(task, { annotations: shapes(ids(4)) });
  ok('a save with nothing deleted sends no deleted_ids', bodyOf().deleted_ids === undefined);

  fetchCalls = [];
  timerState.taskSessionSeconds = 30;
  await timer.drainTaskTime(task);
  ok('a time-only drain carries neither annotations nor deletions',
     bodyOf().annotations === undefined && bodyOf().deleted_ids === undefined);
}

// --- 5. workspace.js holds back a save the server would refuse -------------

{
  st.noteServerAnnotationIds(3, shapes(ids(976)));
  const r = ws.localWipeRefusal(3, shapes(['new-box']));
  ok('the task 660 save is held back', r !== null && r.unexplained === 976);
  ok('and the message names the count', r && /976/.test(r.message));

  st.noteUserRemoved(shapes(ids(976)), [], 3);
  ok('the same save after a real delete-all goes', ws.localWipeRefusal(3, shapes(['new-box'])) === null);
  st.acknowledgeDeletedIds(3, ids(976));

  ok('an ordinary edit goes', ws.localWipeRefusal(3, shapes(ids(976))) === null);
  ok('with no baseline the server decides', ws.localWipeRefusal(99, []) === null);
}

// --- 6. Drafts carry deletions, and a wiping draft never wins -------------

st.state.projectId = 7;
{
  // A real edit: 40 of 100 deleted, saved to a draft, the tab reloaded.
  st.state.gallery = [{ id: 10 }];
  st.state.galleryIndex = 0;
  const g = st.beginHydration();
  st.completeHydration(g);
  st.state.annotations = shapes(ids(60));
  st.noteUserRemoved(shapes(ids(100)), st.state.annotations, 10);
  ws.saveDraft();
  const saved = JSON.parse(store.get(st.draftKey(10)));
  ok('the draft records its deletions', saved.deletedIds?.length === 40);

  st.acknowledgeDeletedIds(10, ids(100));                 // "reload": pending gone
  st.state.annotations = shapes(ids(100));                // server copy on screen
  const restored = ws.restoreDraft({ id: 10 });
  ok('a draft whose removals are explained is restored',
     restored === true && st.state.annotations.length === 60);
  ok('and its deletions are pending again', st.pendingDeletedIds(10).length === 40);
}
{
  // The 660 draft: one new box over 976, nothing deleted.
  store.set(st.draftKey(11), JSON.stringify({
    annotations: shapes(['new-box']), projectId: 7, savedAt: Date.now(),
  }));
  st.state.annotations = shapes(ids(976));
  const restored = ws.restoreDraft({ id: 11 });
  ok('a draft dropping 976 undeleted shapes is not restored', restored === false);
  ok('the server copy stays on screen', st.state.annotations.length === 976);
  ok('the draft is kept, not destroyed (rule 18a)', store.has(st.draftKey(11)));
}
{
  // New work on top of the server copy is still recovered.
  store.set(st.draftKey(12), JSON.stringify({
    annotations: shapes([...ids(50), 'extra']), projectId: 7, savedAt: Date.now(),
  }));
  st.state.annotations = shapes(ids(50));
  ok('a draft that only adds work is restored',
     ws.restoreDraft({ id: 12 }) === true && st.state.annotations.length === 51);
}

// --- 7. The removal paths record what they remove -------------------------

// Driven through the real handlers, so dropping the bookkeeping from any of
// them fails here rather than as a refused save in production.
{
  const ix = await import(js('canvas/interactions.js?v=24'));
  st.state.gallery = [{ id: 20 }];
  st.state.galleryIndex = 0;
  const g = st.beginHydration();
  st.completeHydration(g);
  st.clearHistory();

  // A small delete: no dialog, recorded at once.
  st.state.annotations = shapes(['k1', 'k2', 'k3']);
  st.state.selectedIds = new Set(['k1', 'k2']);
  ix.deleteSelected();
  ok('a small delete removes the selection', st.state.annotations.length === 1);
  ok('and records both ids',
     JSON.stringify(st.pendingDeletedIds(20).sort()) === '["k1","k2"]');

  // Undo brings them back; redo removes them again and records that.
  ix.undoAction();
  ok('undo restores the deleted shapes', st.state.annotations.length === 3);
  st.acknowledgeDeletedIds(20, ['k1', 'k2']);
  ix.redoAction();
  ok('redo of a delete records the ids again',
     JSON.stringify(st.pendingDeletedIds(20).sort()) === '["k1","k2"]');
  st.acknowledgeDeletedIds(20, ['k1', 'k2']);

  // Undoing a draw removes the drawn shape — a removal like any other.
  st.state.annotations = shapes(['k3']);
  st.snapshot();
  st.state.annotations = [...st.state.annotations, { id: 'drawn', type: 'box' }];
  ix.undoAction();
  ok('undoing a draw records the drawn shape',
     JSON.stringify(st.pendingDeletedIds(20)) === '["drawn"]');
  st.acknowledgeDeletedIds(20, ['drawn']);

  // A large delete waits for the dialog: nothing is removed or recorded yet.
  st.state.annotations = shapes(ids(100));
  st.state.selectedIds = new Set(ids(60));
  ix.deleteSelected();
  ok('a large delete waits for confirmation', st.state.annotations.length === 100);
  ok('and records nothing until confirmed', st.pendingDeletedIds(20).length === 0);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

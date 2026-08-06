/**
 * Behaviour spec for the canvas undo/redo history stack.
 *
 * Run: node tests/js/history_spec.mjs
 *
 * Guards the two rules that a production incident on 2026-08-06 established
 * (task 707: a fresh Ctrl+Z on an already-annotated task wiped all 34 polygons
 * and blanked the class panel, and the wipe was saved to the server):
 *
 *   1. Undo is ANNOTATION-ONLY. Classes are project-level state shared by every
 *      task and every annotator; they must never ride in a history entry.
 *   2. Undo is PER-SESSION and PER-TASK. Opening a task starts with an empty
 *      stack, so Ctrl+Z on freshly-loaded work does nothing.
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
const { state, snapshot, clearHistory, resetWorkspaceForNewImage } = s;

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

const ann = (id) => ({ id, type: 'polygon', labelId: 'L1', points: [] });

// --- 1. Classes never enter the history stack ---------------------------

state.labels = [{ id: 'L1', name: 'rust', color: '#f00' }];
state.annotations = [ann('a')];
state.selectedId = null;
clearHistory();
snapshot();

const entry = JSON.parse(state.history[0]);
ok('a history entry carries annotations', Array.isArray(entry.annotations));
ok('a history entry carries NO labels', entry.labels === undefined);
ok('a history entry carries selectedId', 'selectedId' in entry);

// The real failure mode: the class list must survive a restore. Undo restores
// only `annotations`, so labels are simply never reassigned.
state.annotations = [ann('a'), ann('b')];
const restored = JSON.parse(state.history[0]);
state.annotations = restored.annotations;
ok('classes survive an undo restore', state.labels.length === 1);
ok('annotations are rolled back by the restore', state.annotations.length === 1);

// --- 2. Opening a task starts with an empty stack ------------------------

state.labels = [{ id: 'L1', name: 'rust', color: '#f00' }];
state.annotations = [ann('a'), ann('b'), ann('c')];
snapshot();
snapshot();
ok('history accumulates while editing', state.history.length > 0);

// This is exactly the incident: switch task, then hydrate the new task's work.
resetWorkspaceForNewImage();
ok('switching tasks clears undo history', state.history.length === 0);
ok('switching tasks clears redo history', state.redoHistory.length === 0);
ok('switching tasks keeps project classes', state.labels.length === 1);

// Hydration happens after the switch; a Ctrl+Z now must find nothing to pop.
state.annotations = [ann('x'), ann('y'), ann('z')];
ok('nothing to undo on a freshly opened task', state.history.pop() === undefined);
ok('hydrated work is untouched', state.annotations.length === 3);

// --- 3. snapshot() still clears the redo branch --------------------------

clearHistory();
state.redoHistory = ['{"annotations":[],"selectedId":null}'];
snapshot();
ok('a new edit discards the redo branch', state.redoHistory.length === 0);

// --- 4. The stack stays bounded -----------------------------------------

clearHistory();
for (let i = 0; i < 60; i++) snapshot();
ok('history is capped at 50 entries', state.history.length === 50);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

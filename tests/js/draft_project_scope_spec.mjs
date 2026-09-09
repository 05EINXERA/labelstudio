/**
 * Behaviour spec for the draft's project scoping.
 *
 * Run: node tests/js/draft_project_scope_spec.mjs (or via
 * tests/test_draft_project_scope.py)
 *
 * A task can now be moved between projects (.devnotes/move-task-feature/).
 * Label rows are per project, so the server remaps every stored annotation's
 * label_id as the task goes — but the per-task draft in this browser still
 * carries the *source* project's ids.
 *
 * Restoring such a draft discards the correctly remapped set that was just
 * fetched, and the next autosave writes the stale ids back, where the server
 * can only orphan them: label_id NULL, original preserved in
 * `extra._orphanedLabelId`, every shape rendering as an unnamed "Object". That
 * is what happened to task 1229 in production.
 *
 * The other half of this spec matters just as much: the draft is the save-loss
 * safety net (.devnotes/deployment-hardening/04_ANNOTATION_SAVE_LOSS.md), and a
 * fix that made it refuse *more* than the mismatch case would trade a rare bug
 * for a common one. So the permissive cases are pinned too.
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
const { draftMatchesProject, state, draftKey, legacyDraftKey } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

// --- the bug this closes ----------------------------------------------------

{
  // Task 1229's shape exactly: drafted under project 270 (annotation), the
  // task now lives in 285 (moved).
  ok('a draft from another project is refused',
     draftMatchesProject('270', '285') === false);
  ok('the same project is allowed',
     draftMatchesProject('285', '285') === true);
}

{
  // The canvas reads projectId from the URL (a string); a draft may have been
  // written when it was stored as a number. A type difference is not a
  // different project, and treating it as one would refuse every draft.
  ok('a numeric id matches its string form', draftMatchesProject(270, '270') === true);
  ok('a string id matches its numeric form', draftMatchesProject('270', 270) === true);
  ok('genuinely different ids still mismatch across types',
     draftMatchesProject(270, '285') === false);
}

// --- what must NOT change ---------------------------------------------------

{
  // Drafts written before this field existed carry no projectId. Refusing them
  // would break recovery for every task with pending work at upgrade time —
  // trading a rare bug for a common one, on the safety net itself.
  ok('a draft with no recorded project is still restored',
     draftMatchesProject(undefined, '285') === true);
  ok('an explicitly null draft project is still restored',
     draftMatchesProject(null, '285') === true);
}

{
  // The canvas can be opened without ?projectId=. With nothing to compare
  // against, the draft must still win — that is the pre-existing behaviour and
  // the whole point of the net.
  ok('no open project means the draft is still restored',
     draftMatchesProject('270', undefined) === true);
  ok('a null open project still restores', draftMatchesProject('270', null) === true);
  ok('neither side known still restores',
     draftMatchesProject(undefined, undefined) === true);
}

// --- the surrounding contract is untouched ----------------------------------

{
  // The keys are what make the draft per-task and per-origin. Nothing in this
  // change may alter them: a changed key orphans every pending draft at once.
  ok('the draft key is still per task and per origin',
     draftKey(1229) === 'annotation-draft-v1:http://test:1229');
  ok('the legacy key is unchanged', legacyDraftKey(1229) === 'annotation-draft-v1:1229');
}

{
  // The new field has to exist on state for saveDraft to stamp it, and must
  // start null so a canvas opened without a project writes "unknown" rather
  // than a stale value from a previous page.
  ok('state carries a projectId, defaulting to null', state.projectId === null);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

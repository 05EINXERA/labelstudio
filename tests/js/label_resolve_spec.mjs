/**
 * Behaviour spec for `resolveAnnotationLabels` — the class resolver that must
 * never create a class.
 *
 * Run: node tests/js/label_resolve_spec.mjs (or via tests/test_label_resolve.py)
 *
 * On 2026-09-08 six project-wide classes named "object" were created in
 * project 410 by nobody deciding to create a class. Opening a task ran
 * `repairLabelsFromAnnotations`, which called `ensureLabel(...)` — a POST to
 * /api/labels — for every annotation whose `labelId` was not in the restored
 * label set. Four of the six landed inside 37 seconds of reloading one task,
 * with `delta=0` on every save: nothing was drawn, nothing was changed.
 * See .devnotes/fix-class-creation/01_AUDIT.md.
 *
 * The invariant this spec exists to pin: **an unresolvable labelId is stale
 * data, not a new class.** The resolver may repoint an annotation at a class
 * that already exists; it may never invent one, and it may never mutate the
 * label set. Every assertion below is either that invariant or one of the
 * legitimate recoveries that must keep working.
 *
 * state.js touches window/localStorage at import time and pulls in view.js,
 * which constructs an Image(); all three are stubbed, as in
 * draft_project_scope_spec.mjs.
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
const { resolveAnnotationLabels } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

const LABELS = [
  { id: 'lbl-rust', name: 'rust area', color: '#111' },
  { id: 'lbl-crack', name: 'crack', color: '#222' },
];

// --- the bug this closes ----------------------------------------------------

{
  // Task 1194's shape: hand-drawn annotations (no detectedClass) whose labelId
  // is not in the project's class set. The old code created one class per
  // unresolved id, named "object" because detectedClass was absent.
  const labels = LABELS.map((l) => ({ ...l }));
  const before = labels.length;
  const out = resolveAnnotationLabels(
    [{ id: 'a1', labelId: 'gone-1' }, { id: 'a2', labelId: 'gone-2' }],
    labels
  );

  ok('an unresolvable labelId creates no class', labels.length === before);
  ok('the label set is not mutated at all',
     JSON.stringify(labels) === JSON.stringify(LABELS));
  ok('an unresolvable labelId is left untouched, not nulled',
     out[0].labelId === 'gone-1' && out[1].labelId === 'gone-2');
  ok('every annotation survives', out.length === 2);
}

{
  // The specific 2026-09-08 payload: many annotations, all unresolvable. The
  // old code fired one POST per distinct id; 624 objects on task 1194.
  const labels = [];
  const anns = Array.from({ length: 50 }, (_, i) => ({ id: `a${i}`, labelId: `x${i}` }));
  const out = resolveAnnotationLabels(anns, labels);
  ok('50 unresolvable ids against an empty class set create nothing',
     labels.length === 0 && out.length === 50);
}

// --- legitimate recovery that must keep working -----------------------------

{
  // The case the function was actually written for: a detector wrote the class
  // name, the class exists in the project, only the id is stale.
  const out = resolveAnnotationLabels(
    [{ id: 'a1', labelId: 'stale', detectedClass: 'rust area' }],
    LABELS
  );
  ok('a detectedClass naming an existing class is repointed',
     out[0].labelId === 'lbl-rust');
}

{
  // labelByName normalises; the resolver must too, or an import that wrote
  // "Rust_Area" would look unresolvable against the stored "rust area".
  const out = resolveAnnotationLabels(
    [
      { id: 'a1', labelId: 'stale', detectedClass: 'Rust_Area' },
      { id: 'a2', labelId: 'stale', detectedClass: '  CRACK  ' },
    ],
    LABELS
  );
  ok('detectedClass matching is case-insensitive and underscore-normalised',
     out[0].labelId === 'lbl-rust');
  ok('detectedClass matching trims and lowercases',
     out[1].labelId === 'lbl-crack');
}

{
  // A detectedClass naming a class the project does NOT have is the tempting
  // case — it looks like a class worth creating. It is not: the server's set is
  // authoritative, and creating from a per-tab draft is the whole bug.
  const labels = LABELS.map((l) => ({ ...l }));
  const out = resolveAnnotationLabels(
    [{ id: 'a1', labelId: 'stale', detectedClass: 'pothole' }],
    labels
  );
  ok('an unknown detectedClass creates nothing', labels.length === 2);
  ok('an unknown detectedClass leaves the id alone', out[0].labelId === 'stale');
}

// --- what must NOT change ---------------------------------------------------

{
  // A resolvable annotation must pass through by identity, not be rebuilt: the
  // canvas compares annotation sets by JSON to decide whether a task is dirty
  // (annotationsChangedSinceHydration), so gratuitous copying would make an
  // untouched task look edited and trigger a save.
  const ann = { id: 'a1', labelId: 'lbl-rust', points: [[1, 2]] };
  const out = resolveAnnotationLabels([ann], LABELS);
  ok('a resolvable annotation is returned by identity', out[0] === ann);
}

{
  // A comment carries no labelId at all. It must not be treated as unresolved
  // and must certainly not acquire one.
  const out = resolveAnnotationLabels(
    [{ id: 'c1', type: 'comment', text: 'check this' }],
    LABELS
  );
  ok('a comment is left alone', out[0].labelId === undefined);
}

{
  // An empty set, and an empty class set, must both be no-ops rather than
  // throwing — restoreDraft runs this before the project's classes have
  // necessarily arrived.
  ok('no annotations is a no-op', resolveAnnotationLabels([], LABELS).length === 0);
  ok('no classes is a no-op',
     resolveAnnotationLabels([{ id: 'a1', labelId: 'x' }], []).length === 1);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);

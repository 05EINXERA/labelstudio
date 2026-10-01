/**
 * Behaviour spec for serialising the canvas once per save (F2).
 *
 * Run: node tests/js/serialize_once_spec.mjs  (or via tests/test_serialize_once.py)
 *
 * A save used to serialise the same annotation array three to four times (the
 * demotion check, the nothing-to-save check, the payload, the post-save
 * fingerprint), ~100 ms of blocked main thread each on a large task. Callers
 * now build the string once and hand it on. The change is only safe if the
 * string-taking forms answer *exactly* as the array-taking forms did, so that
 * is what this pins — most importantly the fail-safe direction: anything that
 * cannot be proven unchanged reads as changed.
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

const s = await import(new URL('../../frontend/js/state.js', import.meta.url));
const { serializeAnnotations, noteHydratedAnnotations, annotationsChangedSinceHydration } = s;

let pass = 0, fail = 0;
const ok = (name, cond, extra = '') => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name, extra));
};
const box = (id, x) => ({ id, type: 'box', labelId: 'L1', x, y: 0, width: 10, height: 10 });

// 1. serializeAnnotations is JSON.stringify, with the same null/undefined rule.
{
  const a = [box('a', 0), box('b', 5)];
  ok('serialises like JSON.stringify', serializeAnnotations(a) === JSON.stringify(a));
  ok('null serialises as an empty set', serializeAnnotations(null) === '[]');
  ok('undefined serialises as an empty set', serializeAnnotations(undefined) === '[]');
  const cyclic = [{ id: 'x' }]; cyclic[0].self = cyclic[0];
  ok('an unserialisable set yields null, not a throw', serializeAnnotations(cyclic) === null);
}

// 2. Passing the string gives the same answer as passing only the array.
{
  const server = [box('a', 0), box('b', 50)];
  noteHydratedAnnotations(server);
  const cases = {
    'unchanged': server,
    'moved': [box('a', 999), box('b', 50)],
    'added': [...server, box('c', 90)],
    'emptied': [],
  };
  for (const [name, arr] of Object.entries(cases)) {
    const viaArray = annotationsChangedSinceHydration(arr);
    const viaJson = annotationsChangedSinceHydration(arr, serializeAnnotations(arr));
    ok(`change check agrees with and without the string: ${name}`, viaArray === viaJson);
  }
  ok('unchanged reads as unchanged', annotationsChangedSinceHydration(server, serializeAnnotations(server)) === false);
  ok('moved reads as changed', annotationsChangedSinceHydration(cases.moved, serializeAnnotations(cases.moved)) === true);
}

// 3. Recording a baseline from the string equals recording it from the array.
{
  const edited = [box('a', 1), box('b', 2)];
  noteHydratedAnnotations(edited, serializeAnnotations(edited));
  ok('baseline from a string: the same set is unchanged',
     annotationsChangedSinceHydration(edited) === false);
  ok('baseline from a string: a different set is changed',
     annotationsChangedSinceHydration([box('a', 9), box('b', 2)]) === true);
}

// 4. Fail-safe: an unserialisable baseline or current set reads as CHANGED.
{
  const server = [box('a', 0)];
  noteHydratedAnnotations(server);
  const cyclic = [{ id: 'x' }]; cyclic[0].self = cyclic[0];
  ok('an unserialisable current set reads as changed',
     annotationsChangedSinceHydration(cyclic) === true);
  ok('a null string reads as changed (cannot prove unchanged)',
     annotationsChangedSinceHydration(server, null) === true);
  noteHydratedAnnotations(server, null);
  ok('a null baseline means unknown, so everything reads as changed',
     annotationsChangedSinceHydration(server) === true);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

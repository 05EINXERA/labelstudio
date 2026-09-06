/**
 * Behaviour spec for the in-flight save guard.
 *
 * Run: node tests/js/save_coalesce_spec.mjs  (or via tests/test_save_coalesce.py)
 *
 * What this protects: overlapping saves of one task were costing the server
 * ~200 MB of live Python objects each (a measured 17x JSON→object
 * amplification), and CPython's stop-the-world gen-2 collection then stalled
 * every other request. Production showed 84 pairs of saves for one task from
 * one tab less than a second apart.
 *
 * The risk in fixing it is losing work, so most of these tests are about what
 * must NOT be suppressed, and about the guard never latching on.
 * See .devnotes/fix-save-coalesce/01_PLAN.md.
 */
const url = new URL('../../frontend/js/save-coalesce.js', import.meta.url);
const c = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

/** A send() whose promise this test resolves by hand. */
function deferred() {
  let resolve, reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

const tick = () => new Promise((r) => setTimeout(r, 0));

// 1. The basic guard: a second automatic save does not hit the network.
{
  c._resetForTests();
  let calls = 0;
  const d = deferred();
  const send = () => { calls++; return d.promise; };

  const first = c.coalesce(7, send);
  const second = c.coalesce(7, send);

  ok('one request while a save is in flight', calls === 1);
  ok('the task reports in-flight', c.isSaveInFlight(7) === true);
  ok('a folded caller gets a promise, not false', second !== false);
  ok('a folded caller shares the in-flight promise', second === first);

  d.resolve('saved');
  await tick(); await tick();
  ok('exactly one follow-up runs after settle', calls === 2);
}

// 2. Many folded calls produce exactly ONE follow-up, not one each.
{
  c._resetForTests();
  let calls = 0;
  let current = deferred();
  const send = () => { calls++; current = deferred(); return current.promise; };

  c.coalesce(9, send);
  const held = current;
  c.coalesce(9, send);
  c.coalesce(9, send);
  c.coalesce(9, send);
  ok('four calls, one request', calls === 1);

  held.resolve('ok');
  await tick(); await tick();
  ok('four calls produce one follow-up, not three', calls === 2);
}

// 3. The follow-up sends CURRENT state, not the suppressed snapshot.
{
  c._resetForTests();
  const sent = [];
  let state = 'v1';
  let current = deferred();
  const send = () => { sent.push(state); current = deferred(); return current.promise; };

  c.coalesce(11, send);
  const held = current;
  state = 'v2';
  c.coalesce(11, send);   // suppressed; would have sent v2
  state = 'v3';           // more edits arrive while #1 is still in flight

  held.resolve('ok');
  await tick(); await tick();
  ok('follow-up sends the newest state', sent.join(',') === 'v1,v3');
}

// 4. Different tasks never block each other.
{
  c._resetForTests();
  let calls = 0;
  const send = () => { calls++; return deferred().promise; };
  c.coalesce(1, send);
  c.coalesce(2, send);
  c.coalesce(3, send);
  ok('separate tasks each send', calls === 3);
  ok('in-flight count tracks tasks', c.inFlightCount() === 3);
}

// 5. bypass is never suppressed — beacons, Save button, status changes.
{
  c._resetForTests();
  let calls = 0;
  const d = deferred();
  const send = () => { calls++; return d.promise; };

  c.coalesce(4, send);
  c.coalesce(4, send, { bypass: true });
  ok('a bypass save is sent even while one is in flight', calls === 2);

  c._resetForTests();
  calls = 0;
  c.coalesce(5, send, { bypass: true });
  ok('bypass does not register as in-flight', c.isSaveInFlight(5) === false);
}

// 6. The guard must clear on failure, or one failed save locks the task out
//    of autosaving for the rest of the session.
{
  c._resetForTests();
  let calls = 0;
  const d = deferred();
  const send = () => { calls++; return d.promise; };

  const p = c.coalesce(6, send);
  p.catch(() => {});
  d.reject(new Error('network down'));
  await tick(); await tick();

  ok('guard clears after a rejected save', c.isSaveInFlight(6) === false);
  let rejected = false;
  await p.catch(() => { rejected = true; });
  ok('the failure still reaches the caller', rejected === true);

  const d2 = deferred();
  c.coalesce(6, () => { calls++; return d2.promise; });
  ok('the task can save again after a failure', calls === 2);
}

// 7. A synchronous throw from send() must not latch the guard either.
{
  c._resetForTests();
  const p = c.coalesce(8, () => { throw new Error('boom'); });
  let rejected = false;
  await p.catch(() => { rejected = true; });
  ok('a synchronous throw rejects', rejected === true);
  ok('a synchronous throw leaves no in-flight entry', c.isSaveInFlight(8) === false);
}

// 8. A folded caller sees the in-flight save's failure rather than a silent
//    success — it was told "your save is this one".
{
  c._resetForTests();
  const d = deferred();
  const first = c.coalesce(12, () => d.promise);
  const folded = c.coalesce(12, () => d.promise);
  first.catch(() => {}); folded.catch(() => {});
  d.reject(new Error('nope'));
  let foldedRejected = false;
  await folded.catch(() => { foldedRejected = true; });
  ok('a folded caller sees the failure', foldedRejected === true);
}

// 9. Task ids are normalised: 7 and "7" are the same task, or a numeric id
//    from the gallery and a string id from a dataset attribute would each get
//    their own in-flight slot and overlap exactly as before.
{
  c._resetForTests();
  let calls = 0;
  const d = deferred();
  const send = () => { calls++; return d.promise; };
  c.coalesce(13, send);
  c.coalesce('13', send);
  ok('numeric and string ids share one slot', calls === 1);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

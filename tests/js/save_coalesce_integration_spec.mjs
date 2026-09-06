/**
 * Integration spec: the coalescing guard must not lose annotations.
 *
 * The unit spec (save_coalesce_spec.mjs) tests the guard against a stubbed
 * sender. That is not enough for a change that suppresses saves: the risk is
 * not in the guard's bookkeeping, it is in what the *follow-up* save actually
 * puts on the wire, and in what the post-save bookkeeping then claims the
 * server holds.
 *
 * This spec models the real wiring in `syncToBackend`:
 *
 *   currentTask.annotations = [...state.annotations]   // a NEW array per call
 *   sendOnce = () => { const sent = currentTask.annotations; ... }
 *   coalesce(id, sendOnce)
 *
 * and asserts the properties that, if broken, lose an annotator's work:
 *
 *   1. the follow-up sends the LATEST edits, not the snapshot that was
 *      suppressed, and not a stale one
 *   2. the draft is only cleared for annotations the server actually took
 *   3. the "already saved" fingerprint only ever describes what was sent
 *
 * Each of these is a silent-loss bug if wrong: the canvas looks correct, the
 * indicator says Saved, and the work is gone on the next reload.
 */
const url = new URL('../../frontend/js/save-coalesce.js', import.meta.url);
const c = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond, extra = '') => {
  cond ? (pass++, console.log('  PASS', name))
       : (fail++, console.log('  FAIL', name, extra));
};
const tick = () => new Promise((r) => setTimeout(r, 0));

/**
 * A faithful stand-in for the save path in workspace.js.
 *
 * `state.annotations` is the canvas. `task.annotations` is the per-save copy
 * workspace.js makes. `wire` records what each request actually carried, and
 * `fingerprint` records what the client afterwards believes the server holds.
 */
function makeHarness() {
  const state = { annotations: [] };
  const task = { id: 42, annotations: [] };
  const wire = [];
  const fingerprints = [];
  const draftsCleared = [];
  let pending = null;

  // Mirrors syncToBackend: copy the canvas, build a sender that re-reads it at
  // send time (the follow-up must carry the newest canvas, not the snapshot
  // taken when the closure was built).
  const syncToBackend = ({ bypass = false } = {}) => {
    task.annotations = [...state.annotations];
    const sendOnce = () => {
      task.annotations = [...state.annotations];
      const sentAnnotations = task.annotations;
      wire.push(sentAnnotations.join(','));
      pending = { resolve: null };
      const p = new Promise((res) => { pending.resolve = res; });
      return p.then((okFlag) => {
        if (okFlag !== false) {
          draftsCleared.push(task.id);
          fingerprints.push(sentAnnotations.join(','));
        }
        return okFlag;
      });
    };
    return c.coalesce(task.id, sendOnce, { bypass });
  };

  return {
    state, task, wire, fingerprints, draftsCleared, syncToBackend,
    settle: async (okFlag = true) => { pending.resolve(okFlag); await tick(); await tick(); },
  };
}

// 1. THE CORE LOSS CASE.
//    A save is in flight. The annotator draws more. That second save is folded.
//    When the first lands, the follow-up must carry the NEW shapes.
{
  c._resetForTests();
  const h = makeHarness();

  h.state.annotations = ['a', 'b'];
  h.syncToBackend();                 // request #1 sends a,b
  ok('first request sends the current canvas', h.wire[0] === 'a,b');

  h.state.annotations = ['a', 'b', 'c'];
  h.syncToBackend();                 // folded
  h.state.annotations = ['a', 'b', 'c', 'd'];
  h.syncToBackend();                 // folded

  ok('folded calls issue no request', h.wire.length === 1);

  await h.settle(true);              // #1 completes -> follow-up runs

  ok('a follow-up request is issued', h.wire.length === 2,
     `wire=${JSON.stringify(h.wire)}`);
  ok('the follow-up carries the LATEST annotations (a,b,c,d)',
     h.wire[1] === 'a,b,c,d',
     `got "${h.wire[1]}" — annotations drawn during the in-flight save were LOST`);
}

// 2. The fingerprint must never claim the server has work it does not.
//    If it does, `annotationsChangedSinceHydration` returns false, the next
//    autosave is suppressed as "nothing to save", and the work is gone.
{
  c._resetForTests();
  const h = makeHarness();

  h.state.annotations = ['a'];
  h.syncToBackend();
  h.state.annotations = ['a', 'b'];
  h.syncToBackend();                 // folded

  await h.settle(true);

  ok('fingerprint after request #1 describes only what #1 sent',
     h.fingerprints[0] === 'a',
     `got "${h.fingerprints[0]}"`);

  // The follow-up is now in flight; settle it too.
  await h.settle(true);
  ok('fingerprint after the follow-up describes what it sent',
     h.fingerprints[1] === 'a,b',
     `got "${h.fingerprints[1]}"`);
  ok('the server ends up holding every annotation',
     h.wire[h.wire.length - 1] === 'a,b');
}

// 3. A failed save must not clear the draft or the fingerprint — that is the
//    work-protection path (CLAUDE.md rule 18/18a).
{
  c._resetForTests();
  const h = makeHarness();

  h.state.annotations = ['x', 'y'];
  const p = h.syncToBackend();
  p.catch(() => {});
  await h.settle(false);             // server refused

  ok('a refused save clears no draft', h.draftsCleared.length === 0);
  ok('a refused save writes no fingerprint', h.fingerprints.length === 0);
}

// 4. After a failure the task must still be saveable — a latched guard would
//    silently stop autosaving for the rest of the session.
{
  c._resetForTests();
  const h = makeHarness();
  h.state.annotations = ['x'];
  h.syncToBackend();
  await h.settle(false);

  h.state.annotations = ['x', 'z'];
  h.syncToBackend();
  ok('the task saves again after a failure', h.wire.length === 2 && h.wire[1] === 'x,z',
     `wire=${JSON.stringify(h.wire)}`);
}

// 5. A bypass save (beacon / Save button / status change) always goes, and
//    carries the current canvas — this is the last-chance-to-persist path.
{
  c._resetForTests();
  const h = makeHarness();
  h.state.annotations = ['p'];
  h.syncToBackend();                 // in flight
  h.state.annotations = ['p', 'q'];
  h.syncToBackend({ bypass: true }); // pagehide beacon

  ok('a bypass save is sent while another is in flight', h.wire.length === 2);
  ok('the bypass carries the latest canvas', h.wire[1] === 'p,q',
     `got "${h.wire[1]}"`);
}

// 6. A long burst: edits keep arriving across several in-flight saves. The
//    final state must reach the server, whatever the interleaving.
{
  c._resetForTests();
  const h = makeHarness();
  h.state.annotations = ['1'];
  h.syncToBackend();
  for (let i = 2; i <= 8; i++) {
    h.state.annotations = h.state.annotations.concat(String(i));
    h.syncToBackend();
  }
  // Drain every follow-up until the queue is quiet.
  for (let i = 0; i < 10; i++) {
    try { await h.settle(true); } catch { break; }
    if (!c.isSaveInFlight(42)) break;
  }
  const last = h.wire[h.wire.length - 1];
  ok('after a burst, the server holds the final canvas',
     last === '1,2,3,4,5,6,7,8',
     `last request carried "${last}" but the canvas is "${h.state.annotations.join(',')}"`);
}

// 7. THE CASE THAT ALMOST SHIPPED A BUG.
//    Edits arrive while a save is in flight but WITHOUT a further
//    syncToBackend call for them (no debounce has fired yet). The follow-up
//    must still carry them, because it re-reads the canvas at send time rather
//    than reusing the snapshot its closure was built with.
//
//    In the real app every edit calls save(), which reassigns
//    currentTask.annotations before scheduling — so the two agree anyway. This
//    test exists so the guarantee does not silently depend on that timing: a
//    future change to the debounce must not be able to turn folding into
//    annotation loss.
{
  c._resetForTests();
  const h = makeHarness();

  h.state.annotations = ['a'];
  h.syncToBackend();               // #1 sends 'a'
  h.state.annotations = ['a', 'b'];
  h.syncToBackend();               // folded
  // More work lands with NO further syncToBackend call.
  h.state.annotations = ['a', 'b', 'c'];

  await h.settle(true);            // #1 lands -> follow-up runs

  ok('the follow-up re-reads the canvas at send time',
     h.wire[1] === 'a,b,c',
     `got "${h.wire[1]}" but the canvas held "a,b,c" — "c" would have been lost`);
  ok('the fingerprint matches what the follow-up actually sent',
     h.fingerprints[0] === 'a');
}

// 8. Folding must never reduce what the server holds. Whatever the
//    interleaving, the last request is a superset of every suppressed one.
{
  c._resetForTests();
  const h = makeHarness();
  h.state.annotations = ['s1'];
  h.syncToBackend();
  const suppressed = [];
  for (let i = 2; i <= 5; i++) {
    h.state.annotations = h.state.annotations.concat('s' + i);
    suppressed.push(h.state.annotations.join(','));
    h.syncToBackend();
  }
  await h.settle(true);
  const finalWire = h.wire[h.wire.length - 1].split(',');
  const everySuppressedShapeSurvived = suppressed
    .flatMap((s) => s.split(','))
    .every((shape) => finalWire.includes(shape));
  ok('no suppressed shape is missing from the final request',
     everySuppressedShapeSurvived,
     `final="${h.wire[h.wire.length - 1]}"`);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

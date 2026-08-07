/**
 * Behaviour spec for frontend/js/offline-queue.js — the outbox that holds task
 * writes the server has not taken and replays them on reconnect.
 * See .devnotes/offline/01_OFFLINE_RESILIENCE_PLAN.md.
 *
 * Run: node tests/js/offline_queue_spec.mjs   (or via tests/test_offline_queue.py)
 *
 * The invariant most worth guarding is delta accumulation: `time_spent_delta` is
 * incremental, so re-queuing must add to the pending delta while annotations
 * (absolute) replace. Getting that backwards silently loses or double-bills time.
 */

// Minimal browser shims so offline-queue.js can be exercised under node.
const store = new Map();
globalThis.localStorage = {
  getItem: k => store.has(k) ? store.get(k) : null,
  setItem: (k,v) => store.set(k,String(v)),
  removeItem: k => store.delete(k),
  get length(){ return store.size; },
  key: i => [...store.keys()][i],
};
globalThis.window = { addEventListener(){}, location:{ origin:'http://x' } };

// Resolved relative to this file so the spec is runnable directly
// (`node tests/js/offline_queue_spec.mjs`) as well as via pytest.
const queueUrl = new URL('../../frontend/js/offline-queue.js', import.meta.url);
const q = await import(queueUrl);

let pass=0, fail=0;
const ok=(name,cond)=>{ cond?(pass++,console.log('  PASS',name)):(fail++,console.log('  FAIL',name)); };

// 1. delta accumulates across repeated failures; annotations replace.
q.enqueueWrite({id:1, time_spent_delta:10, annotations:'["a"]'});
q.enqueueWrite({id:1, time_spent_delta:5,  annotations:'["a","b"]'});
const e = q.peekWrite(1);
ok('delta accumulates (10+5=15)', e.payload.time_spent_delta===15);
ok('annotations replaced (newest wins)', e.payload.annotations==='["a","b"]');
ok('last-write-wins: one entry per task', q.pendingCount()===1);
ok('queuedAt preserved from first failure', typeof e.queuedAt==='number');
ok('attempts counted', e.attempts===2);

// 2. separate tasks are separate entries
q.enqueueWrite({id:2, time_spent_delta:3, annotations:'[]'});
ok('second task queued separately', q.pendingCount()===2);

// 3. successful drain clears entries and banks deltas
const sent=[];
q.configureQueue({ send: async p => { sent.push(p); return {ok:true, updated_at:'T'}; } });
const flushed = await q.drainQueue();
ok('drain flushed both', flushed===2);
ok('queue empty after drain', q.pendingCount()===0);
ok('replayed delta is the accumulated 15', sent.find(p=>p.id===1).time_spent_delta===15);
ok('oldest-first replay order', sent[0].id===1 && sent[1].id===2);

// 4. transport failure keeps the entry and stops the pass
q.enqueueWrite({id:3, time_spent_delta:1, annotations:'[]'});
q.enqueueWrite({id:4, time_spent_delta:1, annotations:'[]'});
q.configureQueue({ send: async () => ({ok:false}) });
await q.drainQueue();
ok('transport failure retains both', q.pendingCount()===2);
// Regression: a single failed drain pass must already report unreachable. The
// write being replayed had failed once to get queued, so waiting for a second
// backoff cycle left the warning off screen for up to a minute — the exact
// "nobody told the annotators" failure this module exists to prevent.
ok('one failed drain pass reports unreachable', q.isServerUnreachable());

// 5. a 409 routes to the conflict handler, is not retried away, and is kept
let conflicted=null;
q.configureQueue({ send: async()=>({ok:false,conflict:true}), onConflict:(id)=>{conflicted=id;} });
await q.drainQueue();
ok('conflict handler invoked', conflicted===3||conflicted===4);
ok('conflicted write still queued (work preserved)', q.pendingCount()===2);
ok('a live 409 clears unreachable', q.isServerUnreachable()===false);

// 6. discardWrite
q.discardWrite(3); q.discardWrite(4);
ok('discardWrite empties queue', q.pendingCount()===0);

// 7. corrupt storage degrades to empty rather than throwing
store.set('pending-writes-v1','{not json');
ok('unreadable queue -> 0, no throw', q.pendingCount()===0);
store.set('pending-writes-v1','[1,2,3]');
ok('array-shaped queue rejected', q.pendingCount()===0);

// 8. The incident itself, end to end: the server's IP changes under an
//    annotator who is working through several images, then they reconnect.
store.clear();
{
  let serverUp = false;
  const accepted = [];
  q.configureQueue({
    send: async (p) => {
      if (!serverUp) throw new Error('ECONNREFUSED'); // stale IP: no answer at all
      accepted.push(p);
      return { ok: true, updated_at: '2026-07-30T12:00:00Z' };
    },
    onConflict: () => {},
  });

  for (const [taskId, secs] of [[11, 60], [12, 90], [13, 45], [14, 30]]) {
    q.enqueueWrite({
      id: taskId,
      time_spent_delta: secs,
      annotations: JSON.stringify([{ id: `a${taskId}` }]),
      status: 'In Progress',
      updated_at: '2026-07-30T10:00:00Z',
      client_id: 'browser-A',
    });
  }
  // They keep editing the last task: two further failed saves on task 14.
  q.enqueueWrite({ id: 14, time_spent_delta: 20, annotations: JSON.stringify([{ id: 'a14' }, { id: 'b14' }]), client_id: 'browser-A' });
  q.enqueueWrite({ id: 14, time_spent_delta: 10, annotations: JSON.stringify([{ id: 'a14' }, { id: 'b14' }, { id: 'c14' }]), client_id: 'browser-A' });

  ok('outage: 4 tasks held', q.pendingCount() === 4);
  await q.drainQueue();
  ok('outage: drain loses nothing', q.pendingCount() === 4);

  // Persistence, not memory, is what survives the reload the annotator does
  // after being pointed at the new address.
  const persisted = JSON.parse(store.get('pending-writes-v1'));
  ok('outage: survives reload via localStorage', Object.keys(persisted).length === 4);
  ok('outage: task 14 delta accumulated 30+20+10', persisted['14'].payload.time_spent_delta === 60);
  ok('outage: task 14 kept newest 3 shapes', JSON.parse(persisted['14'].payload.annotations).length === 3);

  serverUp = true;
  const flushed = await q.drainQueue();
  ok('reconnect: all 4 replayed', flushed === 4);
  ok('reconnect: queue empty', q.pendingCount() === 0);
  ok('reconnect: no time lost or double-billed (255s)',
     accepted.reduce((s, p) => s + p.time_spent_delta, 0) === 255);
  ok('reconnect: oldest work first', accepted[0].id === 11);
  ok('reconnect: client_id preserved so conflict detection still works',
     accepted.every(p => p.client_id === 'browser-A'));
}

// 9. E-27: a queued write that 403s on replay.
//    Teams made this reachable — a grant can be revoked while work sits in the
//    outbox. A 403 is a real answer from a healthy server, so it must not be
//    retried like a network failure, and it must never cost the annotator their
//    work. See .devnotes/teams/06_EDGE_CASES.md E-27.
store.clear();
{
  let attempts = 0;
  const reported = [];
  q.configureQueue({
    send: async () => {
      attempts += 1;
      return { ok: false, forbidden: true, detail: 'This task is assigned to Alpha.' };
    },
    onConflict: () => { throw new Error('a 403 must not be routed to the conflict handler'); },
    onForbidden: (taskId, payload, detail) => reported.push({ taskId, detail }),
  });

  q.enqueueWrite({
    id: 21,
    time_spent_delta: 40,
    annotations: JSON.stringify([{ id: 'a21' }]),
    client_id: 'browser-A',
  });

  await q.drainQueue();

  ok('403: the draft is retained, not dropped', q.pendingCount() === 1);
  ok('403: reported to the user once', reported.length === 1);
  ok('403: the server message is passed through', reported[0].detail === 'This task is assigned to Alpha.');

  const persisted = JSON.parse(store.get('pending-writes-v1'));
  ok('403: marked so the retry loop skips it', persisted['21'].forbidden === true);
  ok('403: the payload itself is intact', JSON.parse(persisted['21'].payload.annotations).length === 1);
  ok('403: work is not silently lost', persisted['21'].payload.time_spent_delta === 40);

  // A second drain must not hammer a server that already said no.
  const attemptsAfterFirst = attempts;
  await q.drainQueue();
  ok('403: not retried forever', attempts === attemptsAfterFirst);

  // ...and it must not be mistaken for the server being unreachable, which
  // would show the offline banner against a perfectly healthy server.
  ok('403: does not mark the server unreachable', q.isServerUnreachable() === false);
}

// 10. A 422 refusal ("this save would erase existing annotations") shares the
//     403 machinery — stop retrying, keep the payload — but must be reported as
//     its own thing. Conflating the two told annotators working normally on
//     their own tasks, against a server that never went down, that they lacked
//     permission and that their "offline work" was stranded. All three claims
//     were false. The `refused` flag is what keeps the wording honest.
store.clear();
{
  const reported = [];
  q.configureQueue({
    send: async () => ({
      ok: false,
      forbidden: true,
      refused: true,
      detail: 'Refusing to clear existing annotations.',
    }),
    onConflict: () => { throw new Error('a 422 must not be routed to the conflict handler'); },
    onForbidden: (taskId, payload, detail, opts) => reported.push({ taskId, detail, opts }),
  });

  q.enqueueWrite({
    id: 31,
    time_spent_delta: 15,
    annotations: JSON.stringify([]),
    client_id: 'browser-A',
  });

  await q.drainQueue();

  ok('422: reported once', reported.length === 1);
  ok('422: flagged as refused, not as a permission failure',
     reported[0].opts && reported[0].opts.refused === true);

  const persisted = JSON.parse(store.get('pending-writes-v1'));
  ok('422: the payload is kept, exactly as for a 403', persisted['31'] !== undefined);
  ok('422: marked refused in storage', persisted['31'].refused === true);
  ok('422: stops being retried', persisted['31'].forbidden === true);
  ok('422: does not mark the server unreachable', q.isServerUnreachable() === false);

  // The stranded entry must not be counted as retryable work, or the save
  // indicator reports unsaved changes forever against a healthy server.
  ok('422: excluded from the retryable count', q.retryablePendingCount() === 0);
  ok('422: still present in the raw total', q.pendingCount() === 1);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail?1:0);

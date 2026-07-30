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

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail?1:0);

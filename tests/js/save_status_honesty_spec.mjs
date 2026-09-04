/**
 * Behaviour spec for the save indicator's honesty guarantees.
 *
 * Guards the two defects in .devnotes/network-lag/03_FALSE_SAVED_STATUS.md that
 * let the canvas report "Saved" while the server held nothing:
 *
 *   B1 — a lock heartbeat (a ~200-byte request) cleared the save-failure count,
 *        so the red banner disappeared while a 1.8 MB write was still stuck in
 *        the queue. The annotator was told the problem had resolved.
 *   B3 — a successful *time-only* save called discardWrite() unconditionally,
 *        dropping queued annotation work and emptying the queue, which made the
 *        resting status read "Saved".
 *
 * The invariant under test, stated once: the indicator may only say saves are
 * healthy on the evidence of an actual save. Anything else — a heartbeat, an
 * unrelated small write — is not evidence, and treating it as such is the bug.
 *
 * Run: node tests/js/save_status_honesty_spec.mjs
 */

const store = new Map();
globalThis.localStorage = {
  getItem: k => store.has(k) ? store.get(k) : null,
  setItem: (k,v) => store.set(k,String(v)),
  removeItem: k => store.delete(k),
  get length(){ return store.size; },
  key: i => [...store.keys()][i],
};
globalThis.window = { addEventListener(){}, location:{ origin:'http://x' } };

const queueUrl = new URL('../../frontend/js/offline-queue.js', import.meta.url);
const q = await import(queueUrl);

let pass=0, fail=0;
const ok=(name,cond)=>{ cond?(pass++,console.log('  PASS',name)):(fail++,console.log('  FAIL',name)); };

const reset = () => { store.clear(); q.noteServerReachable({ fromSave: true }); };

// ── B1: a heartbeat must not clear the save warning ─────────────────────────

reset();
// Two failed saves cross UNREACHABLE_AFTER_FAILURES, so the banner is showing.
q.enqueueWrite({ id: 1, time_spent_delta: 5, annotations: '["a"]' });
q.noteServerUnreachable();
q.noteServerUnreachable();
ok('B1 setup: two failed saves raise the warning', q.isServerUnreachable() === true);

// The lock heartbeat succeeds — a small request over a link too degraded to
// carry the save. This is the exact sequence that cleared the banner.
q.noteServerReachable();
ok('B1: heartbeat success does NOT clear the save warning',
   q.isServerUnreachable() === true);
ok('B1: the write is still queued', q.retryablePendingCount() === 1);

// Only a real save outcome may retire it.
q.noteServerReachable({ fromSave: true });
ok('B1: a real save outcome DOES clear the warning',
   q.isServerUnreachable() === false);

// The positive case matters as much as the negative: a banner that never clears
// is ignored, which would defeat the purpose of fixing it.
reset();
q.noteServerUnreachable();
q.noteServerUnreachable();
q.noteServerReachable({ fromSave: true });
ok('B1: banner still clears on a genuine successful save',
   q.isServerUnreachable() === false);

// ── B3: a time-only save must not discard queued annotation work ────────────

reset();
// The annotator's real work fails to save and lands in the queue.
q.enqueueWrite({ id: 7, time_spent_delta: 30, annotations: '["obj1","obj2"]' });
ok('B3 setup: annotation work is queued', q.retryablePendingCount() === 1);

// A *time-only* save then succeeds. It carries no `annotations` key at all —
// that omission is meaningful (the server reads it as "leave the stored set
// alone"), so it supersedes nothing about the annotations.
q.discardWrite(7, { supersededBy: { id: 7, time_spent_delta: 10 } });
ok('B3: time-only save does NOT drop the queued annotation write',
   q.retryablePendingCount() === 1);
ok('B3: the queued annotations are intact',
   q.peekWrite(7).payload.annotations === '["obj1","obj2"]');

// The seconds that save banked must not be replayed a second time.
ok('B3: banked seconds are netted off, not double-counted',
   q.peekWrite(7).payload.time_spent_delta === 20);

// A save that DID carry the full annotation set legitimately supersedes it.
q.discardWrite(7, { supersededBy: { id: 7, time_spent_delta: 20, annotations: '["obj1","obj2","obj3"]' } });
ok('B3: a save carrying annotations DOES supersede the queued write',
   q.retryablePendingCount() === 0);

// Netting off must never manufacture a negative delta, which would subtract
// time from the task on the next replay.
reset();
q.enqueueWrite({ id: 8, time_spent_delta: 5, annotations: '["x"]' });
q.discardWrite(8, { supersededBy: { id: 8, time_spent_delta: 999 } });
ok('B3: over-large banked delta floors at zero, never negative',
   q.peekWrite(8).payload.time_spent_delta === 0);

// Back-compat: callers that pass no payload still drop unconditionally. The
// conflict-overwrite path in init.js relies on this.
reset();
q.enqueueWrite({ id: 9, time_spent_delta: 1, annotations: '["y"]' });
q.discardWrite(9);
ok('B3: no-payload discardWrite still drops unconditionally',
   q.pendingCount() === 0);

// ── The combined failure the annotator actually reported ────────────────────

reset();
// Large save fails; work is queued and the banner comes up.
q.enqueueWrite({ id: 12, time_spent_delta: 60, annotations: '["big"]' });
q.noteServerUnreachable();
q.noteServerUnreachable();
// Heartbeats keep succeeding for half an hour...
for (let i = 0; i < 60; i++) q.noteServerReachable();
// ...and small time-only saves keep succeeding too.
q.discardWrite(12, { supersededBy: { id: 12, time_spent_delta: 5 } });

ok('combined: warning survives 30 minutes of healthy heartbeats',
   q.isServerUnreachable() === true);
ok('combined: the annotation work is still queued, not silently dropped',
   q.retryablePendingCount() === 1);
ok('combined: indicator cannot report a clean state',
   q.retryablePendingCount() > 0);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);

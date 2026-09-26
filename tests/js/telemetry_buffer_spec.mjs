/**
 * Spec for the telemetry ring buffer (frontend/js/telemetry/buffer.js).
 *
 * Run: node tests/js/telemetry_buffer_spec.mjs
 *
 * The buffer is what keeps a server outage from growing browser memory
 * without bound: past the cap the OLDEST record goes, and the loss is
 * counted so the report can tell "quiet" from "dropped".
 */
const url = new URL('../../frontend/js/telemetry/buffer.js?v=1', import.meta.url);
const { createRing } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

const r = createRing(3);
[1, 2, 3, 4, 5].forEach((n) => r.push(n));
ok('capped at 3', r.size === 3);
ok('overflow counted', r.dropped === 2);
let batch = r.take();
ok('oldest dropped, newest kept', JSON.stringify(batch.records) === '[3,4,5]');
ok('take reports drops', batch.dropped === 2);
ok('take empties', r.size === 0 && r.dropped === 0);

// A failed flush goes back to the head, ahead of newer records.
r.push(6);
r.putBack(batch);
ok('putBack restores order ahead of newer', JSON.stringify(r.take().records) === '[4,5,6]');

// putBack still respects the cap, and keeps the drop count.
const r2 = createRing(2);
r2.push('new');
r2.putBack({ records: ['a', 'b'], dropped: 1 });
const b2 = r2.take();
ok('putBack trims oldest to cap', JSON.stringify(b2.records) === '["b","new"]');
ok('putBack carries and adds drops', b2.dropped === 2);

const r3 = createRing(5);
r3.noteDropped(4);
r3.noteDropped(-3);
ok('noteDropped adds, ignores negatives', r3.dropped === 4);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

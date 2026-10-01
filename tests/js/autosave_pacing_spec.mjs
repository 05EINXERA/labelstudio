/**
 * Behaviour spec for adaptive autosave spacing.
 *
 * Run: node tests/js/autosave_pacing_spec.mjs  (or via tests/test_autosave_pacing.py)
 *
 * What this protects: the pacer may only ever make an automatic save LATER, by
 * a bounded amount. The properties worth pinning are therefore (a) that it is
 * invisible on a healthy server, (b) that it can never exceed the 10 s ceiling
 * the operator chose, and (c) that, composed with the real in-flight guard, a
 * burst of edits under a slow server collapses into few saves and the final
 * one still carries the newest state. See .devnotes/fix-performance-upgrade/.
 */
const pacing = await import(new URL('../../frontend/js/autosave-pacing.js', import.meta.url));
const coalesceMod = await import(new URL('../../frontend/js/save-coalesce.js', import.meta.url));

let pass = 0, fail = 0;
const ok = (name, cond, extra = '') => {
  cond ? (pass++, console.log('  PASS', name))
       : (fail++, console.log('  FAIL', name, extra));
};

function makePacer() {
  const clock = { t: 1_000_000 };
  const pacer = pacing.createPacer({ now: () => clock.t });
  return { pacer, clock };
}

// 1. The constants are the decision, not an accident.
ok('ceiling is the 10 s the operator chose', pacing.MAX_GAP_MS === 10_000);
ok('floor and debounce are 1 s (today\'s behaviour)',
   pacing.MIN_GAP_MS === 1000 && pacing.DEBOUNCE_MS === 1000);

// 2. Invisible before any measurement and for a never-saved task.
{
  const { pacer } = makePacer();
  ok('no measurement: gap is the floor', pacer.gapMs() === 1000);
  ok('never-saved task waits only the debounce', pacer.delayFor(1) === 1000);
  ok('unknown task id waits only the debounce', pacer.delayFor(null) === 1000);
}

// 3. Healthy server: behaves exactly as before.
{
  const { pacer, clock } = makePacer();
  pacer.observe(300);
  ok('0.3 s round trip: gap clamps up to the 1 s floor', pacer.gapMs() === 1000);
  pacer.noteStart(5);
  clock.t += 400;
  ok('0.4 s after a save: still just the debounce', pacer.delayFor(5) === 1000);
  clock.t += 5000;
  ok('long after a save: just the debounce', pacer.delayFor(5) === 1000);
}

// 4. Slow server: waits for the gap, never past the ceiling.
{
  const { pacer, clock } = makePacer();
  pacer.observe(4000);
  ok('4 s round trip puts the gap at the 10 s ceiling', pacer.gapMs() === 10_000);
  pacer.noteStart(5);
  clock.t += 2000;
  ok('2 s after a save the next waits the remaining 8 s', pacer.delayFor(5) === 8000);
  clock.t += 7000;
  ok('9 s after: only the debounce is left', pacer.delayFor(5) === 1000);
}

// 5. Hard ceiling regardless of how bad the server gets.
{
  const { pacer, clock } = makePacer();
  pacer.observe(120_000);
  ok('a 2-minute round trip is still capped at 10 s', pacer.gapMs() === 10_000);
  pacer.noteStart(9);
  ok('wait right after a save is never more than the ceiling',
     pacer.delayFor(9) <= 10_000);
  clock.t += 1;
  ok('...and stays bounded as time passes', pacer.delayFor(9) <= 10_000);
}

// 6. The gap tracks the server: it relaxes again once the server recovers.
{
  const { pacer } = makePacer();
  for (let i = 0; i < 5; i++) pacer.observe(4000);
  ok('after sustained slowness the gap is at the ceiling', pacer.gapMs() === 10_000);
  for (let i = 0; i < 20; i++) pacer.observe(300);
  ok('after sustained recovery it is back at the floor', pacer.gapMs() === 1000);
}

// 7. Smoothing: one outlier does not throw the gap to the ceiling.
{
  const { pacer } = makePacer();
  for (let i = 0; i < 10; i++) pacer.observe(400);
  pacer.observe(6000);                       // a single stall
  ok('one slow save moves the gap but not to the ceiling',
     pacer.gapMs() > 1000 && pacer.gapMs() < 10_000);
}

// 8. Bad input is ignored, never poisons the state.
{
  const { pacer } = makePacer();
  pacer.observe(NaN); pacer.observe(-5); pacer.observe(Infinity); pacer.observe('x');
  ok('non-finite / negative round trips are ignored', pacer.gapMs() === 1000
     && pacer.smoothedRttMs() === null);
}

// 9. Per-task spacing: one task's save does not delay another's.
{
  const { pacer, clock } = makePacer();
  pacer.observe(4000);
  pacer.noteStart('a');
  clock.t += 1000;
  ok('another task is not held back by it', pacer.delayFor('b') === 1000);
  ok('ids are normalised (7 and "7" are one task)',
     (pacer.noteStart(7), pacer.delayFor('7') > 1000));
}

// 10. forget() releases a task.
{
  const { pacer } = makePacer();
  pacer.observe(4000);
  pacer.noteStart(3);
  pacer.forget(3);
  ok('a forgotten task waits only the debounce', pacer.delayFor(3) === 1000);
}

// 11. Composed with the real in-flight guard under a slow server: a long burst
//     of edits becomes a handful of saves, and the LAST one carries the newest
//     state. This is the property that matters for losing work.
{
  const { pacer, clock } = makePacer();
  coalesceMod._resetForTests();
  const state = { annotations: [] };
  const wire = [];                      // what each request actually carried
  const SERVER_MS = 4000;
  const pending = [];                   // [{at, fire}] timers, driven by hand

  // `sendOnce` mirrors workspace.js: re-read the canvas at send time.
  const sendOnce = () => {
    const sent = state.annotations.join(',');
    wire.push(sent);
    pacer.noteStart(1);
    const startedAt = clock.t;
    return new Promise((resolve) => {
      pending.push({ at: clock.t + SERVER_MS, fire: () => {
        pacer.observe(clock.t - startedAt);
        resolve(true);
      } });
    });
  };

  let timer = null;                     // the single trailing debounce
  const edit = (value) => {
    state.annotations.push(value);
    timer = { at: clock.t + pacer.delayFor(1), fire: () => coalesceMod.coalesce(1, sendOnce) };
  };

  // Drive a virtual clock: an edit every 1.5 s for 60 s, timers firing in order.
  const events = [];
  for (let i = 0; i < 40; i++) events.push({ at: 1_000_000 + i * 1500, edit: i });
  let idx = 0;
  while (idx < events.length || timer || pending.length) {
    const candidates = [];
    if (idx < events.length) candidates.push(['edit', events[idx].at]);
    if (timer) candidates.push(['timer', timer.at]);
    for (const p of pending) candidates.push(['pending', p.at]);
    candidates.sort((a, b) => a[1] - b[1]);
    const [kind, at] = candidates[0];
    clock.t = Math.max(clock.t, at);
    if (kind === 'edit') edit(events[idx++].edit);
    else if (kind === 'timer') { const t = timer; timer = null; t.fire(); }
    else {
      const i = pending.findIndex((p) => p.at === at);
      const [p] = pending.splice(i, 1); p.fire();
      await new Promise((r) => setTimeout(r, 0));   // let promise chains settle
    }
  }
  const newest = state.annotations.join(',');
  ok('saves are far fewer than edits (40 edits)', wire.length < 20, `saves=${wire.length}`);
  ok('the final save carries every edit', wire[wire.length - 1] === newest);
  ok('no save ever carried more than what had been drawn',
     wire.every((w) => newest.startsWith(w)));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

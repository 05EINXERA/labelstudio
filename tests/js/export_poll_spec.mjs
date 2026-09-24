/**
 * Behaviour spec for the export status poller.
 *
 * Run: node tests/js/export_poll_spec.mjs
 *
 * `frontend/js/pages/project/export-poll.js` replaced a 1 s `setInterval`
 * that kept firing while earlier polls were unanswered: eleven were in flight
 * from one page during the 2026-09-24 stall
 * (.devnotes/fix-exports-imports/02_ISSUES.md I-5).
 *
 * The load-bearing property is the first one: never two polls in flight,
 * however slow the server is. The backoff and the hidden-tab pause come after.
 * Timers are injected, so the spec drives time by hand and never sleeps.
 */
const url = new URL('../../frontend/js/pages/project/export-poll.js?v=1', import.meta.url);
const { createPoller, nextDelay, POLL_DELAYS_MS } = await import(url);

let pass = 0, fail = 0;
function ok(name, cond) {
  if (cond) { pass++; } else { fail++; console.log(`FAIL: ${name}`); }
}

/** A hand-driven clock: timers only fire when `tick` says so. */
function fakeTimers() {
  const pending = [];
  return {
    pending,
    setTimer(fn, ms) { const t = { fn, ms }; pending.push(t); return t; },
    clearTimer(t) { const i = pending.indexOf(t); if (i >= 0) pending.splice(i, 1); },
    async fireNext() { const t = pending.shift(); if (t) { t.fn(); } await flush(); return t; },
  };
}
const flush = () => new Promise((r) => setImmediate(r));

// --- the delay schedule --------------------------------------------------
{
  ok('first retry waits 1 s', nextDelay(1) === 1000);
  ok('then 2 s', nextDelay(2) === 2000);
  ok('then 3 s', nextDelay(3) === 3000);
  ok('then 5 s', nextDelay(4) === 5000);
  ok('and stays at 5 s', nextDelay(50) === 5000);
  ok('the cap is the last entry', POLL_DELAYS_MS[POLL_DELAYS_MS.length - 1] === 5000);
}

// --- one poll in flight, however slow the server --------------------------
{
  const clock = fakeTimers();
  let calls = 0, maxInFlight = 0, inFlight = 0;
  const resolvers = [];
  const poller = createPoller({
    poll: () => { calls++; inFlight++; maxInFlight = Math.max(maxInFlight, inFlight);
      return new Promise((r) => resolvers.push(() => { inFlight--; r(true); })); },
    setTimer: clock.setTimer, clearTimer: clock.clearTimer,
  });
  poller.start();
  await flush();
  ok('start polls immediately', calls === 1);
  ok('nothing is scheduled while a poll is unanswered', clock.pending.length === 0);
  // Time passes, the server has not answered: still one call.
  await flush(); await flush();
  ok('a slow reply does not trigger a second poll', calls === 1);
  resolvers.shift()();
  await flush();
  ok('the next poll is scheduled only after the reply', clock.pending.length === 1);
  ok('...after 1 s', clock.pending[0].ms === 1000);
  await clock.fireNext();
  ok('the scheduled poll runs', calls === 2);
  ok('never more than one in flight', maxInFlight === 1);
  poller.stop();
  resolvers.shift()();
  await flush();
  ok('a reply arriving after stop schedules nothing', clock.pending.length === 0);
}

// --- backoff across polls ------------------------------------------------
{
  const clock = fakeTimers();
  const poller = createPoller({ poll: async () => true, setTimer: clock.setTimer, clearTimer: clock.clearTimer });
  poller.start();
  await flush();
  const seen = [];
  for (let i = 0; i < 6; i++) { seen.push(clock.pending[0].ms); await clock.fireNext(); }
  ok('delays back off 1,2,3,5,5,5', seen.join(',') === '1000,2000,3000,5000,5000,5000');
  poller.stop();
}

// --- finishing and errors -------------------------------------------------
{
  const clock = fakeTimers();
  let calls = 0;
  const poller = createPoller({ poll: async () => { calls++; return calls < 3; },
    setTimer: clock.setTimer, clearTimer: clock.clearTimer });
  poller.start();
  await flush();
  await clock.fireNext();
  await clock.fireNext();
  ok('poll returning false stops the poller', calls === 3 && clock.pending.length === 0);
  ok('and it reports inactive', poller.active === false);
}
{
  const clock = fakeTimers();
  let calls = 0;
  const poller = createPoller({ poll: async () => { calls++; throw new Error('network'); },
    setTimer: clock.setTimer, clearTimer: clock.clearTimer });
  poller.start();
  await flush();
  ok('a failed poll keeps polling (a blip must not strand the job)', clock.pending.length === 1);
  poller.stop();
  ok('stop clears the pending timer', clock.pending.length === 0);
}

// --- hidden tab ----------------------------------------------------------
{
  const clock = fakeTimers();
  let hidden = false, calls = 0;
  const poller = createPoller({ poll: async () => { calls++; return true; }, isHidden: () => hidden,
    setTimer: clock.setTimer, clearTimer: clock.clearTimer });
  poller.start();
  await flush();
  hidden = true;
  await clock.fireNext();
  ok('a hidden tab does not poll', calls === 1);
  ok('and schedules nothing while hidden', clock.pending.length === 0);
  poller.resume();
  ok('resume while still hidden does nothing', calls === 1);
  hidden = false;
  poller.resume();
  await flush();
  ok('showing the tab polls again at once', calls === 2);
  ok('and the schedule restarts from 1 s', clock.pending[0]?.ms === 1000);
  poller.resume();
  await flush();
  ok('resume on an already-running poller does not double it', calls === 2 && clock.pending.length === 1);
  poller.stop();
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

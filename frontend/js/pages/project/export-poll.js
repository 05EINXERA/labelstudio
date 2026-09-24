/**
 * Polling schedule for a running export job. Pure: no DOM, no fetch.
 *
 * Replaces `setInterval(poll, 1000)`, which fired every second whether or not
 * the previous request had come back. On 2026-09-24, when an export had
 * starved the server, one page had eleven status polls in flight at once, and
 * the extra load landed exactly when the server could least afford it
 * (.devnotes/fix-exports-imports/02_ISSUES.md I-5). This schedule:
 *
 *  - never has more than one poll in flight: the next is scheduled only after
 *    the previous one settles;
 *  - backs off 1 s -> 2 s -> 3 s -> 5 s and stays at 5 s, so a long export
 *    costs one request per 5 s rather than one per second;
 *  - pauses while the tab is hidden and resumes when it is shown again.
 *
 * Tested by tests/js/export_poll_spec.mjs.
 */

export const POLL_DELAYS_MS = [1000, 2000, 3000, 5000];

/** Delay before poll number `attempt` (0-based; attempt 0 runs immediately). */
export function nextDelay(attempt) {
  const i = Math.min(Math.max(attempt - 1, 0), POLL_DELAYS_MS.length - 1);
  return POLL_DELAYS_MS[i];
}

/**
 * @param {object} opts
 * @param {() => Promise<boolean>} opts.poll  one status check; resolve true to
 *        keep polling, false when the job has finished (or polling must stop).
 *        A rejection is treated as "keep polling": a network blip must not
 *        strand a job the user is waiting on.
 * @param {() => boolean} [opts.isHidden]  whether the page is hidden.
 * @param {Function} [opts.setTimer] / [opts.clearTimer]  injectable timers.
 */
export function createPoller({ poll, isHidden = () => false, setTimer = setTimeout, clearTimer = clearTimeout }) {
  let attempt = 0;
  let timer = null;
  let inFlight = false;
  let stopped = true;
  let paused = false;

  async function run() {
    timer = null;
    if (stopped) return;
    if (isHidden()) {
      paused = true;
      return;
    }
    inFlight = true;
    let keepGoing = true;
    try {
      keepGoing = await poll();
    } catch (err) {
      keepGoing = true;
    } finally {
      inFlight = false;
    }
    if (stopped || keepGoing === false) {
      stopped = true;
      return;
    }
    attempt += 1;
    timer = setTimer(run, nextDelay(attempt));
  }

  return {
    start() {
      this.stop();
      stopped = false;
      paused = false;
      attempt = 0;
      run();
    },
    stop() {
      stopped = true;
      paused = false;
      if (timer !== null) {
        clearTimer(timer);
        timer = null;
      }
    },
    /** Call on visibilitychange: restarts a poller that paused while hidden. */
    resume() {
      if (stopped || !paused || inFlight || timer !== null || isHidden()) return;
      paused = false;
      attempt = 0;
      run();
    },
    get inFlight() { return inFlight; },
    get active() { return !stopped; },
  };
}

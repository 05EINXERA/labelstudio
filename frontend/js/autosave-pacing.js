/**
 * Adaptive spacing for automatic saves.
 *
 * Every save uploads the task's complete annotation set and the server
 * re-reads all of it, so its cost does not depend on how much was edited. With
 * ~20 annotators that cost saturated the server's one core, and nothing pushed
 * back: `save-coalesce.js` stops two saves of one task overlapping, but the
 * moment one settles the next is free to start. A slow server therefore got
 * *more* waiting requests, not fewer, and latency fed on itself
 * (.devnotes/fix-performance-upgrade/01_PROFILE_ANALYSIS.md).
 *
 * This is the missing feedback. The wait before an automatic save is
 *
 *     max(debounce,  lastSaveStart + gap  -  now)
 *     gap = clamp(GAP_FACTOR × smoothed save round trip,  MIN_GAP_MS,  MAX_GAP_MS)
 *
 * so on a healthy server (round trip ≈ 0.3 s) the gap is the 1 s floor and
 * behaviour is exactly what it was; as the server slows, clients back off on
 * their own, up to a hard ceiling of 10 s (the operator's decision,
 * 2026-10-01), which is the most unsaved work a tab can hold before the next
 * automatic save.
 *
 * Why folding the skipped saves cannot lose work: the payload is absolute —
 * every save carries the complete annotation set — so the one save that does
 * go out is a superset of every save it replaced. Same argument as
 * save-coalesce.js, and the same three exemptions apply (the caller keeps them,
 * not this module): a beacon, a user-initiated save and an explicit status
 * change never wait. The wait only ever applies to the debounced autosave.
 *
 * Pure and DOM-free so it can be unit-tested (tests/js/autosave_pacing_spec.mjs).
 */

/** The wait that was always there: trailing debounce after the last edit. */
export const DEBOUNCE_MS = 1000;
/** Never space automatic saves closer than this. */
export const MIN_GAP_MS = 1000;
/** Never space them further than this — the most work a tab may hold unsaved. */
export const MAX_GAP_MS = 10_000;
/** How many round trips apart saves are kept. 2.5× puts a 4 s round trip at the cap. */
export const GAP_FACTOR = 2.5;
/** Weight of the newest round trip in the smoothed value. */
export const SMOOTHING = 0.3;

/**
 * @param {object} [opts]
 * @param {() => number} [opts.now]  Clock, injectable for tests.
 */
export function createPacer({
  now = () => Date.now(),
  minGapMs = MIN_GAP_MS,
  maxGapMs = MAX_GAP_MS,
  gapFactor = GAP_FACTOR,
  smoothing = SMOOTHING,
} = {}) {
  /** @type {number|null} Smoothed round trip of successful saves, ms. */
  let smoothedRtt = null;
  /** taskId → when its last save started, ms. */
  const lastStart = new Map();

  function clamp(value) {
    return Math.min(maxGapMs, Math.max(minGapMs, value));
  }

  return {
    /**
     * Record how long a successful save took. Only successes count: a failure
     * measures the network, not the server's load, and the offline queue
     * already governs how failed writes are retried.
     */
    observe(rttMs) {
      if (!Number.isFinite(rttMs) || rttMs < 0) return;
      smoothedRtt = smoothedRtt === null
        ? rttMs
        : smoothing * rttMs + (1 - smoothing) * smoothedRtt;
    },

    /** The current minimum spacing between automatic saves of one task, ms. */
    gapMs() {
      return smoothedRtt === null ? minGapMs : clamp(gapFactor * smoothedRtt);
    },

    /** The smoothed round trip, or null before the first success. Diagnostics. */
    smoothedRttMs() {
      return smoothedRtt;
    },

    /** Mark a save of this task as having just started. */
    noteStart(taskId) {
      if (taskId == null) return;
      lastStart.set(String(taskId), now());
    },

    /**
     * How long to wait before the next automatic save of this task.
     * Never less than `debounceMs`, so a healthy server sees today's behaviour.
     */
    delayFor(taskId, debounceMs = DEBOUNCE_MS) {
      const started = taskId == null ? undefined : lastStart.get(String(taskId));
      if (started === undefined) return debounceMs;
      const untilGap = started + this.gapMs() - now();
      return Math.max(debounceMs, untilGap);
    },

    /** Forget a task (closed, or its saves are no longer ours to pace). */
    forget(taskId) {
      lastStart.delete(String(taskId));
    },

    /** Reset everything. Tests only. */
    _reset() {
      smoothedRtt = null;
      lastStart.clear();
    },
  };
}

/** The pacer the workspace uses. One per page: server load is not per task. */
export const pacer = createPacer();

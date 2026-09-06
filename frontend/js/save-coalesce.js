/**
 * In-flight guard for task saves.
 *
 * `syncToBackend` is debounced by 1s, which spaces out *scheduling*. Nothing
 * spaced out *execution*: on a large task a save takes 12s, so the next
 * debounce — and the visibilitychange flush, and the 30s timer drain — all fire
 * while the first request is still on the wire. Production showed 84 pairs of
 * saves for one task from one tab less than a second apart, and 26% of all
 * saves arriving within 3s of the previous one for the same task.
 *
 * That is expensive in a way that is invisible from the client: each in-flight
 * save holds its whole annotation set as live Python objects on the server, and
 * the measured JSON→object amplification is **17x** — task 703's 14.5 MB
 * payload becomes 251 MB. Three overlapping saves is ~750 MB for CPython's
 * garbage collector to walk, and gen-2 collection stops the world, so every
 * other request waits behind it (a trivial heartbeat measured 17.6s).
 * See .devnotes/fix-save-coalesce/01_PLAN.md.
 *
 * The fix is to let at most one save per task be in flight, and to fold every
 * request that arrives during it into a single follow-up.
 *
 * **Why folding cannot lose work:** the payload is absolute, not incremental —
 * every save carries the complete annotation set, so a later save is a strict
 * superset of what a suppressed one would have written. This is the same
 * last-write-wins reasoning offline-queue.js already relies on. The one
 * incremental field, `time_spent_delta`, is never touched here: a suppressed
 * call never reaches `drainTaskTime`, which is the only place that reads and
 * zeroes the accumulator, so the seconds stay banked and ride the follow-up.
 *
 * Pure and DOM-free so it can be unit-tested without a browser
 * (tests/js/save_coalesce_spec.mjs).
 */

/** taskId → promise of the save currently in flight. */
const inFlight = new Map();

/** taskIds whose save was suppressed and which therefore need a follow-up. */
const needsFollowUp = new Set();

/**
 * Is a save for this task already on the wire?
 *
 * Exported for callers that want to report state (the save indicator), not
 * just for `coalesce` itself.
 */
export function isSaveInFlight(taskId) {
  return inFlight.has(String(taskId));
}

/** How many tasks currently have a save in flight. Diagnostics only. */
export function inFlightCount() {
  return inFlight.size;
}

/**
 * Reset all state. Tests only — never call this from application code, which
 * would strand a real in-flight request's follow-up.
 */
export function _resetForTests() {
  inFlight.clear();
  needsFollowUp.clear();
}

/**
 * Run `send` for `taskId`, unless a save for it is already in flight.
 *
 * @param {number|string} taskId
 * @param {() => Promise<any>} send  Performs the actual save. Called at most
 *        once per settled generation.
 * @param {object}   [opts]
 * @param {boolean}  [opts.bypass]  Skip the guard entirely and always send.
 *        Set for the three cases that must never be folded — see below.
 * @returns {Promise<any>} The result of this save, or of the in-flight save it
 *        was folded into. Never `false` purely because of suppression: callers
 *        treat `false` as failure and would show the annotator an error for a
 *        save that is, in fact, on its way.
 */
export function coalesce(taskId, send, { bypass = false } = {}) {
  const key = String(taskId);

  // Three cases bypass the guard, for the same reasons the `nothingToSave`
  // gate in workspace.js exempts them:
  //
  //   * a beacon (pagehide / visibilitychange) — the last chance to persist
  //     before the tab dies. There is no "later" to fold into, so durability
  //     beats efficiency.
  //   * a user-initiated save — the user pressed Save and is watching for an
  //     answer.
  //   * an explicit status change ("Save as Complete", Approve, Reject) — a
  //     stated intent carrying a status the follow-up would not know to send.
  //
  // The caller decides which of these applies; this module only honours the
  // flag, so the policy stays next to the context that knows it.
  if (bypass) return Promise.resolve(send());

  const current = inFlight.get(key);
  if (current) {
    // Fold into the in-flight save. The follow-up below will pick up whatever
    // the state is when it runs, which is newer than the snapshot this call
    // would have sent — so suppressing it loses nothing.
    needsFollowUp.add(key);
    return current;
  }

  return _start(key, send);
}

function _start(key, send) {
  let promise;
  try {
    promise = Promise.resolve(send());
  } catch (err) {
    // A synchronous throw must not leave the task permanently marked in-flight,
    // which would suppress every future automatic save for it.
    inFlight.delete(key);
    needsFollowUp.delete(key);
    return Promise.reject(err);
  }

  // `finally` semantics by hand: the guard must clear on rejection too, or one
  // failed save locks the task out of autosaving for the rest of the session.
  const settle = (result, failed) => {
    inFlight.delete(key);
    if (needsFollowUp.delete(key)) {
      // Exactly one follow-up, regardless of how many calls were folded in.
      // Its result is deliberately not propagated to the original callers:
      // they have already been answered by the save they were folded into, and
      // a later failure is reported through the offline queue and the save
      // indicator like any other.
      _start(key, send).catch(() => {});
    }
    if (failed) throw result;
    return result;
  };

  const tracked = promise.then(
    (result) => settle(result, false),
    (err) => settle(err, true),
  );

  // Store the *tracked* promise, so a folded caller waits for the same
  // settlement the guard does rather than resolving one tick earlier.
  inFlight.set(key, tracked);
  return tracked;
}

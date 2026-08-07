/**
 * Offline outbox for task writes.
 *
 * The per-task draft (state.draftKey / workspace.saveDraft) protects the task
 * you are *looking at*: it is restored when that task is reopened. It does
 * nothing for a task you moved on from — which is exactly what was lost when
 * the server's DHCP lease changed under a room full of annotators. See
 * .devnotes/offline/01_OFFLINE_RESILIENCE_PLAN.md (gap G2).
 *
 * This module is the missing half: a queue of writes the server has not taken,
 * across every task, that survives a reload and drains when the server comes
 * back.
 *
 * Shape: a map keyed by task id, not an append log. Every save sends the task's
 * complete annotation set, so an older entry for a task carries nothing the
 * newer one lacks — last-write-wins, and storage growth is bounded by the
 * number of tasks touched rather than the number of failed attempts.
 *
 * The one field that must NOT be overwritten is `time_spent_delta`: it is
 * incremental, so re-queuing accumulates it. Overwriting would silently discard
 * every second accrued between two failed saves.
 */

const QUEUE_KEY = 'pending-writes-v1';

// 5s doubling to a 60s ceiling. The timer — not `navigator.onLine` — is the
// primary recovery mechanism: when a LAN server changes address the NIC stays
// up, so the browser still reports itself online. The `online` event is only
// used to cut a pending wait short.
const RETRY_BASE_MS = 5_000;
const RETRY_MAX_MS = 60_000;

// Replay is attempted only against writes that could still matter. A write
// older than this is kept in storage (never silently dropped — the annotator may
// still want it) but stops being retried, so a stale entry cannot keep the
// "unsaved" banner up forever.
const MAX_REPLAY_AGE_MS = 24 * 60 * 60 * 1000;

/** Injected by init.js so this module needs no import from the timer/workspace
 *  layer (which would be circular: timer.js enqueues into here). */
let sendWrite = null;
let onConflict = null;
let onForbidden = null;

/**
 * @param {object} hooks
 * @param {(payload:object)=>Promise<{ok:boolean, conflict?:boolean, forbidden?:boolean, detail?:string, updated_at?:string}>} hooks.send
 *        Performs one write. Must resolve — never reject — with `conflict:true`
 *        for a 409, `forbidden:true` for a 403, and `ok:false` for anything
 *        else. The 403 case is distinguished because it is not retryable: the
 *        server is healthy and the answer will not change without someone
 *        altering a grant (E-27).
 * @param {(taskId:number, payload:object)=>void} [hooks.onConflict]
 * @param {(taskId:number, payload:object, detail:string)=>void} [hooks.onForbidden]
 */
export function configureQueue({ send, onConflict: conflictHandler, onForbidden: forbiddenHandler } = {}) {
  if (typeof send === 'function') sendWrite = send;
  if (typeof conflictHandler === 'function') onConflict = conflictHandler;
  if (typeof forbiddenHandler === 'function') onForbidden = forbiddenHandler;
}

// ── storage ─────────────────────────────────────────────────────────────────

function readQueue() {
  let raw;
  try {
    raw = localStorage.getItem(QUEUE_KEY);
  } catch (e) {
    // Storage blocked (private mode). The queue degrades to a no-op; the
    // in-memory save path is unaffected.
    return {};
  }
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    // Defend against a hand-edited or partially-written value: a non-object
    // here would throw on every later property access.
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {};
    return parsed;
  } catch (e) {
    console.warn('[offline-queue] discarding unreadable queue', e);
    return {};
  }
}

function writeQueue(queue) {
  try {
    if (Object.keys(queue).length === 0) {
      localStorage.removeItem(QUEUE_KEY);
    } else {
      localStorage.setItem(QUEUE_KEY, JSON.stringify(queue));
    }
    return true;
  } catch (e) {
    // Quota exceeded. Nothing useful to do here but say so loudly — the
    // annotator's safety net just failed, and the status indicator (which reads
    // pendingCount()) will under-report.
    console.error('[offline-queue] could not persist queue', e);
    return false;
  }
}

// ── public state ────────────────────────────────────────────────────────────

const listeners = new Set();

/** Subscribe to queue-size / reachability changes. Returns an unsubscribe fn. */
export function subscribe(fn) {
  if (typeof fn !== 'function') return () => {};
  listeners.add(fn);
  fn(queueState());
  return () => listeners.delete(fn);
}

// Reachability is inferred from write outcomes and the lock heartbeat rather
// than from navigator.onLine, for the reason given at RETRY_BASE_MS.
//
// Two strikes, so one dropped packet or a uvicorn reload does not flash a scary
// banner at every annotator in the room. A replay failure counts as confirmed
// and reaches the threshold immediately (see noteServerUnreachable).
const UNREACHABLE_AFTER_FAILURES = 2;

let consecutiveFailures = 0;
let draining = false;

export function pendingCount() {
  return Object.keys(readQueue()).length;
}

/**
 * Entries that are queued AND still being retried — i.e. excluding forbidden
 * (permission-denied) and conflicted (waiting on a human decision) entries.
 *
 * This is the number that belongs in "Retrying N unsaved changes…": a forbidden
 * entry will never be retried and must not keep the banner open forever against
 * a healthy server (E-27).
 */
export function retryablePendingCount() {
  const queue = readQueue();
  return Object.values(queue).filter((e) => !e.forbidden && !e.conflicted).length;
}

/** True once failures have crossed the threshold at which the UI stops treating
 *  them as a transient blip and tells the user. */
export function isServerUnreachable() {
  return consecutiveFailures >= UNREACHABLE_AFTER_FAILURES;
}

export function queueState() {
  return {
    // The *retryable* count, not the raw one. A forbidden/refused/conflicted
    // entry is deliberately never retried and never expires, so including it
    // here left the save indicator reporting unsaved changes forever against a
    // healthy server — including for work that a later save had already stored
    // successfully. The raw total is still available via pendingCount().
    pending: retryablePendingCount(),
    stranded: pendingCount() - retryablePendingCount(),
    unreachable: isServerUnreachable(),
    draining,
  };
}

function notify() {
  const snapshot = queueState();
  listeners.forEach((fn) => {
    try {
      fn(snapshot);
    } catch (e) {
      console.error('[offline-queue] listener failed', e);
    }
  });
}

/** Record a successful/failed exchange with the server from outside the queue
 *  (the lock heartbeat is a free liveness probe — gap G4). A success both clears
 *  the failure count and is a signal that draining is now worth attempting. */
export function noteServerReachable() {
  const wasUnreachable = isServerUnreachable();
  consecutiveFailures = 0;
  if (wasUnreachable) notify();
  if (pendingCount() > 0) scheduleDrain(0);
}

/**
 * @param {object} [opts]
 * @param {boolean} [opts.confirmed] Set when the failure is already known not to
 *   be a one-off — a replay of a write that had failed before. Such a failure
 *   crosses the reporting threshold on its own instead of waiting for another
 *   backoff cycle, which would keep the warning off screen for up to a minute.
 */
export function noteServerUnreachable({ confirmed = false } = {}) {
  const wasUnreachable = isServerUnreachable();
  consecutiveFailures = confirmed
    ? Math.max(consecutiveFailures + 1, UNREACHABLE_AFTER_FAILURES)
    : consecutiveFailures + 1;
  if (!wasUnreachable && isServerUnreachable()) notify();
}

// ── enqueue ─────────────────────────────────────────────────────────────────

/**
 * Record a write the server did not take.
 *
 * `payload` is the exact body that failed, so replay needs no reconstruction.
 * Only transport failures belong here — a 409 is a real answer from a live
 * server and must go to the conflict handler instead, or the dialog loops.
 */
export function enqueueWrite(payload) {
  if (!payload || !payload.id) return;
  const taskId = String(payload.id);
  const queue = readQueue();
  const existing = queue[taskId];

  // Annotations/status are absolute — the newest wins. The time delta is
  // incremental, so it must accumulate across every failed attempt.
  const pendingDelta = (existing?.payload?.time_spent_delta || 0)
    + (payload.time_spent_delta || 0);

  queue[taskId] = {
    payload: { ...payload, time_spent_delta: pendingDelta },
    // Preserved from the first failure: this is when the work was actually
    // done, which is what MAX_REPLAY_AGE_MS should measure against.
    queuedAt: existing?.queuedAt || Date.now(),
    lastAttemptAt: Date.now(),
    attempts: (existing?.attempts || 0) + 1,
    // The entry is rebuilt rather than merged, so any `conflicted` / `forbidden`
    // flag from a previous attempt is deliberately dropped: re-queuing is how
    // the overwrite path (and a user who regained access) says "try again".
  };

  writeQueue(queue);
  notify();
  scheduleDrain();
}

/** Drop a task's pending write — used when a later save for the same task
 *  succeeds through the normal path, making the queued copy stale. */
export function discardWrite(taskId) {
  if (taskId == null) return;
  const queue = readQueue();
  if (!(String(taskId) in queue)) return;
  delete queue[String(taskId)];
  writeQueue(queue);
  notify();
}

/** The queued payload for a task, or null. Lets the draft-restore path tell
 *  "the server never got this" from "this is merely an old draft". */
export function peekWrite(taskId) {
  if (taskId == null) return null;
  return readQueue()[String(taskId)] || null;
}

// ── drain ───────────────────────────────────────────────────────────────────

let retryTimer = null;
let retryDelay = RETRY_BASE_MS;

function scheduleDrain(delayMs) {
  if (retryTimer) return; // a drain is already pending; don't stack timers
  const delay = delayMs == null ? retryDelay : delayMs;
  retryTimer = setTimeout(() => {
    retryTimer = null;
    drainQueue();
  }, delay);
}

/**
 * Replay every pending write, oldest first.
 *
 * Serial by design: concurrent replays would race on each task's `updated_at`
 * token, and a server that has just come back should not be stampeded.
 *
 * Never rejects. Resolves to the number of writes the server accepted.
 */
export async function drainQueue() {
  if (draining || !sendWrite) return 0;

  const queue = readQueue();
  const taskIds = Object.keys(queue);
  if (taskIds.length === 0) {
    retryDelay = RETRY_BASE_MS;
    return 0;
  }

  draining = true;
  notify();

  // Oldest work first, so a long outage replays in the order it was done.
  taskIds.sort((a, b) => (queue[a].queuedAt || 0) - (queue[b].queuedAt || 0));

  let flushed = 0;
  let sawTransportFailure = false;

  for (const taskId of taskIds) {
    const entry = queue[taskId];
    if (!entry || !entry.payload) {
      delete queue[taskId];
      continue;
    }

    // Too old to replay: keep the record (the annotator may still want to know)
    // but stop retrying so it cannot pin the banner open forever.
    if (Date.now() - (entry.queuedAt || 0) > MAX_REPLAY_AGE_MS) {
      console.warn(`[offline-queue] task ${taskId} write is stale; no longer retrying`);
      continue;
    }

    // Already answered on the merits by a live server, and waiting on a human:
    // a conflict needs the user's overwrite/discard decision, a 403 needs
    // someone to change a grant. Re-sending either just repeats the same
    // refusal. Both stay queued — re-queuing the payload (which enqueueWrite
    // does for the overwrite path) clears the flag and resumes retrying.
    if (entry.forbidden || entry.conflicted) continue;

    let result;
    try {
      result = await sendWrite(entry.payload);
    } catch (e) {
      // configureQueue's contract says send() resolves rather than rejects, but
      // a caller bug must not strand the whole queue.
      result = { ok: false };
    }

    if (result && result.ok) {
      delete queue[taskId];
      flushed += 1;
      consecutiveFailures = 0;
      continue;
    }

    if (result && result.forbidden) {
      // E-27: the server is alive and said no. Retrying cannot help — a grant
      // has to change — and hammering it would hold the offline banner open
      // against a healthy server.
      //
      // The payload is deliberately **kept**. A permission error must never
      // destroy unsaved work (E-08, E-24); the annotator may regain access, or
      // may want to copy the work elsewhere. `forbidden` marks it so the retry
      // loop skips it, exactly as `conflicted` does.
      consecutiveFailures = 0;
      entry.forbidden = true;
      entry.forbiddenDetail = result.detail || '';
      // Distinguishes "refused on the merits" (422) from "you lack permission"
      // (403). Both stop retrying and both keep the payload; only the wording
      // shown to the annotator differs, and getting that wrong reported healthy
      // servers as permission failures.
      entry.refused = !!result.refused;
      entry.lastAttemptAt = Date.now();
      if (onForbidden) {
        try {
          onForbidden(Number(taskId), entry.payload, result.detail || '', {
            refused: !!result.refused,
          });
        } catch (e) {
          console.error('[offline-queue] forbidden handler failed', e);
        }
      }
      continue;
    }

    if (result && result.conflict) {
      // A live server that refused on the merits. Retrying is pointless — the
      // token will not get fresher on its own. Hand it to the UI and stop
      // retrying this task; the payload stays queued so the user's decision
      // (overwrite vs reload) still has the work to act on.
      consecutiveFailures = 0;
      entry.conflicted = true;
      entry.lastAttemptAt = Date.now();
      if (onConflict) {
        try {
          onConflict(Number(taskId), entry.payload);
        } catch (e) {
          console.error('[offline-queue] conflict handler failed', e);
        }
      }
      continue;
    }

    // Transport failure: the server is still unreachable. Abandon the rest of
    // this pass — the remaining writes would fail identically, and hammering a
    // dead address wastes the backoff.
    entry.lastAttemptAt = Date.now();
    sawTransportFailure = true;
    break;
  }

  writeQueue(queue);

  if (sawTransportFailure) {
    // A failed *drain* is not a first blip: the write being replayed had already
    // failed once to get into the queue, so this is the second strike by
    // definition. Reporting it as unconfirmed would leave the banner hidden for
    // another backoff cycle — the "nobody told the annotators" symptom this
    // whole module exists to fix. Ordinary live saves still get their one free
    // failure via the plain call in timer.js.
    noteServerUnreachable({ confirmed: true });
    retryDelay = Math.min(retryDelay * 2, RETRY_MAX_MS);
  } else {
    retryDelay = RETRY_BASE_MS;
  }

  draining = false;
  notify();

  // Anything left that is still worth retrying gets another pass.
  // A forbidden entry is as un-retryable as a conflicted one: both are waiting
  // on a human decision, not on the network.
  if (Object.keys(readQueue()).some((id) => {
    const e = readQueue()[id];
    return !e.conflicted && !e.forbidden;
  })) {
    scheduleDrain();
  }

  return flushed;
}

/** Cancel a pending retry — used on teardown so a drain cannot fire mid-unload. */
export function stopDraining() {
  if (retryTimer) {
    clearTimeout(retryTimer);
    retryTimer = null;
  }
}

// ── triggers ────────────────────────────────────────────────────────────────

/**
 * Wire the automatic recovery triggers. Called once from init.js.
 *
 * The load-time drain is what actually rescues the incident case: an annotator
 * who reloads after being pointed at the new address gets every queued write
 * from every task pushed without touching anything.
 */
export function startQueue() {
  window.addEventListener('online', () => {
    // Cut any pending backoff short — but don't trust this as proof of
    // reachability, only as a hint worth acting on immediately.
    stopDraining();
    retryDelay = RETRY_BASE_MS;
    scheduleDrain(0);
  });

  if (pendingCount() > 0) {
    // Slight delay so the page's own startup requests (labels, task list) go
    // first and the queue isn't competing with the initial render.
    scheduleDrain(1_500);
  }
}

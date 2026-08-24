import { formatTime, clientId } from "../utils.js?v=1";
import { apiFetch } from "../api.js?v=1";
import { timerState } from "../timer-state.js?v=1";
import { canvas } from "../dom.js?v=1";

// Called when the server reports a genuine cross-client conflict. Registered
// by the page so timer.js does not have to know how the workspace wants to
// resolve it (reload, merge, or force-overwrite).
let onConflict = null;

export function setConflictHandler(fn) {
  onConflict = typeof fn === 'function' ? fn : null;
}

const timerToggleBtn = document.getElementById("timerToggleBtn");
const sessionTimerDisplay = document.getElementById("sessionTimerDisplay");
const totalTimeLoggedDisplay = document.getElementById("totalTimeLogged");
const timerResetBtn = document.getElementById("timerResetBtn");
const timerStopBtn = document.getElementById("timerStopBtn");

// Mutable session-timer state. Object-wrapped for the same reason as
// view.js/timer-state.js: these are reassigned, and ES module imports are
// read-only bindings. See .devnotes/refactor/REFACTOR_PLAN.md T9d.
const timerLocalState = {
  timerInterval: null,
  sessionSeconds: 0,
  currentUserForTimer: localStorage.getItem('dataset_username') || 'Unknown',
  // Running count of this page's contribution to the *user's* lifetime total
  // (TeamMember.time_logged). This does NOT drive the "Total" readout, which is
  // per-task — see currentTaskTotalSeconds(). It exists only so the delta sent
  // to /api/team/time can be computed against lastSyncedTotalSeconds.
  totalSeconds: 0,
  lastSyncedTotalSeconds: 0,
  isTimerRunning: false,
  // Wall-clock accounting. setInterval ticks are not 1s apart (background tabs
  // are throttled to 1s..1min), so elapsed time is always derived from
  // Date.now() deltas and the interval only drives repaints. See
  // docs/TIMER_AUDIT.md F1.
  runStartedAt: null,
  accumulatedMs: 0,
  lastTickAt: null,
  // Fractional seconds carried between ticks so nothing is truncated away.
  totalMsCarry: 0
};

// Whether a user-time sync is currently in flight, so the 30s tick and an
// explicit flush cannot double-report the same delta (F4).
let timeSyncInFlight = false;

// Resolves the currently open task, registered by the page so timer.js stays
// independent of workspace state. Returns null when no task is open.
let activeTaskResolver = () => null;

export function setActiveTaskResolver(fn) {
  if (typeof fn === 'function') activeTaskResolver = fn;
}

function currentTaskResolver() {
  try {
    return activeTaskResolver() || null;
  } catch (e) {
    return null;
  }
}

function hasActiveTask() {
  return !!currentTaskResolver();
}

/**
 * Read-and-clear the shared per-task accumulator and POST it to the task.
 * The delta is returned to the accumulator if the request fails, so seconds
 * are retried on the next sync rather than silently lost (F3). This is the
 * single drain point for timerState.taskSessionSeconds (F4).
 */
/** Resolves true when the server accepted the write, false otherwise. */
export async function drainTaskTime(task, { status, annotations, useBeacon = false } = {}) {
  if (!task || !task.id) return false;

  const taskId = task.id;
  const timeDelta = timerState.taskSessionSeconds;
  timerState.taskSessionSeconds = 0;

  const payload = {
    id: taskId,
    time_spent_delta: timeDelta,
    // Sent explicitly as null rather than left undefined when unknown:
    // JSON.stringify drops undefined keys, and an absent updated_at silently
    // disables conflict detection instead of declaring "I have no token".
    updated_at: task.updated_at || null,
    // Lets the server tell our own earlier writes apart from another user's.
    client_id: clientId()
  };

  let nextStatus = status;
  if (!nextStatus && task.status === 'New') {
    nextStatus = 'In Progress';
  }
  if (nextStatus) {
    payload.status = nextStatus;
  }

  if (annotations !== undefined) {
    payload.annotations = JSON.stringify(annotations);
  } else if (task.isFullyLoaded) {
    payload.annotations = JSON.stringify(task.annotations || []);
  }

  // On unload a normal fetch is not guaranteed to be delivered; fetch with keepalive is
  // (F2). This allows us to send standard authorization and CSRF headers.
  if (useBeacon) {
    apiFetch('/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      keepalive: true
    }).catch(() => {});
    
    task.time_spent = (task.time_spent || 0) + timeDelta;
    // Clearing it makes the next save declare "no token"; `client_id` still identifies us
    task.updated_at = null;
    
    updateTimerDisplays();
    // Dispatch is the only signal a beacon gives; treat it as provisional success
    return false;
  }

  try {
    const res = await apiFetch('/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      keepalive: useBeacon || undefined,
      // A heavily-annotated task can take longer than the default 10s to
      // serialize, upload, and be processed server-side (the diff/rewrite
      // loop in _update_or_create_task_impl is per-annotation) — the default
      // was aborting large-but-otherwise-healthy saves and showing an
      // indefinite "Not saved—retrying" with no indication it was a timeout.
      timeoutMs: 45000
    });

    if (res.status === 409) {
      timerState.taskSessionSeconds += timeDelta;
      updateTimerDisplays();

      let code = null, message = null;
      try {
        const body = await res.json();
        if (body && typeof body.detail === 'object') {
          code = body.detail.code;
          message = body.detail.message;
        } else if (body && typeof body.detail === 'string') {
          message = body.detail;
        }
      } catch { /* non-JSON error body */ }

      if (code === 'wipe_guard') {
        // The save was refused because it would have deleted most of the
        // task's annotations — not because another client wrote it. Offering
        // "keep your version" here would just resubmit the same truncated
        // payload and refuse again. Surface it as an error to reload, not a
        // conflict to resolve.
        task.lastSaveError = message || 'Save refused to protect existing annotations. Reload the task.';
        return false;
      }

      // A real conflict: another client wrote this task since we last read it.
      //
      // This used to set `task.id = null` to "prevent further autosaves",
      // which silently disabled saving for the rest of the session — every
      // later edit went to localStorage only and vanished on refresh. A
      // conflict must never cost the user their work, so instead we keep the
      // delta, hand the decision to the user, and leave saving enabled.
      if (onConflict) {
        onConflict(task);
      } else {
        // No handler registered (e.g. a beacon-less background drain): keep
        // the seconds and let the next save retry rather than dropping them.
        console.warn('Task conflict with another client; save deferred.');
      }
      return false;
    }
    if (!res.ok) {
      timerState.taskSessionSeconds += timeDelta;
      updateTimerDisplays();
      // Surface *why* on a rejection the user can act on (e.g. a non-owner
      // trying to change a locked task's status) rather than leaving it to
      // look like a transient "Not saved—retrying" that never explains
      // itself. Best-effort: a malformed error body still leaves lastError
      // unset, and callers already handle that case.
      try {
        const body = await res.json();
        if (body && typeof body.detail === 'string') task.lastSaveError = body.detail;
        else if (body && body.detail && body.detail.message) task.lastSaveError = body.detail.message;
      } catch { /* non-JSON error body; no detail to surface */ }
      return false;
    }
    // The server has banked the delta, so move it from the pending accumulator
    // into the task's stored total. Without this the "Total" readout would drop
    // back by the delta on every sync.
    task.time_spent = (task.time_spent || 0) + timeDelta;

    const data = await res.json();
    if (data && data.updated_at) {
      task.updated_at = data.updated_at;
    }
    updateTimerDisplays();
    return true;
  } catch (e) {
    timerState.taskSessionSeconds += timeDelta;
    updateTimerDisplays();
    if (e && e.name === 'AbortError') {
      task.lastSaveError = 'Save timed out — this task may have too many annotations to save quickly. Retrying…';
    }
    return false;
  }
}

// Back-compat name used by init.js's gallery switch.
export const syncTaskTime = drainTaskTime;

const playSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
const pauseSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>';

/**
 * Total time for the task currently open: what the server has already stored
 * for it, plus the seconds accrued this session that have not been synced yet.
 *
 * This readout used to show the user's lifetime total across every task, which
 * is not what "Total" means in a per-task workspace.
 */
function currentTaskTotalSeconds() {
  const task = currentTaskResolver();
  if (!task) return 0;
  return (task.time_spent || 0) + timerState.taskSessionSeconds;
}

function updateTimerDisplays() {
  if (sessionTimerDisplay) sessionTimerDisplay.textContent = formatTime(timerLocalState.sessionSeconds);
  if (totalTimeLoggedDisplay) totalTimeLoggedDisplay.textContent = formatTime(currentTaskTotalSeconds());
}

// Re-render the readouts after the open task changes (switching images, or a
// sync that folded the pending delta into task.time_spent).
export function refreshTimerDisplays() {
  updateTimerDisplays();
}

/**
 * Begin a fresh session for a newly opened task. Session time is per-task, so
 * it starts at zero; the "Total" readout picks up the new task's stored total
 * via currentTaskResolver().
 */
export function resetSessionForTask() {
  timerLocalState.sessionSeconds = 0;
  timerLocalState.accumulatedMs = 0;
  timerLocalState.totalMsCarry = 0;
  timerLocalState.lastTickAt = Date.now();
  updateTimerDisplays();
}

export function syncTimeToServer({ useBeacon = false } = {}) {
  if (timerLocalState.currentUserForTimer === 'Unknown') return;
  if (timeSyncInFlight) return;

  const syncedUpTo = timerLocalState.totalSeconds;
  const delta = syncedUpTo - timerLocalState.lastSyncedTotalSeconds;
  if (delta <= 0) return;

  const body = JSON.stringify({
    name: timerLocalState.currentUserForTimer,
    time_logged: delta
  });

  if (useBeacon) {
    apiFetch('/api/team/time', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: true
    }).then(res => {
      if (res && res.ok) timerLocalState.lastSyncedTotalSeconds = syncedUpTo;
    }).catch(() => {});
    return;
  }

  // lastSyncedTotalSeconds only advances once the server has accepted the
  // delta; advancing optimistically dropped the seconds on any failure (F3).
  timeSyncInFlight = true;
  apiFetch('/api/team/time', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body
  })
    .then(res => {
      if (res.ok) timerLocalState.lastSyncedTotalSeconds = syncedUpTo;
    })
    .catch(e => console.error('Failed to sync logged time', e))
    .finally(() => { timeSyncInFlight = false; });
}

/**
 * Fold the wall-clock time elapsed since the last tick into every counter.
 * Whole seconds are applied and the remainder carried, so throttled ticks
 * (background tabs) still account for the full interval (F1).
 */
function accrueElapsed() {
  const now = Date.now();
  const elapsedMs = now - timerLocalState.lastTickAt;
  timerLocalState.lastTickAt = now;
  if (elapsedMs <= 0) return;

  timerLocalState.accumulatedMs += elapsedMs;
  timerLocalState.totalMsCarry += elapsedMs;

  const wholeSeconds = Math.floor(timerLocalState.totalMsCarry / 1000);
  if (wholeSeconds > 0) {
    timerLocalState.totalMsCarry -= wholeSeconds * 1000;
    timerLocalState.totalSeconds += wholeSeconds;
    // Only bill a task if one is actually open, otherwise the seconds are
    // credited to whichever task loads next (F8).
    if (hasActiveTask()) {
      timerState.taskSessionSeconds += wholeSeconds;
    }
  }

  timerLocalState.sessionSeconds = Math.floor(timerLocalState.accumulatedMs / 1000);
}

function startTimer() {
  if (timerLocalState.isTimerRunning) return;
  timerLocalState.isTimerRunning = true;
  if (timerToggleBtn) {
    timerToggleBtn.innerHTML = pauseSvg;
    timerToggleBtn.title = "Pause Timer";
  }

  // Re-fetch username in case it changed
  timerLocalState.currentUserForTimer = localStorage.getItem('dataset_username') || 'Unknown';

  timerLocalState.runStartedAt = Date.now();
  timerLocalState.lastTickAt = timerLocalState.runStartedAt;
  noteUserActivity(); // starting the timer is itself activity
  let lastUserSyncAtSecond = timerLocalState.sessionSeconds;

  timerLocalState.timerInterval = setInterval(() => {
    accrueElapsed();

    if (timerLocalState.sessionSeconds - lastUserSyncAtSecond >= 30) {
      lastUserSyncAtSecond = timerLocalState.sessionSeconds;
      syncTimeToServer();
    }

    updateTimerDisplays();
    pauseIfIdle(); // last, so the display reflects the rollback
  }, 1000);
}

function pauseTimer({ useBeacon = false } = {}) {
  if (!timerLocalState.isTimerRunning) return;
  timerLocalState.isTimerRunning = false;
  if (timerToggleBtn) {
    timerToggleBtn.innerHTML = playSvg;
    timerToggleBtn.title = "Start Timer";
  }
  clearInterval(timerLocalState.timerInterval);
  timerLocalState.timerInterval = null;
  accrueElapsed(); // bill the partial interval since the last tick
  timerLocalState.runStartedAt = null;
  updateTimerDisplays();
  syncTimeToServer({ useBeacon }); // final sync on pause
}

if (timerToggleBtn) {
  timerToggleBtn.addEventListener("click", () => {
    if (timerLocalState.isTimerRunning) {
      pauseTimer();
    } else {
      startTimer();
    }
  });
}

/**
 * Reset: discard the time that has not yet reached the server, and only that.
 * Seconds already synced are part of the persisted totals and cannot be taken
 * back from here. Previously this zeroed the display only, while the confirm
 * text promised to clear the session (docs/TIMER_AUDIT.md F5).
 */
if (timerResetBtn) {
  timerResetBtn.addEventListener("click", () => {
    // What is at risk is the time not yet banked against the task.
    const unsynced = timerState.taskSessionSeconds;
    const message = unsynced > 0
      ? `Discard ${formatTime(unsynced)} of unsaved time? Time already saved to this task will be kept.`
      : "Reset the session timer? Time already saved to this task will be kept.";
    if (!confirm(message)) return;

    pauseTimer();
    // Roll the local counter back to the last acknowledged sync point.
    timerLocalState.totalSeconds = timerLocalState.lastSyncedTotalSeconds;
    timerState.taskSessionSeconds = 0;
    timerLocalState.sessionSeconds = 0;
    timerLocalState.accumulatedMs = 0;
    timerLocalState.totalMsCarry = 0;
    updateTimerDisplays();
  });
}

const sessionModal = document.getElementById("sessionModal");
const sessionModalTime = document.getElementById("sessionModalTime");
const sessionClose = document.getElementById("sessionClose");
const sessionOkBtn = document.getElementById("sessionOkBtn");

/**
 * Stop: end the session for real — pause, flush both counters, show the
 * summary, then start a fresh session once the modal is acknowledged. Stop was
 * previously indistinguishable from Pause plus a dialog (F6).
 */
if (timerStopBtn) {
  timerStopBtn.addEventListener("click", async () => {
    pauseTimer(); // also triggers the user-time sync
    if (sessionModalTime) sessionModalTime.textContent = formatTime(timerLocalState.sessionSeconds);
    if (sessionModal) sessionModal.classList.add("is-active");
    updateTimerDisplays();

    // Flush the accrued task time so the session is fully persisted.
    const task = currentTaskResolver();
    if (task) await drainTaskTime(task);
  });
}

function closeSessionModal() {
  if (sessionModal) sessionModal.classList.remove("is-active");
  // The session is over and its time is banked; begin a new one at zero.
  timerLocalState.sessionSeconds = 0;
  timerLocalState.accumulatedMs = 0;
  updateTimerDisplays();
}

if (sessionClose) sessionClose.addEventListener("click", closeSessionModal);
if (sessionOkBtn) sessionOkBtn.addEventListener("click", closeSessionModal);

// Auto-start timer on canvas interaction
if (canvas) {
  canvas.addEventListener("pointerdown", () => {
    if (!timerLocalState.isTimerRunning) {
      startTimer();
    }
    noteUserActivity();
  });
}

// --- Idle auto-pause (docs/TIMER_AUDIT.md F8) ---------------------------------
// Without this, walking away from an open tab silently inflates both the task
// and the user totals. Tuned in one place so it can be changed easily.
export const IDLE_TIMEOUT_MS = 5 * 60 * 1000;

let lastActivityAt = Date.now();

function noteUserActivity() {
  lastActivityAt = Date.now();
}

['pointerdown', 'keydown', 'wheel'].forEach(evt => {
  window.addEventListener(evt, noteUserActivity, { passive: true });
});

// Checked on the same cadence as the display so an idle stretch is billed only
// up to the point activity actually stopped.
function pauseIfIdle() {
  if (!timerLocalState.isTimerRunning) return;
  if (Date.now() - lastActivityAt < IDLE_TIMEOUT_MS) return;

  // Roll back the idle stretch: it was accrued tick by tick, but the user was
  // not working. Only the time up to the last activity should count.
  const idleSeconds = Math.floor((Date.now() - lastActivityAt) / 1000);
  if (idleSeconds > 0) {
    timerLocalState.totalSeconds = Math.max(
      timerLocalState.lastSyncedTotalSeconds,
      timerLocalState.totalSeconds - idleSeconds
    );
    timerState.taskSessionSeconds = Math.max(0, timerState.taskSessionSeconds - idleSeconds);
    timerLocalState.accumulatedMs = Math.max(0, timerLocalState.accumulatedMs - idleSeconds * 1000);
    timerLocalState.sessionSeconds = Math.floor(timerLocalState.accumulatedMs / 1000);
  }
  pauseTimer();
}

// --- Tab-visibility auto-pause/resume (.devnotes/timer/01_TAB_VISIBILITY_PAUSE.md) ---
// Idle-pause (above) only catches a hidden tab after up to 5 minutes. A tab
// switch is detectable instantly via the Page Visibility API, so pause the
// moment it happens rather than waiting for the idle rollback. Unlike idle
// -pause, no rollback is needed: the event fires before any wrongly-accrued
// time builds up, so a plain pause is exact.
let pausedByVisibility = false;

export function handleVisibilityChange() {
  if (document.visibilityState === 'hidden') {
    // Only mark this as "our" pause if the timer was actually running.
    // Otherwise a manual pause (or an idle-pause) that happened to precede
    // the tab switch would get force-resumed when the tab comes back.
    if (timerLocalState.isTimerRunning) {
      pausedByVisibility = true;
      pauseTimer({ useBeacon: true });
    }
  } else if (document.visibilityState === 'visible') {
    if (pausedByVisibility) {
      pausedByVisibility = false;
      startTimer();
    }
  }
}

// Initialize displays
updateTimerDisplays();

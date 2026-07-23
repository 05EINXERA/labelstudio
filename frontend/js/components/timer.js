import { formatTime } from "../utils.js?v=1";
import { apiFetch } from "../api.js?v=1";
import { timerState } from "../timer-state.js?v=1";
import { canvas } from "../dom.js?v=1";

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
export async function drainTaskTime(task, { status, annotations, useBeacon = false } = {}) {
  if (!task || !task.id) return;

  const taskId = task.id;
  const timeDelta = timerState.taskSessionSeconds;
  timerState.taskSessionSeconds = 0;

  const payload = {
    id: taskId,
    time_spent_delta: timeDelta,
    status: status || task.status || 'In Progress',
    assignee: localStorage.getItem('dataset_username') || 'Unknown',
    annotations: JSON.stringify(annotations || task.annotations || []),
    updated_at: task.updated_at
  };

  // On unload a normal fetch is not guaranteed to be delivered; sendBeacon is
  // (F2). Beacons give us no response, so treat dispatch as success.
  if (useBeacon && navigator.sendBeacon) {
    const ok = navigator.sendBeacon(
      '/api/tasks',
      new Blob([JSON.stringify(payload)], { type: 'application/json' })
    );
    if (ok) {
      task.time_spent = (task.time_spent || 0) + timeDelta;
    } else {
      timerState.taskSessionSeconds += timeDelta;
    }
    updateTimerDisplays();
    return;
  }

  try {
    const res = await apiFetch('/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      keepalive: useBeacon || undefined
    });

    if (res.status === 409) {
      const errorMsg = await res.json();
      alert(`Conflict: ${errorMsg.detail}`);
      task.id = null; // Prevent further autosaves for this task
      // The task is abandoned, so the delta has nowhere to go. Drop it rather
      // than letting it leak into the next task the user opens.
      updateTimerDisplays();
      return;
    }
    if (!res.ok) {
      timerState.taskSessionSeconds += timeDelta;
      updateTimerDisplays();
      return;
    }
    // The server has banked the delta, so move it from the pending accumulator
    // into the task's stored total. Without this the "Total" readout would drop
    // back by the delta on every sync.
    task.time_spent = (task.time_spent || 0) + timeDelta;

    const data = await res.json();
    if (data && data.updated_at) {
      task.updated_at = data.updated_at;
    }
  } catch (e) {
    timerState.taskSessionSeconds += timeDelta;
  }
  updateTimerDisplays();
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

  if (useBeacon && navigator.sendBeacon) {
    const ok = navigator.sendBeacon(
      '/api/team/time',
      new Blob([body], { type: 'application/json' })
    );
    if (ok) timerLocalState.lastSyncedTotalSeconds = syncedUpTo;
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

function pauseTimer() {
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
  syncTimeToServer(); // final sync on pause
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

// Initialize displays
updateTimerDisplays();

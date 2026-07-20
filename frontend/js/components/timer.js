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
  totalSeconds: 0,
  lastSyncedTotalSeconds: 0,
  isTimerRunning: false
};

export async function syncTaskTime(task) {
  if (task && task.id) {
    const timeDelta = timerState.taskSessionSeconds;
    timerState.taskSessionSeconds = 0;
    apiFetch('/api/tasks', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        id: task.id,
        time_spent_delta: timeDelta,
        status: task.status || 'In Progress',
        assignee: localStorage.getItem('dataset_username') || 'Unknown',
        annotations: JSON.stringify(task.annotations || []),
        updated_at: task.updated_at
      })
    })
      .then(async res => {
        if (res.status === 409) {
          const errorMsg = await res.json();
          alert(`Conflict: ${errorMsg.detail}`);
          task.id = null; // Prevent further autosaves for this task
          return;
        }
        if (res.ok) {
          const data = await res.json();
          if (data && data.updated_at) {
            task.updated_at = data.updated_at;
          }
        }
      })
      .catch(() => { });
  }
}

// Fetch initial time
(async () => {
  if (timerLocalState.currentUserForTimer !== 'Unknown') {
    try {
      const res = await apiFetch('/api/team');
      if (res.ok) {
        const team = await res.json();
        const member = team.find(m => m.name === timerLocalState.currentUserForTimer);
        if (member) {
          timerLocalState.totalSeconds = member.time_logged || 0;
          timerLocalState.lastSyncedTotalSeconds = timerLocalState.totalSeconds;
          updateTimerDisplays();
        }
      }
    } catch (e) { }
  }
})();

const playSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
const pauseSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>';

function updateTimerDisplays() {
  if (sessionTimerDisplay) sessionTimerDisplay.textContent = formatTime(timerLocalState.sessionSeconds);
  if (totalTimeLoggedDisplay) totalTimeLoggedDisplay.textContent = formatTime(timerLocalState.totalSeconds);
}

export function syncTimeToServer() {
  if (timerLocalState.currentUserForTimer !== 'Unknown') {
    const delta = timerLocalState.totalSeconds - timerLocalState.lastSyncedTotalSeconds;
    if (delta > 0) {
      apiFetch('/api/team/time', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: timerLocalState.currentUserForTimer, time_logged: delta })
      }).catch(() => { });
      timerLocalState.lastSyncedTotalSeconds = timerLocalState.totalSeconds;
    }
  }
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

  timerLocalState.timerInterval = setInterval(() => {
    timerLocalState.sessionSeconds++;
    timerLocalState.totalSeconds++;
    timerState.taskSessionSeconds++;

    if (timerLocalState.sessionSeconds % 30 === 0) {
      syncTimeToServer();
    }

    updateTimerDisplays();
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

if (timerResetBtn) {
  timerResetBtn.addEventListener("click", () => {
    if (confirm("Reset the timer? This will clear your current session time.")) {
      pauseTimer();
      timerLocalState.sessionSeconds = 0;
      updateTimerDisplays();
    }
  });
}

const sessionModal = document.getElementById("sessionModal");
const sessionModalTime = document.getElementById("sessionModalTime");
const sessionClose = document.getElementById("sessionClose");
const sessionOkBtn = document.getElementById("sessionOkBtn");

if (timerStopBtn) {
  timerStopBtn.addEventListener("click", () => {
    pauseTimer();
    if (sessionModalTime) sessionModalTime.textContent = formatTime(timerLocalState.sessionSeconds);
    if (sessionModal) sessionModal.classList.add("is-active");
    updateTimerDisplays();
  });
}

function closeSessionModal() {
  if (sessionModal) sessionModal.classList.remove("is-active");
}

if (sessionClose) sessionClose.addEventListener("click", closeSessionModal);
if (sessionOkBtn) sessionOkBtn.addEventListener("click", closeSessionModal);

// Auto-start timer on canvas interaction
if (canvas) {
  canvas.addEventListener("pointerdown", () => {
    if (!timerLocalState.isTimerRunning) {
      startTimer();
    }
  });
}

// Initialize displays
updateTimerDisplays();

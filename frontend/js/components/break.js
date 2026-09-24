/**
 * Take-a-break control for the annotation workspace.
 *
 * The coffee-cup button in the timer row starts a break: the server records it
 * (POST /api/team/breaks) against the annotator in X-Annotator-Name, the
 * session timer is paused so the break is not billed to the open task, and a
 * blocking "On break" screen covers the canvas until End break is clicked
 * (PATCH /api/team/breaks/current). The Teams page reads those rows to show
 * who is on break and each member's break history.
 *
 * The server is the source of truth for whether a break is open. On load the
 * workspace asks GET /api/team/breaks/current, so a reload or a second tab
 * reopens the break screen with the original start time instead of silently
 * dropping the annotator back into work while the break is still running.
 */
import { apiFetch } from "../api.js?v=3";
import { formatTime } from "../utils.js?v=3";

const breakBtn = document.getElementById("breakBtn");
const breakModal = document.getElementById("breakModal");
const breakElapsed = document.getElementById("breakElapsed");
const breakStartedAt = document.getElementById("breakStartedAt");
const breakEndBtn = document.getElementById("breakEndBtn");
const breakError = document.getElementById("breakError");

// Object-wrapped because it is reassigned (see timer-state.js).
const breakState = {
  startedAtMs: null,
  tickInterval: null,
  busy: false,
};

let onBreakStart = () => {};

function showError(message) {
  if (!breakError) return;
  breakError.textContent = message || "";
  breakError.style.display = message ? "block" : "none";
}

function renderElapsed() {
  if (!breakElapsed || breakState.startedAtMs == null) return;
  const seconds = Math.max(0, Math.floor((Date.now() - breakState.startedAtMs) / 1000));
  breakElapsed.textContent = formatTime(seconds);
}

function showBreakScreen(startedAtIso) {
  const parsed = Date.parse(startedAtIso);
  // Fall back to "now" rather than showing NaN if the server's timestamp is
  // unreadable; the recorded start on the server is unaffected.
  breakState.startedAtMs = Number.isNaN(parsed) ? Date.now() : parsed;
  if (breakStartedAt) {
    breakStartedAt.textContent = new Date(breakState.startedAtMs)
      .toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }
  showError("");
  renderElapsed();
  clearInterval(breakState.tickInterval);
  // Repaint only; elapsed time is always derived from the start timestamp, so
  // a throttled background tab still shows the right figure when it returns.
  breakState.tickInterval = setInterval(renderElapsed, 1000);
  if (breakModal) breakModal.classList.add("is-active");
  if (breakBtn) breakBtn.setAttribute("aria-pressed", "true");
  if (breakEndBtn) breakEndBtn.focus();
}

function hideBreakScreen() {
  clearInterval(breakState.tickInterval);
  breakState.tickInterval = null;
  breakState.startedAtMs = null;
  if (breakModal) breakModal.classList.remove("is-active");
  if (breakBtn) breakBtn.setAttribute("aria-pressed", "false");
}

async function readError(res, fallback) {
  try {
    const body = await res.json();
    if (body && typeof body.detail === "string") return body.detail;
  } catch (err) {
    console.warn("Break request returned a non-JSON error body", err);
  }
  return fallback;
}

async function startBreak() {
  if (breakState.busy) return;
  breakState.busy = true;
  if (breakBtn) breakBtn.disabled = true;
  try {
    const res = await apiFetch("/api/team/breaks", { method: "POST" });
    if (!res) return;
    if (!res.ok) {
      alert(await readError(res, `Could not start the break (${res.status}).`));
      return;
    }
    const entry = await res.json();
    onBreakStart();
    showBreakScreen(entry.started_at);
  } catch (err) {
    alert("Could not start the break: " + err.message);
  } finally {
    breakState.busy = false;
    if (breakBtn) breakBtn.disabled = false;
  }
}

async function endBreak() {
  if (breakState.busy) return;
  breakState.busy = true;
  if (breakEndBtn) breakEndBtn.disabled = true;
  try {
    const res = await apiFetch("/api/team/breaks/current", { method: "PATCH" });
    if (!res) return;
    if (!res.ok) {
      // Stay on the break screen: leaving it while the server still has the
      // break open would record the rest of the shift as break time.
      showError(await readError(res, `Could not end the break (${res.status}). Try again.`));
      return;
    }
    hideBreakScreen();
  } catch (err) {
    showError("Could not reach the server to end the break. Check the connection and try again.");
  } finally {
    breakState.busy = false;
    if (breakEndBtn) breakEndBtn.disabled = false;
  }
}

/** Restore an open break after a reload, or one started in another tab. */
async function restoreOpenBreak() {
  try {
    const res = await apiFetch("/api/team/breaks/current");
    if (!res || !res.ok) return;
    const status = await res.json();
    if (status.on_break && status.current) {
      onBreakStart();
      showBreakScreen(status.current.started_at);
    }
  } catch (err) {
    console.warn("Could not check for an open break", err);
  }
}

/**
 * @param {object} opts
 * @param {() => void} [opts.onBreakStart] called when a break begins or is
 *   restored — the page uses it to pause the session timer.
 */
export function initBreakControl({ onBreakStart: onStart } = {}) {
  if (!breakBtn || !breakModal) return;
  if (typeof onStart === "function") onBreakStart = onStart;
  breakBtn.addEventListener("click", startBreak);
  if (breakEndBtn) breakEndBtn.addEventListener("click", endBreak);
  restoreOpenBreak();
}

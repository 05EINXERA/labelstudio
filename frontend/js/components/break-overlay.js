/**
 * The "Take a Break" overlay (.devnotes/attendance-feature/ § 4.1).
 *
 * A declared break replaces a guess with a fact: the interval is bounded by
 * two user actions rather than inferred from a gap, so it is as accurate as
 * the annotator is honest rather than as accurate as a heuristic.
 *
 * **This overlay is deliberately NOT dismissible by Escape or a backdrop
 * click**, which is the one place it departs from `modal.js`. Dismissing it
 * would leave a break open with no visible way to end it — the founding
 * problem of this whole feature, reachable by a stray keypress. Only *End
 * Break* closes it. That is why this does not use `createModal`: that helper
 * wires both dismissals, and opting out of them is most of what it does.
 *
 * The break also pauses the annotation timer (Q15: "both"), so one user action
 * owns the interval and the 5-minute idle auto-pause never has to guess at it.
 */
import { apiFetch } from "../api.js?v=5";
import { pauseTimerForBreak, resumeTimerAfterBreak } from "./timer.js?v=9";

let els = null;
let tickTimer = null;
let startedAt = null;
let ending = false;

/** Seconds as H:MM:SS, matching the timer's own elapsed display. */
function formatElapsed(totalSeconds) {
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return (
    `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
  );
}

function render() {
  if (!els?.elapsed || startedAt === null) return;
  const seconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  els.elapsed.textContent = formatElapsed(seconds);
}

function show(sinceMs) {
  startedAt = sinceMs;
  render();
  els.overlay.classList.add("is-active"); // rule 15: the class, never style.display
  clearInterval(tickTimer);
  tickTimer = setInterval(render, 1000);
  // Focus End Break so the keyboard route out of the overlay is the only route
  // out of the overlay.
  requestAnimationFrame(() => els.endBtn?.focus());
}

function hide() {
  clearInterval(tickTimer);
  tickTimer = null;
  startedAt = null;
  els.overlay.classList.remove("is-active");
}

/** True while a break is open on screen. */
export function isOnBreak() {
  return Boolean(els?.overlay.classList.contains("is-active"));
}

async function startBreak() {
  if (isOnBreak()) return;
  try {
    const res = await apiFetch("/api/attendance/break/start", { method: "POST" });
    if (!res.ok) {
      console.error("Could not start a break", res.status);
      return;
    }
    const body = await res.json();
    // The server's start time wins, not the client's: it is what the record
    // will hold, and an idempotent response hands back an *existing* break's
    // start rather than the moment of this click.
    pauseTimerForBreak();
    show(new Date(body.started_at).getTime());
  } catch (err) {
    // A break that cannot be declared must not leave the annotator staring at
    // an overlay they cannot end, so nothing is shown on failure.
    console.error("Could not start a break", err);
  }
}

async function endBreak() {
  if (ending) return; // a double-click must not fire two ends
  ending = true;
  try {
    const res = await apiFetch("/api/attendance/break/end", { method: "POST" });
    if (!res.ok) console.error("Could not end the break cleanly", res.status);
  } catch (err) {
    console.error("Could not end the break cleanly", err);
  } finally {
    ending = false;
    // The overlay closes and the timer resumes even if the request failed.
    // Trapping someone behind a network error is worse than a break whose end
    // the server infers by timeout — the fallback exists for exactly this.
    hide();
    resumeTimerAfterBreak();
  }
}

/**
 * Wire the Take a Break button and its overlay.
 *
 * @param {object} nodes
 * @param {HTMLElement} nodes.button   the "Take a Break" trigger
 * @param {HTMLElement} nodes.overlay  the overlay root
 * @param {HTMLElement} nodes.elapsed  where the running count is written
 * @param {HTMLElement} nodes.endBtn   the "End Break" button
 */
export function wireBreakOverlay(nodes) {
  if (!nodes?.button || !nodes?.overlay) return;
  els = nodes;

  els.button.addEventListener("click", startBreak);
  els.endBtn?.addEventListener("click", endBreak);

  // Deliberately absent: an Escape handler and a backdrop click handler. See
  // the module note — dismissing this overlay strands an open break.

  // Closing the tab mid-break ends it, so the record is not left to the
  // IDLE_GAP fallback when the browser could say so. The same beacon pattern
  // as task-lock.js and timer.js, and CSRF-compatible through apiFetch.
  window.addEventListener("pagehide", () => {
    if (!isOnBreak()) return;
    try {
      // keepalive rather than sendBeacon: this needs the session cookie and
      // the CSRF header that apiFetch attaches, and fetch(keepalive) carries
      // both where a beacon would not.
      apiFetch("/api/attendance/break/end", { method: "POST", keepalive: true });
    } catch (err) {
      // Nothing to recover to — the page is going away. The fallback closes
      // the break at read time.
      console.error("Could not flush the break on unload", err);
    }
  });

  // Restore an open break after a reload. Without this a refresh mid-break
  // strands the user: the break is open server-side, but the page has no
  // overlay and therefore no way to end it.
  restoreOpenBreak();
}

async function restoreOpenBreak() {
  try {
    const res = await apiFetch("/api/attendance/break/open");
    if (!res.ok) return;
    const body = await res.json();
    if (!body?.started_at) return;
    pauseTimerForBreak();
    show(new Date(body.started_at).getTime());
  } catch (err) {
    console.error("Could not check for an open break", err);
  }
}

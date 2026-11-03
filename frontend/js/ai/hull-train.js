/**
 * Hull "Train" button: retrains the hull model from the app's finished tasks.
 *
 * Admins (ADMIN_USERNAMES on the server) get the button; everyone gets the
 * progress chip while a run is going, because the server pauses the AI tools
 * for the duration (the training needs the whole GPU). The finished model is
 * added to the Hull model picker by the server; `onFinished` refreshes it.
 *
 * State comes from GET /api/training/hull; start and cancel are POST and
 * DELETE on the same path.
 */
import { apiFetch } from "../api.js?v=3";

const button = document.querySelector("#hullTrainButton");
const chip = document.querySelector("#hullTrainStatus");
const POLL_MS = 20000;

let current = { state: "idle", can_train: false };
let onFinished = () => {};
let timer = null;
// Only a run seen finishing in this page is announced; a result from long ago is not news.
let sawRunning = false;

function percent(value) {
  return value == null ? "-" : value.toFixed(3);
}

function describe(status) {
  if (status.state === "running") {
    if (status.stage === "training" && status.epoch) {
      return `Training hull model: epoch ${status.epoch}/${status.epochs} · best ${percent(status.best)}`;
    }
    return `Training hull model: ${status.stage || "starting"}…`;
  }
  if (!sawRunning) return "";
  if (status.state === "done") return `New hull model ready: ${status.name} (defect mIoU ${percent(status.best)})`;
  if (status.state === "failed") return `Hull training failed: ${status.message || "see log"}`;
  if (status.state === "cancelled") return "Hull training cancelled";
  return "";
}

function render() {
  const running = current.state === "running";
  if (button) {
    button.hidden = !current.can_train;
    const label = button.querySelector(".btn-label");
    if (label) label.textContent = running ? "Stop" : "Train";
    button.title = running
      ? "Stop the hull model training"
      : "Train a new hull model on all finished tasks. Takes hours; AI tools are paused meanwhile.";
  }
  if (chip) {
    const text = describe(current);
    chip.textContent = text;
    chip.hidden = !text;
  }
}

function schedulePoll() {
  window.clearTimeout(timer);
  if (current.state === "running") timer = window.setTimeout(refresh, POLL_MS);
}

function apply(status) {
  const wasRunning = current.state === "running";
  current = status;
  if (status.state === "running") sawRunning = true;
  render();
  schedulePoll();
  if (wasRunning && status.state === "done") onFinished();
}

async function request(method) {
  const response = await apiFetch("/api/training/hull", { method });
  const payload = await response.json();
  if (!response.ok) throw new Error(typeof payload.detail === "string" ? payload.detail : `Request failed (${response.status})`);
  return payload;
}

async function refresh() {
  try {
    apply(await request("GET"));
  } catch (error) {
    console.error("Could not read hull training status:", error);
    schedulePoll();
  }
}

async function onClick() {
  const running = current.state === "running";
  const question = running
    ? "Stop the hull model training? Progress so far is discarded."
    : "Train a new hull model on all finished tasks?\n\nThis takes several hours. AI tools (Detect, Hull, Refine, Auto-Tag) are paused for everyone until it finishes. The new model is added to the Hull model picker; the current default is not changed.";
  if (!window.confirm(question)) return;
  button.disabled = true;
  try {
    apply(await request(running ? "DELETE" : "POST"));
  } catch (error) {
    console.error(error);
    window.alert(error.message || "Could not change hull training.");
  } finally {
    button.disabled = false;
  }
}

/** Wire the button; `finished` runs once when a watched run completes. */
export function initHullTrain(finished) {
  onFinished = finished || onFinished;
  if (button) button.addEventListener("click", onClick);
}

/** Read the server's state; call after login/page load. */
export function loadHullTrain() {
  return refresh();
}

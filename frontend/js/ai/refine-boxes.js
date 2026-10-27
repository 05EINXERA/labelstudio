/**
 * SAM box refinement ("Refine" toolbar button).
 *
 * Sends the image and its boxes to /api/detect/refine-boxes; SAM segments the
 * object inside each box and the box is tightened to that mask. The selected
 * boxes are refined, or every box when none is selected. The server keeps a
 * box unchanged when SAM's mask disagrees with it, so a miss never makes a
 * label worse. One undo step reverts the whole batch.
 */
import { round } from "../utils.js?v=3";
import { apiFetch, pollJob } from "../api.js?v=3";
import { state, snapshot } from "../state.js?v=5";
import { view } from "../canvas/view.js?v=3";
import { detectState } from "./detect-state.js?v=1";
import { getImageSrcForAPI } from "./shared.js?v=1";
import { setStatus, save, render } from "../components/workspace.js?v=17";

const TIMEOUT_MS = 120000;
// Matches RefineBoxesPayload.boxes max_length on the server.
const MAX_BOXES = 100;

const button = document.querySelector("#refineBoxesButton");

function setBusy(isBusy) {
  detectState.detectionBusy = isBusy;
  if (!button) return;
  button.disabled = isBusy;
  const label = button.querySelector(".btn-label");
  if (label) label.textContent = isBusy ? "Refining..." : "Refine";
}

function boxesToRefine() {
  const boxes = state.annotations.filter((item) => item.type === "bbox");
  const selected = boxes.filter((item) => state.selectedIds.has(item.id));
  return selected.length ? selected : boxes;
}

export async function refineBoxesWithSam() {
  if (!view.imageLoaded || detectState.detectionBusy) return 0;

  const targets = boxesToRefine().slice(0, MAX_BOXES);
  if (!targets.length) {
    setStatus("No boxes to refine");
    return 0;
  }

  setBusy(true);
  setStatus(`Refining ${targets.length} box${targets.length === 1 ? "" : "es"}`);
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const response = await apiFetch(`${window.location.origin}/api/detect/refine-boxes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        image: await getImageSrcForAPI(),
        boxes: targets.map((item) => [item.x, item.y, item.width, item.height]),
        sam_model: localStorage.getItem("ai_sam_model") || null
      })
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(typeof payload.detail === "string" ? payload.detail : `Refinement failed (${response.status})`);
    }
    const result = await pollJob(payload.job_id, controller);

    snapshot();
    let refined = 0;
    (result.boxes || []).forEach((entry, index) => {
      const annotation = targets[index];
      if (!annotation || !entry.refined) return;
      const [x, y, width, height] = entry.bbox.map(round);
      annotation.x = x;
      annotation.y = y;
      annotation.width = Math.max(1, width);
      annotation.height = Math.max(1, height);
      annotation.points = [
        { x, y },
        { x: x + annotation.width, y },
        { x: x + annotation.width, y: y + annotation.height },
        { x, y: y + annotation.height }
      ];
      refined += 1;
    });
    render();
    save();
    setStatus(`Refined ${refined} of ${targets.length}`);
    return refined;
  } catch (error) {
    console.error(error);
    setStatus("Refinement failed");
    window.alert(error.name === "AbortError" || error.message === "Aborted"
      ? "Box refinement timed out."
      : (error.message || "Box refinement failed."));
    return 0;
  } finally {
    clearTimeout(timeoutId);
    setBusy(false);
  }
}

export function initRefineBoxes() {
  if (button) button.addEventListener("click", () => refineBoxesWithSam());
}

/** Disable the button while the server refuses AI jobs (availability -> enabled). */
export function setRefineBoxesAvailable(available) {
  if (button) button.disabled = !available;
}

/**
 * Hull-condition semantic segmentation ("Hull" toolbar button).
 *
 * Posts the image to /api/detect/semantic and adds the returned polygons to
 * the canvas. The button stays hidden unless the server reports the optional
 * model as available (GET /api/detect/availability -> semantic).
 *
 * Pressing it again never deletes work. Hull regions nobody has edited are
 * redrawn by a sharper pass; everything else on the canvas (manual regions,
 * edited Hull regions) is kept and nothing new is drawn over it; areas Hull
 * covered before but the annotator deleted stay empty; and undrawn areas are
 * searched again with a lower bar. See ml/semseg.py compose_repeat.
 */
import { generateUUID, round } from "../utils.js?v=3";
import { apiFetch, pollJob } from "../api.js?v=3";
import { state, snapshot } from "../state.js?v=5";
import { view } from "../canvas/view.js?v=3";
import { annotationPoints } from "../canvas/geometry.js?v=10";
import { detectState } from "./detect-state.js?v=1";
import { getImageSrcForAPI } from "./shared.js?v=1";
import { setStatus, save, render } from "../components/workspace.js?v=17";

// Its own source tag, so the YOLO "Detect" button never touches these
// regions (it replaces "auto-detect").
const SOURCE = "hull-detect";
const TIMEOUT_MS = 120000;
// Per-task note that Hull has run, so a second press still knows the photo
// was segmented after the annotator deleted every Hull region.
const ranKey = (taskId) => `hull-ran-v1:${taskId}`;

/**
 * Short fingerprint of a region's label and outline, stored on each Hull
 * region when it is drawn. A region whose fingerprint still matches has not
 * been moved, reshaped or relabelled since. Points are compared at whole
 * pixels, so re-rounding on save and reload does not count as an edit.
 */
function fingerprint(annotation) {
  let hash = 0x811c9dc5;  // FNV-1a, 32 bit
  const text = `${annotation.labelId}|` + annotationPoints(annotation)
    .map((point) => `${Math.round(point.x)},${Math.round(point.y)}`).join(";");
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(36);
}

function isUntouchedHull(annotation) {
  return annotation.source === SOURCE && Boolean(annotation.hullPrint) && annotation.hullPrint === fingerprint(annotation);
}

function openTaskId() {
  const task = state.gallery && state.galleryIndex >= 0 ? state.gallery[state.galleryIndex] : null;
  return task && task.id ? task.id : null;
}

function hullRanBefore(taskId) {
  if (!taskId) return false;
  try {
    return localStorage.getItem(ranKey(taskId)) === "1";
  } catch (error) {
    console.warn("Could not read the Hull run note", error);
    return false;
  }
}

function noteHullRan(taskId) {
  if (!taskId) return;
  try {
    localStorage.setItem(ranKey(taskId), "1");
  } catch (error) {
    // Best effort: without it a press after deleting every Hull region
    // behaves like a first press.
    console.warn("Could not write the Hull run note", error);
  }
}

const outline = (annotation) => annotationPoints(annotation).map((point) => ({ x: point.x, y: point.y }));

const button = document.querySelector("#hullDetectButton");
const BASE_TITLE = button ? button.title : "";
// Name of the server's hull model ("hull_v5_own"), from availability and
// refreshed by each result; shown on the button and in the status line.
let modelName = "";

function showModel(model) {
  if (!model || !model.name) return;
  modelName = model.name;
  if (button) {
    const epoch = model.epoch == null ? "" : ` (epoch ${model.epoch})`;
    button.title = `${BASE_TITLE}\nModel: ${model.name}${epoch}`;
  }
}

function withModel(text) {
  return modelName ? `${text} · ${modelName}` : text;
}

function setBusy(isBusy) {
  detectState.detectionBusy = isBusy;
  if (!button) return;
  button.disabled = isBusy;
  const label = button.querySelector(".btn-label");
  if (label) label.textContent = isBusy ? "Segmenting..." : "Hull";
}

/**
 * The project label a predicted class stands for, or null.
 *
 * Matched on a name prefix sent by the server ("af paint" -> "AF Paint
 * Deterioration / Peeling"). Deliberately never creates a label: a project
 * without the hull classes should get nothing, not eight new classes.
 */
function labelForPrediction(prediction) {
  const prefix = String(prediction.label_prefix || "").toLowerCase();
  if (!prefix) return null;
  return state.labels.find((label) => String(label.name || "").trim().toLowerCase().startsWith(prefix)) || null;
}

function toAnnotation(prediction, label) {
  const [x, y, width, height] = prediction.bbox;
  return {
    id: generateUUID(),
    labelId: label.id,
    points: prediction.points.map((point) => ({ x: round(point.x), y: round(point.y) })),
    x: round(Math.max(0, x)),
    y: round(Math.max(0, y)),
    width: round(Math.max(1, width)),
    height: round(Math.max(1, height)),
    score: round(prediction.score),
    source: SOURCE,
    type: "polygon",
    detectedClass: label.name
  };
}

function withFingerprint(annotation) {
  annotation.hullPrint = fingerprint(annotation);
  return annotation;
}

export async function detectHullCondition() {
  if (!view.imageLoaded || detectState.detectionBusy) return 0;

  const taskId = openTaskId();
  // Untouched Hull regions are redrawn; every other region with an area
  // (manual, edited Hull, other AI tools) is kept as it is.
  const redrawIds = new Set(state.annotations.filter(isUntouchedHull).map((item) => item.id));
  const keptCount = state.annotations.length - redrawIds.size;
  const repeat = state.annotations.some((item) => item.source === SOURCE) || hullRanBefore(taskId);
  const keep = state.annotations.filter((item) => !redrawIds.has(item.id)).map(outline).filter((points) => points.length >= 3);
  const refine = state.annotations.filter((item) => redrawIds.has(item.id)).map(outline);

  setBusy(true);
  setStatus(withModel(repeat ? "Refining hull" : "Segmenting hull"));
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const response = await apiFetch(`${window.location.origin}/api/detect/semantic`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({ image: await getImageSrcForAPI(), keep, refine, repeat })
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(typeof payload.detail === "string" ? payload.detail : `Segmentation failed (${response.status})`);
    }
    const result = await pollJob(payload.job_id, controller);
    showModel(result.model);
    if (openTaskId() !== taskId) {
      // The annotator moved to another photo while this one was segmenting.
      setStatus("Hull result dropped (photo changed)");
      return 0;
    }

    const predictions = result.predictions || [];
    const detected = [];
    for (const prediction of predictions) {
      const label = labelForPrediction(prediction);
      if (label) detected.push(withFingerprint(toAnnotation(prediction, label)));
    }
    if (predictions.length && !detected.length) {
      setStatus("No matching classes");
      window.alert("This project has none of the hull-condition classes, so nothing was added.");
      return 0;
    }

    snapshot();
    // A region edited while the request ran is no longer untouched: keep it.
    state.annotations = [
      ...state.annotations.filter((item) => !(redrawIds.has(item.id) && isUntouchedHull(item))),
      ...detected
    ];
    state.selectedId = null;
    render();
    save();
    noteHullRan(taskId);
    const found = detected.length ? `${detected.length} regions` : "No regions";
    setStatus(withModel(repeat && keptCount ? `${found}, ${keptCount} kept` : found));
    return detected.length;
  } catch (error) {
    console.error(error);
    setStatus("Segmentation failed");
    window.alert(error.name === "AbortError" || error.message === "Aborted"
      ? "Hull segmentation timed out."
      : (error.message || "Hull segmentation failed."));
    return 0;
  } finally {
    clearTimeout(timeoutId);
    setBusy(false);
  }
}

export function initHullDetect() {
  if (button) button.addEventListener("click", () => detectHullCondition());
}

/** Show or hide the button from the server's availability answer. */
export function setHullDetectAvailable(available, model) {
  if (button) button.hidden = !available;
  if (available) showModel(model);
}

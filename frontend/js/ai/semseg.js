/**
 * Hull-condition semantic segmentation ("Hull" toolbar button).
 *
 * Posts the image to /api/detect/semantic and adds the returned polygons to
 * the canvas. The button stays hidden unless the server reports the optional
 * model as available (GET /api/detect/availability -> semantic).
 *
 * When the server has several hull models (availability -> semantic_models),
 * a picker beside the button chooses which one runs. The choice is a
 * per-browser convenience kept in localStorage; the server's default is used
 * when nothing (or a model that no longer exists) was chosen.
 */
import { generateUUID, round } from "../utils.js?v=3";
import { apiFetch, pollJob } from "../api.js?v=3";
import { state, snapshot } from "../state.js?v=5";
import { view } from "../canvas/view.js?v=3";
import { detectState } from "./detect-state.js?v=1";
import { getImageSrcForAPI } from "./shared.js?v=1";
import { setStatus, save, render } from "../components/workspace.js?v=17";

// Its own source tag, so re-running replaces only earlier hull results and
// the YOLO "Detect" button never wipes them (it replaces "auto-detect").
const SOURCE = "hull-detect";
const TIMEOUT_MS = 120000;

const button = document.querySelector("#hullDetectButton");
const picker = document.querySelector("#hullModelSelect");
const BASE_TITLE = button ? button.title : "";
const PICKER_STORAGE_KEY = "hullModel";
// [{id, name, epoch, default}] from availability; empty without a picker.
let models = [];
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

function modelLabel(model) {
  const epoch = model.epoch == null ? "" : ` · epoch ${model.epoch}`;
  return `${model.name}${epoch}${model.default ? " (default)" : ""}`;
}

function readSavedModel() {
  try {
    return window.localStorage.getItem(PICKER_STORAGE_KEY);
  } catch (error) {
    console.warn("Could not read the saved hull model:", error);
    return null;
  }
}

function saveModel(id) {
  try {
    window.localStorage.setItem(PICKER_STORAGE_KEY, id);
  } catch (error) {
    console.warn("Could not save the hull model choice:", error);
  }
}

/** The picked model's id, or null to let the server use its default. */
function chosenModelId() {
  return picker && !picker.hidden && picker.value ? picker.value : null;
}

function fillPicker(list) {
  models = Array.isArray(list) ? list : [];
  if (!picker) return;
  picker.replaceChildren(...models.map((model) => {
    const option = document.createElement("option");
    option.value = model.id;
    option.textContent = modelLabel(model);
    return option;
  }));
  picker.hidden = models.length < 2;
  if (picker.hidden) return;
  const saved = readSavedModel();
  const initial = models.find((model) => model.id === saved) || models.find((model) => model.default) || models[0];
  picker.value = initial.id;
  showModel(initial);
}

function withModel(text) {
  return modelName ? `${text} · ${modelName}` : text;
}

function setBusy(isBusy) {
  detectState.detectionBusy = isBusy;
  if (!button) return;
  button.disabled = isBusy;
  if (picker) picker.disabled = isBusy;
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

export async function detectHullCondition() {
  if (!view.imageLoaded || detectState.detectionBusy) return 0;

  setBusy(true);
  setStatus(withModel("Segmenting hull"));
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const response = await apiFetch(`${window.location.origin}/api/detect/semantic`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({ image: await getImageSrcForAPI(), model: chosenModelId() })
    });
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(typeof payload.detail === "string" ? payload.detail : `Segmentation failed (${response.status})`);
    }
    const result = await pollJob(payload.job_id, controller);
    showModel(result.model);

    const predictions = result.predictions || [];
    const detected = [];
    for (const prediction of predictions) {
      const label = labelForPrediction(prediction);
      if (label) detected.push(toAnnotation(prediction, label));
    }
    if (predictions.length && !detected.length) {
      setStatus("No matching classes");
      window.alert("This project has none of the hull-condition classes, so nothing was added.");
      return 0;
    }

    snapshot();
    state.annotations = [...state.annotations.filter((item) => item.source !== SOURCE), ...detected];
    state.selectedId = null;
    render();
    save();
    setStatus(withModel(detected.length ? `${detected.length} regions` : "No regions"));
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
  if (picker) {
    picker.addEventListener("change", () => {
      const model = models.find((item) => item.id === picker.value);
      if (!model) return;
      saveModel(model.id);
      showModel(model);
      setStatus(`Hull model: ${model.name}`);
    });
  }
}

/** Show or hide the button (and model picker) from the server's availability answer. */
export function setHullDetectAvailable(available, model, list = []) {
  if (button) button.hidden = !available;
  if (!available) {
    fillPicker([]);
    return;
  }
  showModel(model);
  fillPicker(list);
}

/**
 * vertex-size-control.js
 *
 * Lets the annotator choose the on-screen radius of polygon vertex handles.
 * Toolbar range slider + badge, redraws live, persisted to localStorage.
 */

import { annotationSettings } from "../feature-flags.js?v=11";
import { drawAllLayers } from "../canvas/draw.js?v=9";

const VERTEX_SIZE_STORAGE_KEY = "annotation_vertex_radius";
const DEFAULT_RADIUS = annotationSettings.vertexHandleRadius;
const MIN_GRAB_RADIUS = annotationSettings.vertexGrabRadius;
const MIN_RADIUS = 2;
const MAX_RADIUS = 16;

/**
 * Applies a vertex handle radius (screen px) and refreshes the canvas.
 * The click target is kept at least as large as the drawn dot.
 */
export function setVertexRadius(radius, persist = true) {
  const clamped = Math.max(MIN_RADIUS, Math.min(MAX_RADIUS, Math.round(radius)));

  annotationSettings.vertexHandleRadius = clamped;
  annotationSettings.vertexGrabRadius = Math.max(MIN_GRAB_RADIUS, clamped);
  annotationSettings.minGrabScreenRadius = annotationSettings.vertexGrabRadius;

  const slider = document.getElementById("vertexSizeSlider");
  const badge = document.getElementById("vertexSizeBadge");
  if (slider && Number(slider.value) !== clamped) slider.value = String(clamped);
  if (badge) badge.textContent = `${clamped}px`;

  if (persist) {
    try {
      localStorage.setItem(VERTEX_SIZE_STORAGE_KEY, String(clamped));
    } catch (err) {
      console.warn("Could not persist vertex size", err);
    }
  }

  drawAllLayers();
}

/** Shows or hides vertex handles (drawing only; hit-testing is unchanged). */
export function setVertexHandlesHidden(hidden) {
  annotationSettings.vertexHandlesHidden = hidden;
  const btn = document.getElementById("vertexVisibilityToggle");
  if (btn) {
    btn.setAttribute("aria-checked", String(!hidden));
    btn.classList.toggle("is-active", !hidden);
    btn.title = hidden ? "Vertices hidden. Show (V)" : "Hide vertices (V)";
  }
  drawAllLayers();
}

export function initVertexSizeControl() {
  setVertexHandlesHidden(false);
  document
    .getElementById("vertexVisibilityToggle")
    ?.addEventListener("click", () =>
      setVertexHandlesHidden(!annotationSettings.vertexHandlesHidden));
  window.addEventListener("keydown", (e) => {
    const target = e.target;
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target?.isContentEditable) {
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey || e.repeat) return;
    if (e.key === "v" || e.key === "V") {
      e.preventDefault();
      setVertexHandlesHidden(!annotationSettings.vertexHandlesHidden);
    }
  });

  const slider = document.getElementById("vertexSizeSlider");
  const badge = document.getElementById("vertexSizeBadge");

  let saved = null;
  try {
    saved = localStorage.getItem(VERTEX_SIZE_STORAGE_KEY);
  } catch (err) {
    console.warn("Could not read vertex size", err);
  }
  const initial = saved !== null && !isNaN(Number(saved)) ? Number(saved) : DEFAULT_RADIUS;
  setVertexRadius(initial, false);

  slider?.addEventListener("input", (e) => setVertexRadius(Number(e.target.value), true));

  if (badge) {
    badge.style.cursor = "pointer";
    badge.title = "Vertex size. Double-click to reset.";
    badge.addEventListener("dblclick", () => setVertexRadius(DEFAULT_RADIUS, true));
  }
}

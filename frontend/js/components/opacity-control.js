/**
 * opacity-control.js
 *
 * Controls the annotation color intensity / fill opacity in real time.
 * Supports:
 *  - Toolbar range slider (0% to 100%)
 *  - Interactive percentage badge
 *  - Real-time canvas redraw on both committed annotations and in-progress drawings
 *  - Keyboard shortcuts '[' (decrease) and ']' (increase)
 *  - Persistence to localStorage
 */

import { annotationOpacity } from "../feature-flags.js?v=1";
import { drawAllLayers } from "../canvas/draw.js?v=1";
import { setStatus } from "./workspace.js?v=3";

const OPACITY_STORAGE_KEY = "annotation_opacity_percent";
const DEFAULT_OPACITY = 50;

/**
 * Applies opacity percentage (0–100) to annotationOpacity and refreshes canvas.
 * @param {number} percent - Percentage from 0 (wireframe) to 100 (solid)
 * @param {boolean} [persist=true] - Whether to save the setting to localStorage
 */
export function setAnnotationOpacity(percent, persist = true) {
  const clamped = Math.max(0, Math.min(100, Math.round(percent)));
  const base = clamped / 100;

  // Control intensity specifically for presently annotating and selected shapes.
  // Background unselected shapes remain at a calm, consistent baseline (0.35).
  annotationOpacity.drawing = base;
  annotationOpacity.selected = base;
  annotationOpacity.normal = 0.35;

  const slider = document.getElementById("opacitySlider");
  const badge = document.getElementById("opacityBadge");

  if (slider && Number(slider.value) !== clamped) {
    slider.value = String(clamped);
  }
  if (badge) {
    badge.textContent = `${clamped}%`;
  }

  if (persist) {
    localStorage.setItem(OPACITY_STORAGE_KEY, String(clamped));
  }

  drawAllLayers();
}

/**
 * Initializes the opacity slider, badge, and keyboard shortcuts.
 */
export function initOpacityControl() {
  const slider = document.getElementById("opacitySlider");
  const badge = document.getElementById("opacityBadge");

  const saved = localStorage.getItem(OPACITY_STORAGE_KEY);
  const initial = saved !== null && !isNaN(Number(saved)) ? Number(saved) : DEFAULT_OPACITY;

  setAnnotationOpacity(initial, false);

  if (slider) {
    slider.addEventListener("input", (e) => {
      const val = Number(e.target.value);
      setAnnotationOpacity(val, true);
    });
  }

  // Double-click badge to cycle through common presets (50% -> 20% -> 80% -> 50%)
  if (badge) {
    badge.style.cursor = "pointer";
    badge.addEventListener("dblclick", () => {
      const current = Number(slider?.value || DEFAULT_OPACITY);
      const next = current === 50 ? 20 : (current === 20 ? 80 : 50);
      setAnnotationOpacity(next, true);
      setStatus(`Color intensity: ${next}%`);
    });
  }

  // Keyboard shortcuts: [ and ] to adjust opacity
  window.addEventListener("keydown", (e) => {
    const target = e.target;
    if (target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement || target?.isContentEditable) {
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;

    if (e.key === "[" || e.key === "{") {
      e.preventDefault();
      const current = Number(slider?.value || DEFAULT_OPACITY);
      const next = Math.max(0, current - 10);
      setAnnotationOpacity(next, true);
      setStatus(`Color intensity: ${next}%`);
    } else if (e.key === "]" || e.key === "}") {
      e.preventDefault();
      const current = Number(slider?.value || DEFAULT_OPACITY);
      const next = Math.min(100, current + 10);
      setAnnotationOpacity(next, true);
      setStatus(`Color intensity: ${next}%`);
    }
  });
}

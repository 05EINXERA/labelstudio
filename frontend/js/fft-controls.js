/**
 * fft-controls.js
 *
 * Wires the FFT smoothing toolbar controls (Smooth button, strength slider,
 * auto-smooth toggle) to the canvas annotation state.
 *
 * Call initFftControls() once during app init.
 */

import { smoothPolygon, autoTolerance } from './canvas/fft-smooth.js?v=1';
import { state, snapshot } from './state.js?v=2';
import { updateAnnotationBounds } from './canvas/geometry.js?v=4';
import { render } from './components/workspace.js?v=6';
import { setStatus } from './components/workspace.js?v=6';

// ---------------------------------------------------------------------------
// Persistence keys
// ---------------------------------------------------------------------------
const AUTO_SMOOTH_KEY = 'fft-auto-smooth';

/**
 * Returns true when the auto-smooth toggle is active.
 */
export function isAutoSmoothEnabled() {
  const toggle = document.getElementById('fftAutoSmoothToggle');
  if (toggle) return toggle.classList.contains('is-on');
  return localStorage.getItem(AUTO_SMOOTH_KEY) === 'true';
}



// ---------------------------------------------------------------------------
// initFftControls — call once in init.js
// ---------------------------------------------------------------------------
export function initFftControls() {
  // ── Elements ──────────────────────────────────────────────────────────────
  const autoToggle    = document.getElementById('fftAutoSmoothToggle');

  if (!autoToggle) {
    // Controls not present in this page (e.g. login page); silently bail.
    return;
  }

  // ── Restore persisted state ───────────────────────────────────────────────
  const autoOn = localStorage.getItem(AUTO_SMOOTH_KEY) === 'true';
  if (autoOn) autoToggle.classList.add('is-on');

  // ── Auto-smooth toggle ────────────────────────────────────────────────────
  autoToggle.addEventListener('click', () => {
    const on = autoToggle.classList.toggle('is-on');
    autoToggle.setAttribute('aria-checked', String(on));
    localStorage.setItem(AUTO_SMOOTH_KEY, String(on));
    setStatus(on ? 'Auto-smooth: ON — polygons will be smoothed on finalize' : 'Auto-smooth: OFF');
  });
}

// ---------------------------------------------------------------------------
// applyAutoSmooth — called from interactions.js finalizePolygon()
//
// Smooths the annotation in-place using `autoTolerance` (which adapts the
// epsilon to the point count). Does NOT call snapshot() — the caller already
// manages undo history around finalization.
// ---------------------------------------------------------------------------
export function applyAutoSmooth(annotation) {
  if (!isAutoSmoothEnabled()) return;
  if (!annotation || !annotation.points || annotation.points.length < 8) return;
  if (annotation.type === 'bbox') return;

  const tol = autoTolerance(annotation.points.length);
  const smoothed = smoothPolygon(annotation.points, tol);
  annotation.points = smoothed;
  // updateAnnotationBounds is called by the caller (finalizePolygon)
}

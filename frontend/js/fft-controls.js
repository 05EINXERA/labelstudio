/**
 * fft-controls.js
 *
 * Wires the FFT smoothing toolbar controls (Smooth button, strength slider,
 * auto-smooth toggle) to the canvas annotation state.
 *
 * Call initFftControls() once during app init.
 */

import { smoothPolygonFFT, autoKeepRatio } from './canvas/fft-smooth.js?v=1';
import { state, snapshot } from './state.js?v=6';
import { updateAnnotationBounds } from './canvas/geometry.js?v=1';
import { render } from './components/workspace.js?v=9';
import { setStatus } from './components/workspace.js?v=9';

// ---------------------------------------------------------------------------
// Persistence keys
// ---------------------------------------------------------------------------
const KEEP_RATIO_KEY  = 'fft-keep-ratio';
const AUTO_SMOOTH_KEY = 'fft-auto-smooth';

// ---------------------------------------------------------------------------
// Public accessors (used by interactions.js for auto-smooth)
// ---------------------------------------------------------------------------

/**
 * Returns the current keep-ratio (0.05 – 1.0) from the UI slider.
 * Falls back to the stored or default value if the element is not yet in DOM.
 */
export function getCurrentKeepRatio() {
  const slider = document.getElementById('fftStrengthSlider');
  if (slider) return parseFloat(slider.value);
  const stored = localStorage.getItem(KEEP_RATIO_KEY);
  return stored ? parseFloat(stored) : 0.4;
}

/**
 * Returns true when the auto-smooth toggle is active.
 */
export function isAutoSmoothEnabled() {
  const toggle = document.getElementById('fftAutoSmoothToggle');
  if (toggle) return toggle.classList.contains('is-on');
  return localStorage.getItem(AUTO_SMOOTH_KEY) === 'true';
}

// ---------------------------------------------------------------------------
// Core smooth action (used by the Smooth button)
// ---------------------------------------------------------------------------
function applySmoothToSelected() {
  const annotation = state.annotations.find(a => a.id === state.selectedId);

  if (!annotation) {
    setStatus('Select polygon to smooth');
    return;
  }
  if (!annotation.points || annotation.points.length < 4) {
    setStatus('Polygon needs 4+ points');
    return;
  }
  // Bounding-box annotations (exactly 4 axis-aligned points) should not be
  // smoothed — they would turn into a non-rectangular quadrilateral.
  if (annotation.type === 'bbox' || annotation.points.length === 4) {
    setStatus('Works on polygons only');
    return;
  }

  const keepRatio = getCurrentKeepRatio();
  snapshot();
  const smoothed = smoothPolygonFFT(annotation.points, keepRatio);
  annotation.points = smoothed;
  updateAnnotationBounds(annotation);
  render();
  setStatus(`Smoothed (${Math.round(keepRatio * 100)}%)`);
}

// ---------------------------------------------------------------------------
// initFftControls — call once in init.js
// ---------------------------------------------------------------------------
export function initFftControls() {
  // ── Elements ──────────────────────────────────────────────────────────────
  const smoothBtn     = document.getElementById('fftSmoothBtn');
  const slider        = document.getElementById('fftStrengthSlider');
  const sliderLabel   = document.getElementById('fftStrengthLabel');
  const autoToggle    = document.getElementById('fftAutoSmoothToggle');

  if (!smoothBtn || !slider || !autoToggle) {
    // Controls not present in this page (e.g. login page); silently bail.
    return;
  }

  // ── Restore persisted state ───────────────────────────────────────────────
  const storedRatio = localStorage.getItem(KEEP_RATIO_KEY);
  if (storedRatio) slider.value = storedRatio;
  updateSliderLabel();

  const autoOn = localStorage.getItem(AUTO_SMOOTH_KEY) === 'true';
  if (autoOn) autoToggle.classList.add('is-on');

  // ── Smooth button ─────────────────────────────────────────────────────────
  smoothBtn.addEventListener('click', () => {
    applySmoothToSelected();
  });

  // ── Strength slider ───────────────────────────────────────────────────────
  slider.addEventListener('input', () => {
    updateSliderLabel();
    localStorage.setItem(KEEP_RATIO_KEY, slider.value);
  });

  // ── Auto-smooth toggle ────────────────────────────────────────────────────
  autoToggle.addEventListener('click', () => {
    const on = autoToggle.classList.toggle('is-on');
    autoToggle.setAttribute('aria-checked', String(on));
    localStorage.setItem(AUTO_SMOOTH_KEY, String(on));
    setStatus(on ? 'Auto-smooth: ON' : 'Auto-smooth: OFF');
  });

  // ── Helper ────────────────────────────────────────────────────────────────
  function updateSliderFill() {
    // Compute percentage position of the thumb relative to [min, max] range
    const min = parseFloat(slider.min) || 0.05;
    const max = parseFloat(slider.max) || 1.0;
    const val = parseFloat(slider.value);
    const pct = ((val - min) / (max - min)) * 100;
    // Set --val so the CSS gradient knows where to split fill vs rail
    slider.style.setProperty('--val', `${pct.toFixed(1)}%`);
  }

  function updateSliderLabel() {
    if (sliderLabel) {
      sliderLabel.textContent = `${Math.round(parseFloat(slider.value) * 100)}%`;
    }
    updateSliderFill();
  }
}

// ---------------------------------------------------------------------------
// applyAutoSmooth — called from interactions.js finalizePolygon()
//
// Smooths the annotation in-place using `autoKeepRatio` (which adapts the
// cutoff to the point count). Does NOT call snapshot() — the caller already
// manages undo history around finalization.
// ---------------------------------------------------------------------------
export function applyAutoSmooth(annotation) {
  if (!isAutoSmoothEnabled()) return;
  if (!annotation || !annotation.points || annotation.points.length < 8) return;
  if (annotation.type === 'bbox') return;

  const kr = autoKeepRatio(annotation.points.length);
  const smoothed = smoothPolygonFFT(annotation.points, kr);
  annotation.points = smoothed;
  // updateAnnotationBounds is called by the caller (finalizePolygon)
}

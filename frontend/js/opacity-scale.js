/**
 * opacity-scale.js
 *
 * The slider-percentage <-> opacity conversion for the toolbar's Opacity
 * control, and the captured default it falls back to.
 *
 * The one slider drives TWO values: `annotationOpacity.selected` (the shape
 * the user has selected) and `annotationOpacity.drawing` (the shape being
 * traced right now). Annotators adjust opacity mid-draw for the same reason
 * they adjust it on a selected shape — the fill hides the pixels they are
 * tracing against — so a control that only took effect after the polygon
 * closed would miss the moment it is most needed.
 *
 * They are coupled by RATIO, not set equal. `drawing` defaults to half of
 * `selected` (0.30 vs 0.60), a deliberate choice so an in-progress shape reads
 * lighter than a committed one and the closing moment is visible. Setting both
 * to the slider value would erase that distinction. The ratio is derived from
 * the two defaults rather than hardcoded, so changing either constant in
 * feature-flags.js keeps the relationship the author intended.
 *
 * Split out from opacity-controls.js so it stays pure: that module imports
 * draw.js, which imports dom.js and touches `document` at module load, so
 * anything importing it cannot be unit-tested under bare node. This file
 * imports only feature-flags.js, which is dependency-free — the same reason
 * canvas/handle-size.js and objects-filter.js are separate modules.
 */

import { annotationOpacity } from "./feature-flags.js?v=4";

/**
 * The default, captured at module load — before any slider edit can mutate
 * `annotationOpacity.selected`. Reading the live value inside the functions
 * below would make the fallback drift to wherever the user last left the
 * slider, which is not a default.
 */
const DEFAULT_SELECTED = annotationOpacity.selected;

/**
 * How much lighter the in-progress fill is than the selected one, captured
 * from the defaults at module load (0.30 / 0.60 = 0.5).
 *
 * Guarded against a zero or non-finite `selected` default, which would make
 * this Infinity or NaN and poison every drawing opacity derived from it. A
 * ratio of 1 in that case simply means "same as selected" — a sane fallback
 * rather than an invisible in-progress shape.
 */
const DEFAULT_DRAWING = annotationOpacity.drawing;
const DRAWING_RATIO = (Number.isFinite(DEFAULT_DRAWING) &&
                       Number.isFinite(DEFAULT_SELECTED) && DEFAULT_SELECTED > 0)
  ? DEFAULT_DRAWING / DEFAULT_SELECTED
  : 1;

/**
 * Slider percentage (0-100) -> opacity (0-1), clamped.
 *
 * Degenerate input falls back to the default rather than propagating: a NaN
 * alpha makes hexToRgba() emit an invalid colour string, which canvas silently
 * ignores — every selected shape would lose its fill with nothing logged to
 * explain it.
 */
export function pctToOpacity(pct) {
  const n = Number(pct);
  if (!Number.isFinite(n)) return DEFAULT_SELECTED;
  return Math.min(1, Math.max(0, n / 100));
}

/** Opacity (0-1) -> integer slider percentage (0-100). */
export function opacityToPct(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return Math.round(DEFAULT_SELECTED * 100);
  return Math.round(Math.min(1, Math.max(0, n)) * 100);
}

/** The default as a percentage — the value the slider starts at. */
export function defaultPct() {
  return opacityToPct(DEFAULT_SELECTED);
}

/**
 * The in-progress ("drawing") opacity that pairs with a given selected
 * opacity, preserving the ratio between the two defaults.
 *
 * Clamped like the others: a ratio above 1 with a high selected value could
 * otherwise push this past a valid alpha.
 */
export function drawingOpacityFor(selectedOpacity) {
  const n = Number(selectedOpacity);
  // The captured default, not the live value — the slider mutates
  // annotationOpacity.drawing, so reading it here would make the fallback
  // drift to wherever the user last left it.
  if (!Number.isFinite(n)) return DEFAULT_DRAWING;
  return Math.min(1, Math.max(0, n * DRAWING_RATIO));
}

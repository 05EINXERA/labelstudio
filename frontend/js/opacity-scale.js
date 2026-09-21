/**
 * opacity-scale.js
 *
 * The slider-percentage <-> opacity conversion for the toolbar's Opacity
 * control, and the captured default it falls back to.
 *
 * Split out from opacity-controls.js so it stays pure: that module imports
 * draw.js, which imports dom.js and touches `document` at module load, so
 * anything importing it cannot be unit-tested under bare node. This file
 * imports only feature-flags.js, which is dependency-free — the same reason
 * canvas/handle-size.js and objects-filter.js are separate modules.
 */

import { annotationOpacity } from "./feature-flags.js?v=3";

/**
 * The default, captured at module load — before any slider edit can mutate
 * `annotationOpacity.selected`. Reading the live value inside the functions
 * below would make the fallback drift to wherever the user last left the
 * slider, which is not a default.
 */
const DEFAULT_SELECTED = annotationOpacity.selected;

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

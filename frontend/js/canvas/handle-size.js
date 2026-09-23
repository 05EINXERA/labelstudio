/**
 * handle-size.js
 *
 * How big a vertex handle is drawn at a given zoom.
 *
 * Pure: no DOM, no canvas, no `state` and no `view` import — it takes the zoom
 * as an argument so it can be unit-tested without a browser (same pattern as
 * objects-filter.js and comment-geometry.js). draw.js is the only caller and
 * supplies `view.viewZoom`.
 *
 * The curve, its constants and the reasoning behind keying off viewZoom rather
 * than imageBox.scale are all documented in feature-flags.js.
 */

import { annotationSettings } from "../feature-flags.js?v=5";

/**
 * Drawn radius, in on-screen pixels, of a vertex handle at `viewZoom`.
 *
 * @param {number} viewZoom Multiplier over fit-to-canvas scale; 1 === fit.
 * @returns {number} Radius in screen pixels, clamped to the configured range.
 */
export function vertexHandleRadius(viewZoom) {
  const {
    vertexHandleRadius: base,
    vertexHandleFalloff: falloff,
    vertexHandleMinRadius: min,
    vertexHandleMaxRadius: max
  } = annotationSettings;

  const zoom = Number(viewZoom);
  // A zero, negative or non-finite zoom makes the power term Infinity or NaN,
  // and a NaN radius makes arc() throw — which would blank the whole overlay
  // layer, losing every annotation on screen over a bad number. None of these
  // should reach us (setZoom clamps to [0.1, 500]), so this is a guard, not a
  // code path: fall back to the fit-zoom size and carry on drawing.
  if (!Number.isFinite(zoom) || zoom <= 0) return base;

  return Math.min(max, Math.max(min, base / zoom ** falloff));
}

/**
 * Ring width for a handle of radius `radius`.
 *
 * Scales with the handle instead of sitting at a constant 2px: at the minimum
 * radius a 2px ring would be most of the disc, leaving no white centre and
 * turning the handle into a solid colour dot.
 *
 * The divisor is derived from the configured base radius so that a handle at
 * fit zoom gets the historical 2px ring whatever the base is set to. It was
 * once hardcoded as 2.25, which silently encoded "the base is 4.5" -- raising
 * the base then thinned the ring proportionally instead of keeping it.
 */
const LINE_WIDTH_DIVISOR = annotationSettings.vertexHandleRadius / 2;

export function vertexHandleLineWidth(radius) {
  return Math.max(1, radius / LINE_WIDTH_DIVISOR);
}

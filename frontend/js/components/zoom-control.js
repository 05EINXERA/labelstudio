// Zoom readout and +/- buttons in the top nav.
//
// view.viewZoom is a multiplier over the fit-to-canvas (baseScale) scale.
// The display shows pixel-accurate percentage:
//   displayed % = view.baseScale * view.viewZoom * 100
// so 100% means one image pixel = one screen pixel (true 1:1).
// At fit (viewZoom === 1) the display shows e.g. "23%" for a large image,
// or "150%" for a small image that is upscaled to fill the canvas.

import { view } from "../canvas/view.js?v=1";
import { setZoom } from "../canvas/interactions.js?v=1";
import { drawAllLayers } from "../canvas/draw.js?v=1";

// viewZoom bounds (multiplier over fit-scale).
//
// MIN: allow zooming out to 10% of native pixels.
// MAX: viewZoom of 100 → displayed % = baseScale × 100 × 100.
//      For a 1:1 image (baseScale=1) that is exactly 10000%.
//      For a large image (e.g. baseScale=0.2) the ceiling is 2000% native,
//      still far more than any annotation task needs.
//
// Both values must be kept in sync with the clamp inside setZoom.
const MIN_ZOOM = 0.1;
const MAX_ZOOM = 500;

// Same ratio as one wheel notch, so button and wheel zoom feel identical.
const STEP = 1.1;

const zoomInButton = document.querySelector("#zoomInButton");
const zoomOutButton = document.querySelector("#zoomOutButton");
const zoomLevel = document.querySelector("#zoomLevel");

export function updateZoomDisplay() {
  if (!zoomLevel) return;

  // Pixel-accurate percentage: baseScale converts viewZoom (fit-relative) to
  // actual pixels-per-image-pixel.  baseScale is 1 before the first draw, so
  // the display correctly shows "100%" at startup before an image is loaded.
  const pct = Math.round(view.baseScale * view.viewZoom * 100);
  zoomLevel.textContent = `${pct}%`;
  zoomLevel.title = "Click to reset to fit";

  const disabled = !view.imageLoaded;
  // Small epsilon: repeated STEP multiplication lands fractionally short of
  // the bound, which would leave a button enabled but inert.
  if (zoomInButton) {
    zoomInButton.disabled = disabled || view.viewZoom >= MAX_ZOOM - 1e-6;
  }
  if (zoomOutButton) {
    zoomOutButton.disabled = disabled || view.viewZoom <= MIN_ZOOM + 1e-6;
  }
  zoomLevel.disabled = disabled;
}

export function initZoomControl() {
  if (zoomInButton) {
    zoomInButton.addEventListener("click", () => {
      // No cursor position: setZoom falls back to the canvas centre, which is
      // the right pivot for a button press.
      setZoom(view.viewZoom * STEP);
    });
  }

  if (zoomOutButton) {
    zoomOutButton.addEventListener("click", () => {
      setZoom(view.viewZoom / STEP);
    });
  }

  if (zoomLevel) {
    zoomLevel.addEventListener("click", () => {
      // Reset to fit (viewZoom === 1) and re-centre. setZoom derives viewPan
      // from its pivot point, so pan must be cleared here to actually centre.
      setZoom(1);
      view.viewPan.x = 0;
      view.viewPan.y = 0;
      drawAllLayers();
    });
  }

  updateZoomDisplay();
}

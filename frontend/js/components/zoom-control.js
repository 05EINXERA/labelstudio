// Zoom readout and +/- buttons in the top nav.
//
// view.viewZoom is a multiplier over the fit-to-canvas scale, so 1 renders as
// 100%: the percentage is relative to the image fitting the canvas, not to the
// image's natural pixel size.

import { view } from "../canvas/view.js?v=1";
import { setZoom } from "../canvas/interactions.js?v=1";
import { drawAllLayers } from "../canvas/draw.js?v=1";

// Matches the clamp inside setZoom. Kept in sync so the buttons disable at the
// same points where further zooming would be a no-op.
const MIN_ZOOM = 0.25;
const MAX_ZOOM = 100;

// Same ratio as one wheel notch, so button and wheel zoom feel identical.
const STEP = 1.1;

const zoomInButton = document.querySelector("#zoomInButton");
const zoomOutButton = document.querySelector("#zoomOutButton");
const zoomLevel = document.querySelector("#zoomLevel");

export function updateZoomDisplay() {
  if (!zoomLevel) return;

  const pct = Math.round(view.viewZoom * 100);
  zoomLevel.textContent = `${pct}%`;

  const disabled = !view.imageLoaded;
  // Compare against the clamp with a small epsilon: repeated multiplication
  // lands fractionally short of the bound, which would leave the button
  // enabled but inert.
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
      // what a button press should pivot around.
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
      // setZoom always derives viewPan from its pivot point, so the pan has to
      // be cleared afterwards and redrawn to actually recentre the image.
      setZoom(1);
      view.viewPan.x = 0;
      view.viewPan.y = 0;
      drawAllLayers();
    });
  }

  updateZoomDisplay();
}

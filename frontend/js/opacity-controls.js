/**
 * opacity-controls.js
 *
 * The toolbar's Opacity slider: sets the fill opacity of the SELECTED
 * annotation, live, while the user drags.
 *
 * Session-only by design. The value lives in the `annotationOpacity` module
 * object and nowhere else — no localStorage, no server preference, no task
 * record. A reload re-evaluates feature-flags.js and the slider is back at its
 * default. This is deliberate and is the main way this control differs from
 * the FFT strength slider it replaced, which did persist; do not "restore"
 * that behaviour by copying the old handler.
 *
 * Drives both `selected` and `drawing` — an annotator needs to thin the fill
 * while tracing, not only after the shape closes. The two stay in their
 * default ratio (drawing is half of selected) rather than being set equal, so
 * an in-progress shape still reads lighter than a committed one; the
 * arithmetic is in opacity-scale.js. `normal` is deliberately NOT bound — it
 * paints the static layer, which would make every input event a full static
 * repaint. See .devnotes/opacity-slider-feature/01_PLAN.md § 3.6.
 *
 * The arithmetic lives in opacity-scale.js so it can be tested without a DOM;
 * this module is the wiring only.
 */

import { annotationOpacity } from "./feature-flags.js?v=5";
import { pctToOpacity, defaultPct, drawingOpacityFor } from "./opacity-scale.js?v=2";
import { draw } from "./canvas/draw.js?v=11";

/**
 * Wires the slider. Call once during app init; a no-op on pages without the
 * toolbar.
 */
export function initOpacityControls() {
  const slider = document.getElementById("opacitySlider");
  const label = document.getElementById("opacityValueLabel");
  if (!slider) return;

  const start = defaultPct();
  slider.value = String(start);
  updateReadout(start);

  slider.addEventListener("input", () => {
    const pct = Number(slider.value);
    const selected = pctToOpacity(pct);
    annotationOpacity.selected = selected;
    // The in-progress shape tracks it, keeping the default ratio.
    annotationOpacity.drawing = drawingOpacityFor(selected);
    updateReadout(pct);
    // draw(), not drawAllLayers(). Both the selected shape and the one being
    // drawn are painted on the interactive layer; drawStaticLayer() handles
    // the unselected ones and this slider cannot change them. This also means
    // dragging the slider mid-draw repaints correctly without disturbing the
    // in-progress polygon. drawAllLayers() would also recompute
    // the image box and repaint the whole image layer — on every pixel of
    // slider travel, for no visual difference. See the plan § 2.5.
    draw();
  });

  function updateReadout(pct) {
    if (label) label.textContent = `${pct}%`;
    // Drives the CSS fill gradient's split point.
    slider.style.setProperty("--val", `${pct}%`);
    slider.setAttribute("aria-valuenow", String(pct));
  }
}

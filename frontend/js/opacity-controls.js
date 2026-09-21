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
 * Only `selected` is bound. `normal` and `drawing` are untouched — see
 * .devnotes/opacity-slider-feature/01_PLAN.md § 3.6.
 *
 * The arithmetic lives in opacity-scale.js so it can be tested without a DOM;
 * this module is the wiring only.
 */

import { annotationOpacity } from "./feature-flags.js?v=3";
import { pctToOpacity, defaultPct } from "./opacity-scale.js?v=1";
import { draw } from "./canvas/draw.js?v=8";

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
    annotationOpacity.selected = pctToOpacity(pct);
    updateReadout(pct);
    // draw(), not drawAllLayers(). Selected shapes are painted on the
    // interactive layer; drawStaticLayer() handles the unselected ones and
    // this slider cannot change them. drawAllLayers() would also recompute
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

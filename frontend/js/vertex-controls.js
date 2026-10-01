/**
 * vertex-controls.js
 *
 * The toolbar's "Vertex : On / Off" pill: shows whether vertex handles are
 * drawn on selected shapes, and toggles them when clicked (the same as
 * tapping "V").
 *
 * "On" means the handles are visible. The pill shows what is on screen, so a
 * held "V" reads Off until release. It is updated only when the state changes
 * (from interactions.js), never from render(), so panning and zooming never
 * touch the DOM.
 *
 * The toggle itself lives in canvas/interactions.js and is passed in as
 * `onToggle`. Importing it here would make the two modules import each other.
 * See .devnotes/feat/hide-vertex/01_DESIGN.md D8.
 */

import { state } from "./state.js?v=14";

/**
 * Wires the pill. Call once during app init; a no-op on pages without it.
 *
 * @param {() => void} onToggle Runs one "V" tap.
 */
export function initVertexControls(onToggle) {
  const pill = document.getElementById("vertexToggle");
  if (!pill) return;
  pill.addEventListener("click", () => {
    onToggle();
    // Hand focus back to the page so the next V / H / Delete reaches the
    // canvas instead of pressing this button again.
    pill.blur();
  });
  syncVertexPill();
}

/** Repaint the pill from state. Cheap: one text and two class writes. */
export function syncVertexPill() {
  const pill = document.getElementById("vertexToggle");
  if (!pill) return;
  const on = !(state.verticesHidden || state.verticesPeekHidden);
  pill.textContent = on ? "Vertex : On" : "Vertex : Off";
  pill.classList.toggle("is-on", on);
  pill.classList.toggle("is-off", !on);
  pill.setAttribute("aria-pressed", on ? "true" : "false");
}

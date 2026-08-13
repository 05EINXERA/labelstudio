import { canvas } from "../dom.js?v=3";
import { state } from "../state.js?v=7";
import { view } from "./view.js?v=1";
import { render } from "../components/workspace.js?v=13";
import {
  canvasPoint, hitTest,
  sendToBack, sendBackward, bringToFront, bringForward
} from "./interactions.js?v=6";

// Right-click z-order menu for annotations.
//
// The canvas already suppresses the native context menu (interactions.js) so
// right-drag can pan. This reuses that same event: a plain right-click over an
// annotation (no pan modifiers, in select mode) opens this menu instead of
// panning; anywhere else falls through to the existing pan behaviour untouched.

let menuEl = null;

function buildMenu() {
  const el = document.createElement("div");
  el.className = "context-menu";
  el.setAttribute("role", "menu");
  const items = [
    { label: "Bring to Front", hint: "Shift+F", action: bringToFront },
    { label: "Bring Forward", hint: "F", action: bringForward },
    { label: "Send Backward", hint: "B", action: sendBackward },
    { label: "Send to Back", hint: "Shift+B", action: sendToBack }
  ];
  items.forEach(({ label, hint, action }) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("role", "menuitem");
    btn.innerHTML = `<span>${label}</span><span class="context-menu-hint">${hint}</span>`;
    btn.addEventListener("click", () => {
      action();
      closeMenu();
    });
    el.appendChild(btn);
  });
  document.body.appendChild(el);
  return el;
}

function closeMenu() {
  if (menuEl) menuEl.style.display = "none";
}

function openMenuAt(clientX, clientY) {
  if (!menuEl) menuEl = buildMenu();
  // Show first so we can measure it, then clamp to the viewport so the menu
  // never spills off the right or bottom edge.
  menuEl.style.display = "block";
  const { offsetWidth: w, offsetHeight: h } = menuEl;
  const x = Math.min(clientX, window.innerWidth - w - 4);
  const y = Math.min(clientY, window.innerHeight - h - 4);
  menuEl.style.left = `${Math.max(4, x)}px`;
  menuEl.style.top = `${Math.max(4, y)}px`;
}

export function initContextMenu() {
  // Runs before the interactions.js contextmenu handler's preventDefault has
  // any visible effect (both just suppress the native menu); we add our own UI.
  canvas.addEventListener("contextmenu", (event) => {
    if (!view.imageLoaded) return;
    // A polygon is mid-placement (first point clicked, not yet closed): the
    // annotator needs every right-click to keep panning around the in-progress
    // shape, not select/reorder some other completed annotation out from under
    // them. Once the shape is closed view.drag reverts to null/other types and
    // right-click selection resumes normally. Selecting a class without having
    // placed a point yet is unaffected — view.drag isn't "draw-polygon" then.
    if (view.drag?.type === "draw-polygon") return;
    // Right-click is also the pan gesture: pointerdown sets view.isPanning and
    // records view.panStart for *every* right-press, so isPanning alone can't
    // tell a tap from a drag. Distinguish by movement — contextmenu carries the
    // release position; if it barely moved from the press origin it was a
    // click (show the menu), otherwise it was a pan-drag (leave it alone).
    if (view.isPanning && view.panStart) {
      const moved = Math.hypot(
        event.clientX - view.panStart.x,
        event.clientY - view.panStart.y
      );
      if (moved > 4) return;
    }

    const point = canvasPoint(event);
    const hitId = hitTest(point);
    if (!hitId) {
      closeMenu();
      return; // empty space: keep the existing (suppressed) pan behaviour
    }

    // Select the clicked annotation unless it is already part of the current
    // selection — then keep the whole selection so the menu reorders the batch.
    if (!state.selectedIds.has(hitId)) {
      state.selectedIds.clear();
      const hitAnnotation = state.annotations.find((a) => a.id === hitId);
      if (hitAnnotation && hitAnnotation.groupId) {
        state.annotations.forEach((a) => {
          if (a.groupId === hitAnnotation.groupId) state.selectedIds.add(a.id);
        });
      } else {
        state.selectedIds.add(hitId);
      }
      state.selectedId = hitId;
      state.mode = "select";
      // Edge hover/selection was measured against the previously selected
      // shape; carrying it over onto a different one points at an edge that
      // may not exist there. Every other selection-changing path in
      // interactions.js clears both indices for the same reason.
      view.hoveredLineIndex = -1;
      view.selectedLineIndex = -1;
      render();
    }

    openMenuAt(event.clientX, event.clientY);
  });

  // Any click, Escape or scroll dismisses the menu.
  document.addEventListener("pointerdown", (event) => {
    if (menuEl && !menuEl.contains(event.target)) closeMenu();
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeMenu();
  });
  window.addEventListener("scroll", closeMenu, true);
  window.addEventListener("resize", closeMenu);
}

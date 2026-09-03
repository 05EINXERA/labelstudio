import { canvas } from "../dom.js?v=1";
import { state, labelDisplayName } from "../state.js?v=2";
import { view } from "./view.js?v=1";
import { render, relabelSelection } from "../components/workspace.js?v=6";
import {
  canvasPoint, hitTest,
  sendToBack, sendBackward, bringToFront, bringForward
} from "./interactions.js?v=4";

// Right-click menu for annotations: change class, and z-order.
//
// The canvas already suppresses the native context menu (interactions.js) so
// right-drag can pan. This reuses that same event: a plain right-click over an
// annotation (no pan modifiers, in select mode) opens this menu instead of
// panning; anywhere else falls through to the existing pan behaviour untouched.
//
// "Change Class" is the deliberate path for relabelling a shape that is already
// drawn. Clicking a class in the sidebar only sets the class for the *next*
// annotation — it used to also retag whatever happened to be selected, which
// annotators kept triggering by accident (see relabelSelection in workspace.js).

let menuEl = null;
let submenuEl = null;

/**
 * Build the "Change Class ▸" row and its submenu.
 *
 * Relabelling a drawn shape lives here rather than on the sidebar class list,
 * where a stray click used to retag the selection silently. The submenu is
 * rebuilt on every open because the project's classes (and which one is
 * current) change underneath it.
 */
function buildChangeClassRow(parent) {
  const row = document.createElement("button");
  row.type = "button";
  row.className = "context-menu-submenu-trigger";
  row.setAttribute("role", "menuitem");
  row.setAttribute("aria-haspopup", "true");
  row.innerHTML = `<span>Change Class</span><span class="context-menu-hint">▸</span>`;

  submenuEl = document.createElement("div");
  submenuEl.className = "context-submenu";
  submenuEl.setAttribute("role", "menu");
  row.appendChild(submenuEl);

  parent.appendChild(row);
  return row;
}

/** Fill the submenu with the current project's classes. */
function renderClassSubmenu() {
  if (!submenuEl) return;
  submenuEl.innerHTML = "";

  if (!state.labels.length) {
    const empty = document.createElement("div");
    empty.className = "context-submenu-empty";
    empty.textContent = "No classes defined";
    submenuEl.appendChild(empty);
    return;
  }

  // The tick marks the class the selection already has. With a mixed selection
  // no single class is current, so nothing is ticked.
  const selected = state.annotations.filter(
    (a) => state.selectedIds.has(a.id) && a.type !== "comment"
  );
  const labelIds = new Set(selected.map((a) => a.labelId));
  const currentId = labelIds.size === 1 ? [...labelIds][0] : null;

  state.labels.forEach((label) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.setAttribute("role", "menuitem");
    const isCurrent = label.id === currentId;
    if (isCurrent) btn.setAttribute("aria-checked", "true");
    btn.innerHTML = `
      <span class="context-submenu-item">
        <span class="swatch" style="background:${label.color || "#65727f"}"></span>
        <span class="context-submenu-name"></span>
      </span>
      <span class="context-menu-hint">${isCurrent ? "✓" : ""}</span>
    `;
    // textContent, not innerHTML: class names are user-supplied.
    btn.querySelector(".context-submenu-name").textContent = labelDisplayName(label);
    btn.addEventListener("click", (event) => {
      event.stopPropagation();
      relabelSelection(label);
      render();
      closeMenu();
    });
    submenuEl.appendChild(btn);
  });
}

function buildMenu() {
  const el = document.createElement("div");
  el.className = "context-menu";
  el.setAttribute("role", "menu");

  buildChangeClassRow(el);

  const divider = document.createElement("div");
  divider.className = "context-menu-divider";
  el.appendChild(divider);

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
  // Classes and the current selection both change between openings.
  renderClassSubmenu();
  // Show first so we can measure it, then clamp to the viewport so the menu
  // never spills off the right or bottom edge.
  menuEl.style.display = "block";
  menuEl.classList.remove("flip-submenu");
  const { offsetWidth: w, offsetHeight: h } = menuEl;
  const x = Math.min(clientX, window.innerWidth - w - 4);
  const y = Math.min(clientY, window.innerHeight - h - 4);
  menuEl.style.left = `${Math.max(4, x)}px`;
  menuEl.style.top = `${Math.max(4, y)}px`;

  // The submenu opens to the right by default; near the right edge that would
  // put the class list off-screen, so flip it to the left of the menu.
  if (submenuEl) {
    const needed = submenuEl.offsetWidth || 180;
    if (Math.max(4, x) + w + needed > window.innerWidth - 4) {
      menuEl.classList.add("flip-submenu");
    }
  }
}

export function initContextMenu() {
  // Runs before the interactions.js contextmenu handler's preventDefault has
  // any visible effect (both just suppress the native menu); we add our own UI.
  canvas.addEventListener("contextmenu", (event) => {
    if (!view.imageLoaded) return;
    
    // Do not show context menu or change selection if the user is in the middle of a drag or drawing a polygon.
    if (view.drag) return;
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

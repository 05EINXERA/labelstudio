// Drag-to-resize for the workspace sidebar. Width lives in the --sidebar-width
// custom property on .app-shell, which grid-template-columns reads, so resizing
// is a single style write rather than a layout rewrite.

const MIN_WIDTH = 220;
const MAX_WIDTH = 520;
const DEFAULT_WIDTH = 300;
const STORAGE_KEY = "sidebarWidth";

// Below this the sidebar becomes an overlay drawer (see the mobile toggle in
// app.html); dragging a width there would fight that behaviour.
const DRAWER_BREAKPOINT = 1024;

const shell = document.querySelector(".app-shell");
const handle = document.querySelector("#sidebarResizeHandle");

function clampWidth(width) {
  return Math.max(MIN_WIDTH, Math.min(MAX_WIDTH, width));
}

function applyWidth(width) {
  shell.style.setProperty("--sidebar-width", `${width}px`);
}

export function initSidebarResize() {
  if (!shell || !handle) return;

  const saved = Number(localStorage.getItem(STORAGE_KEY));
  applyWidth(Number.isFinite(saved) && saved > 0 ? clampWidth(saved) : DEFAULT_WIDTH);

  let dragging = false;

  handle.addEventListener("pointerdown", (event) => {
    if (window.innerWidth <= DRAWER_BREAKPOINT) return;
    dragging = true;
    // Capture so the drag survives the pointer leaving the 6px-wide handle.
    handle.setPointerCapture(event.pointerId);
    document.body.style.cursor = "col-resize";
    // Stops the drag from selecting sidebar text as it sweeps across.
    document.body.style.userSelect = "none";
    event.preventDefault();
  });

  handle.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    // Sidebar starts at the viewport's left edge, so clientX is the width.
    applyWidth(clampWidth(event.clientX));
  });

  function endDrag(event) {
    if (!dragging) return;
    dragging = false;
    if (handle.hasPointerCapture(event.pointerId)) {
      handle.releasePointerCapture(event.pointerId);
    }
    document.body.style.cursor = "";
    document.body.style.userSelect = "";

    const current = shell.style.getPropertyValue("--sidebar-width");
    localStorage.setItem(STORAGE_KEY, parseInt(current, 10) || DEFAULT_WIDTH);

    // The canvas is sized from its container's pixel dimensions, so it has to
    // be told the container changed or it keeps the pre-drag backing size.
    window.dispatchEvent(new Event("resize"));
  }

  handle.addEventListener("pointerup", endDrag);
  handle.addEventListener("pointercancel", endDrag);

  // Double-click restores the default width.
  handle.addEventListener("dblclick", () => {
    applyWidth(DEFAULT_WIDTH);
    localStorage.setItem(STORAGE_KEY, DEFAULT_WIDTH);
    window.dispatchEvent(new Event("resize"));
  });
}

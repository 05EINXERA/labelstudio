import { generateUUID, clamp, round, normalizeClassName, formatTime, clientId } from "./utils.js?v=1";
import { apiFetch, pollJob } from "./api.js?v=1";
import {
  state, snapshot, resetWorkspaceForNewImage
} from "./state.js?v=1";
import { view } from "./canvas/view.js?v=1";
import { commentOverlayRefs } from "./comment-overlay.js?v=1";
import {
  canvas, ctx, imageCanvas, imageCtx, staticCanvas, staticCtx, stageWrap,
  emptyState, drawMode, selectMode, boxMode, polygonMode, commentMode, magicWandMode,
  autoDetectButton, undoButton, redoButton, deleteButton, clearButton, exportLink, saveButton
} from "./dom.js?v=1";
import { drawAllLayers } from "./canvas/draw.js?v=1";
import {
  setStatus, syncToBackend, save, loadSaved, saveDraft, restoreDraft,
  render, manualSaveWithUI
} from "./components/workspace.js?v=1";
import { autoDetectObjects, autoTagObjects } from "./ai/detect.js?v=1";
import {
  syncTaskTime, syncTimeToServer, drainTaskTime, setActiveTaskResolver,
  setConflictHandler, resetSessionForTask, refreshTimerDisplays,
  handleVisibilityChange
} from "./components/timer.js?v=1";
import {
  finalizePolygon, deleteSelected, undoAction, redoAction, setZoomChangeHandler
} from "./canvas/interactions.js?v=1";
import { initContextMenu } from "./canvas/context-menu.js?v=1";
import { initSidebarResize } from "./components/sidebar-resize.js?v=1";
import { initZoomControl, updateZoomDisplay } from "./components/zoom-control.js?v=1";
import { claimTask, heartbeatTask, releaseTask } from "./task-lock.js?v=1";
import { initFftControls } from "./fft-controls.js?v=1";

if (!localStorage.getItem('logged_in')) {
  window.location.href = '/';
}

const breadcrumbProject = document.querySelector("#breadcrumbProject");
const breadcrumbImage = document.querySelector("#breadcrumbImage");
const backToProject = document.querySelector("#backToProject");
const autoTagButton = document.querySelector("#autoTagButton");
const aiSettingsMenuButton = document.querySelector("#aiSettingsMenuButton");
const aiSettingsDropdownContainer = document.querySelector("#aiSettingsDropdownContainer");
const prevImageButton = document.querySelector("#prevImageButton");
const nextImageButton = document.querySelector("#nextImageButton");
const galleryPosition = document.querySelector("#galleryPosition");
const logoutBtnApp = document.querySelector("#logoutBtnApp");

// Flush both counters before the page can go away. The task delta must be
// flushed unconditionally, not only when a debounced save happens to be
// pending — otherwise time accrued after the last autosave was credited to the
// user but never to the task (docs/TIMER_AUDIT.md F2).
function flushPendingSaves({ useBeacon = false } = {}) {
  if (window.backendSyncTimeout) {
    clearTimeout(window.backendSyncTimeout);
    window.backendSyncTimeout = null;
  }
  syncToBackend({ useBeacon });
  syncTimeToServer({ useBeacon });
}

function _releaseCurrentLock({ useBeacon = false } = {}) {
  // T2.2 — release the soft lock on the open task on page hide / unload.
  const task = state.gallery && state.galleryIndex >= 0
    ? state.gallery[state.galleryIndex] : null;
  if (task && task.id) {
    releaseTask(task.id, clientId(), { useBeacon });
  }
}

window.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') {
    flushPendingSaves({ useBeacon: true });
    _releaseCurrentLock({ useBeacon: true });
  }
  // Pauses the session timer on hide and auto-resumes it on return, without
  // touching save-flush/lock-release above (see timer.js for details).
  handleVisibilityChange();
});

window.addEventListener('pagehide', () => {
  flushPendingSaves({ useBeacon: true });
  _releaseCurrentLock({ useBeacon: true });
});

function resizeCanvas() {
  const rect = stageWrap.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  // Rounded, not floored. The canvases are CSS-sized to 100% of .stage-wrap,
  // whose rect is fractional at non-integer zoom / display scaling. Flooring
  // made the backing store up to a device pixel narrower than the box it is
  // painted into, so the browser rescaled the entire canvas by e.g. 1919/1920
  // — a slight blur over the image at every zoom level. Rounding keeps the
  // backing store matched to the display box to within half a device pixel.
  const w = Math.round(rect.width * ratio);
  const h = Math.round(rect.height * ratio);

  // Pin the CSS size to the exact backing-store size in device pixels rather
  // than leaving the stylesheet's width/height:100% to stretch it. With 100%,
  // any backing store that does not match the box is bilinearly rescaled by
  // the browser AFTER we draw — a blur we cannot cancel from inside the 2D
  // context, and the reason crisp (unsmoothed) output looked blurry AND
  // blocky at once. Rounding above can differ from the fractional box by up
  // to half a device pixel, so the two must be tied together explicitly.
  const cssW = w / ratio;
  const cssH = h / ratio;
  for (const el of [imageCanvas, staticCanvas, canvas]) {
    el.style.width = cssW + "px";
    el.style.height = cssH + "px";
  }

  imageCanvas.width = w;
  imageCanvas.height = h;
  imageCtx.setTransform(ratio, 0, 0, ratio, 0, 0);

  staticCanvas.width = w;
  staticCanvas.height = h;
  staticCtx.setTransform(ratio, 0, 0, ratio, 0, 0);

  canvas.width = w;
  canvas.height = h;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);

  if (view.pendingCommentPoint) {
    view.pendingCommentPoint = null;
    commentOverlayRefs.commentOverlay.classList.add("is-hidden");
  }
  drawAllLayers();
}

if (logoutBtnApp) {
  logoutBtnApp.addEventListener("click", async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch (e) { }
    localStorage.removeItem("dataset_username");
    localStorage.removeItem("image-annotation-mvp-v1");
    localStorage.removeItem("logged_in");
    window.location.href = "index.html";
  });
}

function loadImageFromSource(src, name, { autoDetect = false } = {}) {
  view.imageElement = new Image();
  view.imageElement.onload = async () => {
    view.imageLoaded = true;
    emptyState.classList.add("is-hidden");
    if (breadcrumbImage) breadcrumbImage.textContent = name;
    state.image = { src, name, width: view.imageElement.naturalWidth, height: view.imageElement.naturalHeight };
    if (state.galleryIndex >= 0 && state.gallery[state.galleryIndex]) {
      state.gallery[state.galleryIndex].width = view.imageElement.naturalWidth;
      state.gallery[state.galleryIndex].height = view.imageElement.naturalHeight;
    }
    resizeCanvas();
    updateZoomDisplay();
    render();
    if (autoDetect) {
      await autoDetectObjects({ replace: true });
    }
    // No save() here. Saving on image load sent a write with whatever
    // updated_at token was in state at that instant — frequently stale —
    // producing a spurious 409 on every task open. Annotation changes
    // call save() themselves; the timer drain handles time accounting.
    // (Fix for the looping conflict dialog bug — T1.3 regression.)
  };
  view.imageElement.src = src;
}


async function switchImage(index) {
  if (index < 0 || index >= state.gallery.length) return;
  if (state.galleryIndex >= 0 && state.gallery[state.galleryIndex]) {
    const prevTask = state.gallery[state.galleryIndex];
    prevTask.annotations = [...state.annotations];
    // Drains the accumulator against the outgoing task. Bound to prevTask, so
    // it stays correct even though galleryIndex moves before it resolves.
    syncTaskTime(prevTask);
    // T2.2 — release the soft lock on the outgoing task.
    releaseTask(prevTask.id, clientId());
  }
  state.galleryIndex = index;
  // Session time is per-task: the new task starts a fresh session, and the
  // Total readout switches to that task's stored total.
  resetSessionForTask();
  const item = state.gallery[index];

  snapshot();
  resetWorkspaceForNewImage();

  // T1.3 — Hydrate annotations on demand.
  // The gallery list is fetched annotation-free (include_annotations=false),
  // so each task carries an empty annotations array until it is opened. Fetch
  // the full task now — one small request instead of the whole project blob
  // on every page load. The draft wins if it differs from the server copy,
  // which is the correct recovery behaviour (same as before).
  if (item.id) {
    try {
      const res = await apiFetch(`/api/tasks/${item.id}`);
      if (res && res.ok) {
        const detail = await res.json();
        // Write the server copy into the gallery slot so subsequent switches
        // don't re-fetch unnecessarily.
        item.annotations = Array.isArray(detail.annotations) ? detail.annotations : [];
        // Always refresh the concurrency token from the server response —
        // omitting this was the root of the annotation-loss bug.
        if (detail.updated_at) item.updated_at = detail.updated_at;
        if (detail.time_spent != null) item.time_spent = detail.time_spent;
      }
    } catch (e) {
      console.error("Failed to hydrate task annotations:", e);
    }

    // T2.2 — claim the soft lock on the new task.
    // If another annotator already holds it, warn but don't block.
    try {
      const lock = await claimTask(item.id, clientId());
      if (lock.status === 'locked') {
        const secsLeft = lock.seconds_remaining || 60;
        setStatus(`⚠ Task locked (~${secsLeft}s)`);
      }
    } catch (e) {
      // Lock errors are never fatal — annotation can continue.
      console.warn('[task-lock] claim on open failed:', e);
    }
  }

  state.annotations = [...item.annotations];
  // Recover anything this browser had for the task that never reached the
  // server (refresh mid-edit, failed save, unresolved conflict). Applied after
  // the server copy is in place, so it only takes effect when it differs.
  if (restoreDraft(item)) {
    setStatus("Recovered draft");
  }
  loadImageFromSource(item.url, item.name);

  updateGalleryUI();
}

function updateGalleryUI() {
  const total = state.gallery.length;
  const current = state.galleryIndex + 1;
  if (galleryPosition) galleryPosition.textContent = total > 0 ? `${current} / ${total}` : "0 / 0";
  if (prevImageButton) prevImageButton.disabled = current <= 1;
  if (nextImageButton) nextImageButton.disabled = current >= total || total === 0;
}

if (prevImageButton) {
  prevImageButton.addEventListener("click", () => { switchImage(state.galleryIndex - 1); });
}
if (nextImageButton) {
  nextImageButton.addEventListener("click", () => { switchImage(state.galleryIndex + 1); });
}

drawMode.addEventListener("click", () => {
  if (!state.activeLabelId) {
    setStatus("Pick class first");
    render(); // re-render to show the hint in shapeHint
    return;
  }
  state.mode = "draw";
  render();
});

selectMode.addEventListener("click", () => {
  if (view.drag?.type === "draw-polygon") {
    finalizePolygon();
  }
  state.mode = "select";
  render();
});

boxMode.addEventListener("click", () => {
  if (view.drag?.type === "draw-polygon") {
    finalizePolygon();
  }
  state.mode = "draw";
  state.shape = "box";
  render();
});

polygonMode.addEventListener("click", () => {
  state.mode = "draw";
  state.shape = "polygon";
  render();
});

commentMode.addEventListener("click", () => {
  if (view.drag?.type === "draw-polygon") {
    finalizePolygon();
  }
  state.mode = "draw";
  state.shape = "comment";
  render();
});

magicWandMode.addEventListener("click", () => {
  if (view.drag?.type === "draw-polygon") {
    finalizePolygon();
  }
  state.mode = "draw";
  state.shape = "magicWand";
  render();
});

commentOverlayRefs.commentOverlayInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    const text = commentOverlayRefs.commentOverlayInput.value;
    if (text && text.trim() !== "") {
      if (view.pendingCommentEditId) {
        const annotation = state.annotations.find(a => a.id === view.pendingCommentEditId);
        if (annotation) {
          snapshot();
          annotation.text = text.trim();
          render();
          save();
          setStatus("Comment updated");
        }
        view.pendingCommentEditId = null;
        commentOverlayRefs.commentOverlay.classList.add("is-hidden");
      } else if (view.pendingCommentPoint) {
        snapshot();
        const annotation = {
          id: generateUUID(),
          type: "comment",
          text: text.trim(),
          author: localStorage.getItem('dataset_username') || "Unknown",
          x: round(view.pendingCommentPoint.x),
          y: round(view.pendingCommentPoint.y),
          width: 20,
          height: 20,
          points: [
            { x: view.pendingCommentPoint.x - 10, y: view.pendingCommentPoint.y - 10 },
            { x: view.pendingCommentPoint.x + 10, y: view.pendingCommentPoint.y - 10 },
            { x: view.pendingCommentPoint.x + 10, y: view.pendingCommentPoint.y + 10 },
            { x: view.pendingCommentPoint.x - 10, y: view.pendingCommentPoint.y + 10 }
          ]
        };
        state.annotations.push(annotation);
        state.selectedId = annotation.id;
        view.pendingCommentPoint = null;
        commentOverlayRefs.commentOverlay.classList.add("is-hidden");
        render();
        save();
        setStatus("Comment added");
      }
    } else {
      // If empty, treat as cancel
      view.pendingCommentPoint = null;
      view.pendingCommentEditId = null;
      commentOverlayRefs.commentOverlay.classList.add("is-hidden");
      render();
    }
  } else if (e.key === "Escape") {
    e.preventDefault();
    view.pendingCommentPoint = null;
    view.pendingCommentEditId = null;
    commentOverlayRefs.commentOverlay.classList.add("is-hidden");
    render();
  }
});

undoButton.addEventListener("click", () => {
  undoAction();
});

redoButton.addEventListener("click", () => {
  redoAction();
});

deleteButton.addEventListener("click", () => {
  deleteSelected();
});

clearButton.addEventListener("click", () => {
  if (!state.annotations.length) return;
  snapshot();
  state.annotations = [];
  state.selectedId = null;
  view.drag = null;
  render();
  save();
  setStatus("Cleared all");
});

// Save button: manual save with visual feedback
if (saveButton) {
  saveButton.addEventListener("click", () => {
    manualSaveWithUI();
  });
}

// Ctrl+S shortcut: trigger manual save
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "s") {
    e.preventDefault();
    if (saveButton) {
      manualSaveWithUI();
    }
  }
});

if (aiSettingsMenuButton) {
  aiSettingsMenuButton.addEventListener("click", (e) => {
    e.stopPropagation();
    aiSettingsDropdownContainer.classList.toggle("show");
  });
}
document.addEventListener("click", (e) => {
  if (aiSettingsDropdownContainer && !aiSettingsDropdownContainer.contains(e.target)) {
    aiSettingsDropdownContainer.classList.remove("show");
  }
});

// --- Move Objects lock/unlock toggle -----------------------------------
const moveObjectsDropdownContainer = document.querySelector("#moveObjectsDropdownContainer");
const moveObjectsMenuButton = document.querySelector("#moveObjectsMenuButton");
const moveObjectsToggle = document.querySelector("#moveObjectsToggle");

// Keep the toolbar button, the switch, and the hint in sync with state so the
// lock status reads the same everywhere it is shown.
function renderMoveObjectsUI() {
  const on = state.moveObjectsUnlocked;
  if (moveObjectsToggle) {
    moveObjectsToggle.classList.toggle("is-on", on);
    moveObjectsToggle.setAttribute("aria-checked", on ? "true" : "false");
  }
  if (moveObjectsMenuButton) {
    const icon = moveObjectsMenuButton.querySelector(".btn-icon");
    const label = moveObjectsMenuButton.querySelector(".btn-label");
    if (icon) icon.textContent = on ? "🔓" : "🔒";
    if (label) label.textContent = on ? "Unlocked" : "Locked";
    moveObjectsMenuButton.classList.toggle("is-active", on);
  }
}

if (moveObjectsMenuButton) {
  moveObjectsMenuButton.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = moveObjectsDropdownContainer.classList.toggle("show");
    moveObjectsMenuButton.setAttribute("aria-expanded", open ? "true" : "false");
  });
}
if (moveObjectsToggle) {
  moveObjectsToggle.addEventListener("click", (e) => {
    e.stopPropagation();
    state.moveObjectsUnlocked = !state.moveObjectsUnlocked;
    renderMoveObjectsUI();
    setStatus(state.moveObjectsUnlocked ? "Move: On" : "Move: Off");
  });
}
document.addEventListener("click", (e) => {
  if (moveObjectsDropdownContainer && !moveObjectsDropdownContainer.contains(e.target)) {
    moveObjectsDropdownContainer.classList.remove("show");
    if (moveObjectsMenuButton) moveObjectsMenuButton.setAttribute("aria-expanded", "false");
  }
});
renderMoveObjectsUI();

autoDetectButton.addEventListener("click", () => autoDetectObjects({ replace: true }));
if (autoTagButton) {
  autoTagButton.addEventListener("click", () => autoTagObjects());
}

// Images are loaded from the project page, not dropped onto the canvas, so the
// drop target only suppresses the browser's default navigate-to-file.
stageWrap.addEventListener("dragover", (event) => {
  event.preventDefault();
});

stageWrap.addEventListener("drop", (event) => {
  event.preventDefault();
});

window.addEventListener("resize", resizeCanvas);

// The window `resize` event alone is not enough. .stage-wrap is a flex child
// (flex: 1 1 auto; min-height: 0), so its box changes without the window
// changing size at all: the toolbar wrapping to a second row, the annotation
// list growing, a scrollbar appearing. Each of those left the backing store
// sized for the OLD box while CSS stretched the canvas to the new one, and the
// browser bilinearly resampled the whole layer on top of whatever we drew —
// blur that no imageSmoothing setting inside the 2D context can undo.
if (typeof ResizeObserver !== "undefined") {
  // Guarded: resizeCanvas writes inline width/height on the canvases, which is
  // itself a layout change inside .stage-wrap. Without the no-op check that
  // feeds back into the observer as a "ResizeObserver loop" warning.
  let lastW = 0;
  let lastH = 0;
  new ResizeObserver(() => {
    const rect = stageWrap.getBoundingClientRect();
    if (rect.width === lastW && rect.height === lastH) return;
    lastW = rect.width;
    lastH = rect.height;
    resizeCanvas();
  }).observe(stageWrap);
}

// Note: there is deliberately no cross-tab `storage` listener that reloads
// annotations. Drafts are per-task and per-tab now; the old listener watched a
// single global key, so a second tab editing a different task would overwrite
// this tab's in-memory annotations with unrelated ones.

// A genuine conflict means another browser wrote this task since we loaded it.
// The user decides: keep editing (and overwrite on the next save) or reload
// the server's copy. Either way their work stays in the local draft, so the
// destructive old behaviour — disabling saves outright — cannot recur.
setConflictHandler((task) => {
  saveDraft();
  const reload = confirm(
    "This task was changed by someone else while you were working.\n\n" +
    "OK — reload their version (your unsaved work stays recoverable).\n" +
    "Cancel — keep your version and overwrite on the next save."
  );
  if (reload) {
    window.location.reload();
  } else {
    // Null token: next save is accepted as a deliberate overwrite rather than
    // looping on the same conflict.
    task.updated_at = null;
    setStatus("Keeping version (will overwrite)");
  }
});

loadSaved();
resizeCanvas();
render();

// --- Settings Menu Logic ---
const openSettingsBtn = document.getElementById("openSettingsBtn");
const settingsModal = document.getElementById("settingsModal");
const settingsClose = document.getElementById("settingsClose");
const settingsUsernameInput = document.getElementById("settingsUsernameInput");
const saveUsernameBtn = document.getElementById("saveUsernameBtn");
const exportDataBtn = document.getElementById("exportDataBtn");
const importDataInput = document.getElementById("importDataInput");
const clearDataBtn = document.getElementById("clearDataBtn");

// AI Settings elements
const aiModelSize = document.getElementById("settingsAiModelSize");
const aiSamModel = document.getElementById("settingsAiSamModel");


const dropdownAiConf = document.getElementById("dropdownAiConf");
const dropdownAiConfVal = document.getElementById("dropdownAiConfVal");
const dropdownAiNms = document.getElementById("dropdownAiNms");
const dropdownAiNmsVal = document.getElementById("dropdownAiNmsVal");
const dropdownSaveAiSettingsBtn = document.getElementById("dropdownSaveAiSettingsBtn");



if (dropdownAiConf) {
  dropdownAiConf.value = localStorage.getItem("ai_conf") || "0.35";
  if (dropdownAiConfVal) dropdownAiConfVal.textContent = dropdownAiConf.value;
  dropdownAiConf.addEventListener('input', e => { if (dropdownAiConfVal) dropdownAiConfVal.textContent = e.target.value; });
}
if (dropdownAiNms) {
  dropdownAiNms.value = localStorage.getItem("ai_nms") || "0.45";
  if (dropdownAiNmsVal) dropdownAiNmsVal.textContent = dropdownAiNms.value;
  dropdownAiNms.addEventListener('input', e => { if (dropdownAiNmsVal) dropdownAiNmsVal.textContent = e.target.value; });
}


if (aiModelSize) {
  aiModelSize.value = localStorage.getItem("ai_model_size") || "n";
  aiModelSize.addEventListener('change', e => {
    localStorage.setItem("ai_model_size", e.target.value);
    setStatus("Model size changed");
  });
}

if (aiSamModel) {
  aiSamModel.value = localStorage.getItem("ai_sam_model") || "mobile_sam.pt";
  aiSamModel.addEventListener('change', e => {
    localStorage.setItem("ai_sam_model", e.target.value);
    setStatus("Magic Wand updated");
  });
}

if (openSettingsBtn) {
  openSettingsBtn.addEventListener("click", () => {
    settingsUsernameInput.value = localStorage.getItem("dataset_username") || "";



    settingsModal.classList.add("is-active");
  });
}



if (dropdownSaveAiSettingsBtn) {
  dropdownSaveAiSettingsBtn.addEventListener("click", () => {
    localStorage.setItem("ai_model_size", aiModelSize.value);
    localStorage.setItem("ai_sam_model", aiSamModel.value);
    localStorage.setItem("ai_conf", dropdownAiConf.value);
    localStorage.setItem("ai_nms", dropdownAiNms.value);

    setStatus("Settings applied");
    // Dropdown will close automatically if it loses focus, or we just leave it open.
  });
}

if (settingsClose) {
  settingsClose.addEventListener("click", () => {
    settingsModal.classList.remove("is-active");
  });
}

if (settingsModal) {
  settingsModal.addEventListener("click", (e) => {
    if (e.target === settingsModal) settingsModal.classList.remove("is-active");
  });
}

if (saveUsernameBtn) {
  saveUsernameBtn.addEventListener("click", () => {
    const newName = settingsUsernameInput.value.trim();
    if (newName) {
      localStorage.setItem("dataset_username", newName);
      const displayUsername = document.getElementById("displayUsername");
      if (displayUsername) displayUsername.textContent = newName;
      setStatus("Username saved");
    }
  });
}

if (exportDataBtn) {
  exportDataBtn.addEventListener("click", () => {
    const backup = {
      workspace: localStorage.getItem("image-annotation-mvp-v1"),
      team: localStorage.getItem("dataset_team"),
      tasks: localStorage.getItem("dataset_tasks"),
      username: localStorage.getItem("dataset_username")
    };
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(backup));
    const downloadAnchor = document.createElement("a");
    downloadAnchor.setAttribute("href", dataStr);
    downloadAnchor.setAttribute("download", "workspace_backup.json");
    document.body.appendChild(downloadAnchor);
    downloadAnchor.click();
    downloadAnchor.remove();
    setStatus("Data exported");
  });
}

if (importDataInput) {
  importDataInput.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = (event) => {
      try {
        const backup = JSON.parse(event.target.result);
        if (backup.workspace) localStorage.setItem("image-annotation-mvp-v1", backup.workspace);
        if (backup.team) localStorage.setItem("dataset_team", backup.team);
        if (backup.tasks) localStorage.setItem("dataset_tasks", backup.tasks);
        if (backup.username) localStorage.setItem("dataset_username", backup.username);

        alert("Workspace imported successfully! The page will now reload.");
        window.location.reload();
      } catch (err) {
        alert("Invalid backup file.");
        console.error(err);
      }
    };
    reader.readAsText(file);
  });
}

if (clearDataBtn) {
  clearDataBtn.addEventListener("click", () => {
    if (confirm("WARNING: This will permanently delete all your local annotations, tasks, and settings! Are you absolutely sure?")) {
      localStorage.clear();
      window.location.href = "index.html"; // Go back to login since username is cleared
    }
  });
}

// T3.2 — Annotator name prompt: advisory free-text, no server validation.
// Under the shared account we can't verify names against /api/team because
// everyone logs in as the same user; the name is purely a local label used
// for display and assignment convention.
const teamValidationForm = document.getElementById("teamValidationForm");
if (teamValidationForm) {
  teamValidationForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const nameInput = document.getElementById("teamValidationName").value.trim();
    if (!nameInput) return;

    localStorage.setItem('dataset_username', nameInput);

    const displayUser = document.getElementById("displayUsername");
    if (displayUser) displayUser.textContent = nameInput;

    document.getElementById("teamValidationModal").classList.remove("is-active");
    const userPanel = document.getElementById("userPanel");
    if (userPanel) userPanel.style.display = "block";
  });
}


async function fetchLabels() {
  if (!projectId) {
    // No project context: never show classes cached from a previous project/user.
    state.labels = [];
    render();
    return;
  }
  try {
    const res = await apiFetch(`/api/labels?projectId=${projectId}`);
    if (res.ok) {
      const labels = await res.json();
      state.labels = labels;
      render();
    }
  } catch (err) {
    console.error("Failed to fetch labels from backend:", err);
  }
}

// Workspace Project Support
const urlParams = new URLSearchParams(window.location.search);
const projectId = urlParams.get('projectId');

// Points the back arrow at the project this workspace was opened from, and
// fills the breadcrumb's project half.
async function initWorkspaceContext() {
  if (!projectId) return;

  if (backToProject) {
    backToProject.href = `project.html?id=${projectId}#/tasks`;
  }
  if (exportLink) {
    exportLink.href = `project.html?id=${projectId}#/exports`;
  }

  try {
    const res = await apiFetch('/api/projects');
    if (!res.ok) return;
    const projects = await res.json();
    const project = projects.find(p => String(p.id) === String(projectId));
    if (project && breadcrumbProject) {
      breadcrumbProject.textContent = project.name;
      breadcrumbProject.title = project.name;
    }
  } catch (e) {
    console.error("Failed to resolve project name for breadcrumb", e);
  }
}

async function loadWorkspaceTasks() {
  if (!projectId) return;
  try {
    // T1.2 — Fetch the gallery shell without annotation blobs. At 25 clients
    // each refreshing at shift start, shipping every task's full annotation
    // JSON through one process was O(project size × clients). The per-task
    // detail endpoint (GET /api/tasks/{id}) hydrates annotations when the
    // task is actually opened (switchImage → T1.3).
    const res = await apiFetch(`/api/tasks?projectId=${projectId}&include_annotations=false`);
    if (res.ok) {
      const tasks = await res.json();
      state.gallery = tasks.map(t => ({
        id: t.id,
        name: t.description,
        url: "/" + t.image_path.replace(/\\/g, "/"),
        // Starts empty; hydrated on open via GET /api/tasks/{id}.
        annotations: [],
        width: 0,
        height: 0,
        status: t.status,
        assignee: t.assignee,
        // Persisted per-task total; the workspace "Total" readout is scoped to
        // the open task, so it needs this as its base.
        time_spent: t.time_spent || 0,
        // Optimistic-concurrency token — refreshed from the detail fetch on
        // open, but initialised here so the field is always present.
        updated_at: t.updated_at || null
      }));

      if (state.gallery.length > 0) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        let initialIndex = 0;
        const targetTaskId = urlParams.get('taskId');
        if (targetTaskId) {
          const foundIndex = state.gallery.findIndex(t => t.id == targetTaskId);
          if (foundIndex !== -1) initialIndex = foundIndex;
        }
        await switchImage(initialIndex);
      } else {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        updateGalleryUI();
        // No task open: the Total readout has nothing to show.
        refreshTimerDisplays();
      }
    }
  } catch (e) {
    console.error(e);
  }
}


document.addEventListener('DOMContentLoaded', () => {
  initSidebarResize();
  initContextMenu();
  setZoomChangeHandler(updateZoomDisplay);
  initZoomControl();
  // Resolves the open task, or null. Task time is only billed while a task is
  // actually open (F8), and Stop uses this to flush the right task (F6).
  setActiveTaskResolver(() => {
    if (typeof state === 'undefined' || !state) return null;
    if (state.galleryIndex < 0 || !state.gallery) return null;
    return state.gallery[state.galleryIndex] || null;
  });

  // T2.2 — heartbeat: refresh the soft lock every 30 s while a task is open.
  setInterval(() => {
    const task = state.gallery && state.galleryIndex >= 0
      ? state.gallery[state.galleryIndex] : null;
    if (task && task.id) {
      heartbeatTask(task.id, clientId()).catch(() => {});
    }
  }, 30_000);

  initWorkspaceContext();
  fetchLabels();
  if (projectId) {
    loadWorkspaceTasks();
  }

  const completeTaskBtn = document.getElementById('completeTaskBtn');
  if (completeTaskBtn) {
    completeTaskBtn.addEventListener('click', async () => {
      console.log("Complete Task button clicked!");
      if (state.gallery.length === 0) {
        alert("No image to complete!");
        return;
      }
      const currentTask = state.gallery[state.galleryIndex];

      // Only update if it has an id
      if (currentTask.id) {
        try {
          // Single drain point handles the time delta and retries it on
          // failure (docs/TIMER_AUDIT.md F3/F4).
          await drainTaskTime(currentTask, {
            status: 'Completed',
            annotations: state.annotations
          });
          currentTask.status = 'Completed';

          const tcModal = document.getElementById('taskCompletedModal');
          if (tcModal) tcModal.classList.add('is-active');
        } catch (e) {
          console.error(e);
          alert('Failed to mark task as completed.');
        }
      } else {
        // For local tasks, simply show the completion modal so they can continue
        const tcModal = document.getElementById('taskCompletedModal');
        if (tcModal) tcModal.classList.add('is-active');
      }
    });
  }
});


const tcModal = document.getElementById('taskCompletedModal');
const tcClose = document.getElementById('taskCompletedClose');
const tcOk = document.getElementById('taskCompletedOkBtn');

function closeTaskCompletedModal() {
  if (tcModal) tcModal.classList.remove('is-active');
  if (state.galleryIndex < state.gallery.length - 1) {
    switchImage(state.galleryIndex + 1); // async; no await needed here — fire and continue
  }
}

if (tcClose) tcClose.addEventListener('click', closeTaskCompletedModal);
if (tcOk) tcOk.addEventListener('click', closeTaskCompletedModal);

// Initialise FFT smoothing controls (Smooth button, slider, auto-smooth toggle).
initFftControls();

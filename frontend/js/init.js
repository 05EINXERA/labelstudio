import { generateUUID, clamp, round, normalizeClassName, formatTime, clientId } from "./utils.js?v=1";
import { apiFetch, pollJob } from "./api.js?v=2";
import {
  state, snapshot, resetWorkspaceForNewImage,
  beginHydration, completeHydration, failHydration, hydrationOk, hydrationFailed,
  hydrationSaveBlock, currentHydrationGeneration, noteHydratedAnnotationCount
} from "./state.js?v=5";
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
  render, manualSaveWithUI, refreshSaveStatus, pruneStaleDrafts
} from "./components/workspace.js?v=7";
import {
  configureQueue, startQueue, subscribe as subscribeQueue, drainQueue,
  enqueueWrite, retryablePendingCount, noteServerReachable, noteServerUnreachable,
  peekWrite as peekQueuedWrite, discardWrite as discardQueuedWrite
} from "./offline-queue.js?v=4";
import { autoDetectObjects, autoTagObjects } from "./ai/detect.js?v=1";
import {
  syncTaskTime, syncTimeToServer, drainTaskTime, setActiveTaskResolver,
  setConflictHandler, resetSessionForTask, refreshTimerDisplays,
  handleVisibilityChange
} from "./components/timer.js?v=3";
import {
  finalizePolygon, deleteSelected, undoAction, redoAction, setZoomChangeHandler
} from "./canvas/interactions.js?v=3";
import { initContextMenu } from "./canvas/context-menu.js?v=1";
import { getCurrentUser } from "./session.js?v=1";
import { canReview } from "./permissions.js?v=1";
import {
  applyReadOnlyMode, isReadOnly, loadProjectPermissions, renderReviewControls,
  reportSaveForbidden, reportSaveRefused, setMyTeams, setMyUserId, updateTaskBanner,
  renderStatusDropdown, renderSaveSplitMenu, updateTaskStatusPill, currentRole,
  taskWriteBlock,
} from "./canvas-permissions.js?v=6";
import { initSidebarResize } from "./components/sidebar-resize.js?v=1";
import { initZoomControl, updateZoomDisplay } from "./components/zoom-control.js?v=1";
import { claimTask, heartbeatTask, releaseTask } from "./task-lock.js?v=2";
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


/**
 * Surface a hydration failure with a retry the user can actually take.
 *
 * The failure state is sticky and destructive-adjacent: saves stay blocked
 * until a fetch succeeds, so a message that merely says "click to retry"
 * without wiring anything (as the first cut of this fix did) strands the task
 * for the rest of the session. This renders a real button that re-runs
 * switchImage for the same index, which takes a fresh generation and retries
 * the fetch. If it succeeds the gate opens and saving resumes; if it fails
 * again the button comes back.
 */
function showHydrationFailure(index) {
  const host = document.querySelector("#saveStatus");
  if (!host) {
    setStatus("⚠ Failed to load task annotations — reload the page");
    return;
  }
  // setStatus schedules a 3s revert to the resting message; cancel it so the
  // failure notice does not quietly disappear while saves remain blocked.
  window.clearTimeout(setStatus.timer);
  host.textContent = "⚠ Could not load annotations — saving is disabled. ";
  const retry = document.createElement("button");
  retry.type = "button";
  retry.textContent = "Retry";
  retry.style.cssText =
    "background:none;border:none;padding:0;color:inherit;font:inherit;" +
    "text-decoration:underline;cursor:pointer;";
  retry.addEventListener("click", () => {
    host.textContent = "Retrying…";
    switchImage(index);
  });
  host.appendChild(retry);
}

async function switchImage(index) {
  if (index < 0 || index >= state.gallery.length) return;

  // Claim the hydration generation before anything else, and in particular
  // before `state.galleryIndex` moves below. Between the index moving and the
  // fetch landing, the open task is the *new* one while `state.annotations` is
  // still the outgoing (or, after the reset, an empty) set — the exact window
  // in which a fast Ctrl+S used to save `[]` over real work. Taking the
  // generation first means every save path is closed for the whole of that
  // window, including the awaited drain of the outgoing task below.
  //
  // It also supersedes any switchImage still in flight: a stale call's
  // completeHydration() is refused, so rapid paging cannot mark the newest
  // task hydrated on the strength of an older task's fetch.
  const generation = beginHydration();

  if (state.galleryIndex >= 0 && state.gallery[state.galleryIndex]) {
    const prevTask = state.gallery[state.galleryIndex];
    prevTask.annotations = [...state.annotations];
    // Drains the accumulator against the outgoing task. Bound to prevTask, so
    // it stays correct even though galleryIndex moves before it resolves.
    //
    // Awaited, and the result checked. Discarding it was gap G2: an annotator
    // paging through images during an outage left a trail of tasks whose writes
    // had failed and which nothing would ever retry. drainTaskTime queues the
    // failed payload itself, so all this needs to do is keep the draft (the
    // per-task safety net) alive and tell the user.
    const saved = await syncTaskTime(prevTask, { annotations: prevTask.annotations });
    if (saved === false) {
      // Name the task and the set explicitly. The hydration gate is already
      // shut for the *incoming* task by this point, but the work being
      // rescued belongs to the outgoing one and is genuine — an implicit
      // saveDraft() would read the shut gate and discard it.
      saveDraft({ task: prevTask, annotations: prevTask.annotations });
      refreshSaveStatus();
    }
    // T2.2 — release the soft lock on the outgoing task.
    releaseTask(prevTask.id, clientId());
  }
  state.galleryIndex = index;
  // Session time is per-task: the new task starts a fresh session, and the
  // Total readout switches to that task's stored total.
  resetSessionForTask();
  const item = state.gallery[index];

  // No snapshot() here. Switching tasks is not an undoable edit, and taking one
  // was actively harmful: it pushed the outgoing (about to be emptied) state
  // onto the stack, so the first Ctrl+Z on the freshly-opened task restored an
  // empty annotation array over the hydrated work and saved the wipe.
  // resetWorkspaceForNewImage() clears the stack instead — undo is per-session.
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
      // A newer switchImage has taken over while this fetch was in flight.
      // Abandon quietly: writing into `item`/`state` now would drop this
      // task's annotations onto the canvas of a different one.
      if (generation !== currentHydrationGeneration()) return;
      if (res && res.ok) {
        const detail = await res.json();
        if (generation !== currentHydrationGeneration()) return;
        // Write the server copy into the gallery slot so subsequent switches
        // don't re-fetch unnecessarily.
        item.annotations = Array.isArray(detail.annotations) ? detail.annotations : [];
        // Always refresh the concurrency token from the server response —
        // omitting this was the root of the annotation-loss bug.
        if (detail.updated_at) item.updated_at = detail.updated_at;
        if (detail.time_spent != null) item.time_spent = detail.time_spent;
        // Refresh assignment fields and the authoritative can_write flag so
        // the permission banner is always accurate for the task just opened —
        // including mid-session reassignments.
        item.assignee_user_id   = detail.assignee_user_id   ?? null;
        item.assignee_name      = detail.assignee_name      ?? null;
        item.assigned_team_id   = detail.assigned_team_id   ?? null;
        item.assigned_team_name = detail.assigned_team_name ?? null;
        item.can_write          = detail.can_write          ?? null;
        // Record what the server actually held, so a later empty save can be
        // told apart from a canvas that simply never got populated.
        noteHydratedAnnotationCount(item.annotations.length);
        completeHydration(generation);
      } else {
        failHydration(generation);
        showHydrationFailure(index);
      }
    } catch (e) {
      console.error("Failed to hydrate task annotations:", e);
      failHydration(generation);
      showHydrationFailure(index);
    }

    // T2.2 — claim the soft lock on the new task.
    // If another annotator already holds it, warn but don't block.
    try {
      const lock = await claimTask(item.id, clientId());
      // Another switch superseded this one during the claim: stop before the
      // canvas assignment below, which would otherwise paint this task's
      // annotations over whichever task is now open.
      if (generation !== currentHydrationGeneration()) return;
      // Don't overwrite the hydration-failure notice (and its Retry button)
      // with a lock warning — the failure is the more important state, and it
      // is the one gating saves.
      if (lock.status === 'locked' && !hydrationFailed()) {
        const secsLeft = lock.seconds_remaining || 60;
        setStatus(`⚠ Task locked (~${secsLeft}s)`);
      }
    } catch (e) {
      // Lock errors are never fatal — annotation can continue.
      console.warn('[task-lock] claim on open failed:', e);
      if (generation !== currentHydrationGeneration()) return;
    }
  } else {
    // No server id yet, so there is nothing to hydrate and nothing on the
    // server that a save could overwrite. Open the gate explicitly — leaving
    // it shut would block saving this task forever, and the gate must fail
    // closed only where a wipe is actually possible.
    completeHydration(generation);
  }

  // On a failed hydration `item.annotations` is not the server's set — it is
  // whatever the annotation-free gallery load left there (`[]`) or a stale
  // copy from a previous open. Painting it would present emptiness as if it
  // were this task's work; the save gate is already shut, so leave the canvas
  // as the reset left it and let the Retry button drive recovery.
  if (hydrationFailed()) {
    loadImageFromSource(item.url, item.name);
    updateGalleryUI();
    refreshTaskPermissionUI();
    return;
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
  // Re-evaluate the banner and review buttons for the task just opened: both
  // are per-task, and the assignment banner in particular must be on screen
  // *before* any drawing happens rather than after the first refused save.
  refreshTaskPermissionUI();
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
          // Real identity when we have it. The localStorage value is a name the
          // user typed into a prompt (rule 14) and is kept only as a fallback
          // for a cached bundle mid-rollout; Phase 5 (F3) removes it.
          author: currentUser?.username || localStorage.getItem('dataset_username') || "Unknown",
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
  const total = state.annotations.length;
  if (!total) return;

  // Clear-all is the most destructive control on the canvas: one click deletes
  // every shape on the task and — unlike deleting them individually — there is
  // no partial result to notice before the save goes out. It is also adjacent
  // to Delete (selected) in the toolbar, so a misclick wipes the whole image.
  //
  // Native confirm() rather than a styled modal to match every other
  // destructive action in this codebase (project/task/class delete, leaving a
  // team) and because it is synchronous: the clear must not begin until the
  // answer is known, and an async modal here would mean restructuring the
  // save path for one dialog.
  const shapes = `${total} annotation${total === 1 ? "" : "s"}`;
  if (!confirm(
    `Delete all ${shapes} on this image?\n\n` +
    "You can undo this with Ctrl+Z as long as you stay on this task."
  )) return;

  snapshot();
  state.annotations = [];
  state.selectedId = null;
  view.drag = null;
  render();
  // A deliberate delete-all. Without allowClear the server's clear-guard refuses
  // this save and the annotator is told their "offline work could not be saved"
  // for something they just chose to do.
  save({ allowClear: true });
  setStatus(`Cleared ${shapes}`);
});

// Save button: manual save with visual feedback
if (saveButton) {
  saveButton.addEventListener("click", () => {
    manualSaveWithUI();
  });
}

// Ctrl+S shortcut: trigger manual save.
//
// The read-only check is load-bearing, not decorative. The Save button is
// disabled purely by CSS (`body.is-read-only #saveButton { pointer-events:none }`),
// which stops a click but does nothing to a keydown handler bound on `document`
// — so Ctrl+S was the one way a user viewing a task assigned to someone else
// could still fire a save. The write was correctly refused by the server, but
// the resulting 403 stranded a permanently-unretryable entry in the offline
// queue and told them their work "could not be saved", which read as data loss.
// Refusing here means the doomed write is never created in the first place.
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key === "s") {
    e.preventDefault();
    if (!saveButton) return;
    const task = state.gallery?.[state.galleryIndex] || null;
    const block = taskWriteBlock(task);
    if (block) {
      setStatus("Read-only — not saved");
      return;
    }
    // Ctrl+S is the fastest path to a save — fast enough to land inside the
    // task-switch window before annotations have hydrated, which is exactly
    // how `[]` reached the server. The gate is positive and opens only on a
    // confirmed fetch, so pressing Ctrl+S mid-switch is refused rather than
    // racing. manualSaveWithUI re-checks it too; this check exists to give
    // the keyboard user the reason instead of a silent no-op.
    const hydrationBlock = hydrationSaveBlock();
    if (hydrationBlock) {
      setStatus(hydrationBlock);
      return;
    }
    manualSaveWithUI();
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

  // "Keep mine" is only a real choice when "mine" is a trustworthy set. If the
  // canvas is empty, or the open task never hydrated, keeping it means writing
  // emptiness (or a never-loaded view) over whatever the other writer saved —
  // a wipe dressed up as a user decision. Reload instead, without offering it.
  //
  // Note the honest wording: saveDraft() above declines to write while the
  // hydration gate is shut, so in the unhydrated case there is no recoverable
  // draft. Discarding an unhydrated canvas is the correct trade against
  // destroying confirmed server work, but the dialog must not promise safety
  // it is not delivering.
  if (state.annotations.length === 0 || !hydrationOk()) {
    alert(
      "This task was changed by someone else.\n\n" +
      "Your workspace has no confirmed annotations to keep, so saving now would " +
      "erase their work. The page will reload to fetch the current version."
    );
    window.location.reload();
    return;
  }

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

// --- Offline outbox (.devnotes/offline/01_OFFLINE_RESILIENCE_PLAN.md) --------
//
// Teach the queue how to perform a write and what to do with a conflict. Done
// here rather than inside offline-queue.js so that module stays free of imports
// from timer.js / workspace.js — timer.js enqueues into it, which would
// otherwise be a cycle.
configureQueue({
  async send(payload) {
    try {
      const res = await apiFetch('/api/tasks', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      // apiFetch returns undefined after redirecting an unauthenticated user.
      // Not a transport failure, but not a success either — leave it queued.
      if (!res) return { ok: false };
      if (res.status === 409) return { ok: false, conflict: true };
      // E-27: a permission failure is not a transport failure. Retrying it
      // forever pins the offline banner open against a server that is answering
      // perfectly well, and the answer will never change on its own — someone
      // has to grant access. Surfaced like a conflict (reported once, retries
      // stopped) and, critically, the payload stays queued: a permission error
      // must never cost the annotator their work.
      if (res.status === 403) {
        const body = await res.json().catch(() => null);
        return {
          ok: false,
          forbidden: true,
          detail: (body && body.detail) || 'You no longer have permission to edit this task.',
        };
      }
      // 422: the server refused this specific payload on the merits — right
      // now, exclusively "this would silently erase existing annotations"
      // (api/routers/tasks.py, see .devnotes/offline/INCIDENT_692.md). Handled
      // exactly like a 403: not retryable (resending the same empty payload
      // would refuse identically forever), and the payload must NOT be
      // dropped — it is still the annotator's real unsaved work, just not
      // safe to auto-apply. Reuses the `forbidden` queue state rather than
      // adding a parallel one, since the required behavior (stop retrying,
      // keep payload, tell the user once) is identical.
      // 422: the server refused this specific payload on the merits — right
      // now, exclusively "this would silently erase existing annotations".
      // Shares the queue's un-retryable handling with 403 (resending an
      // identical payload would be refused identically forever, and the work
      // must be kept either way) but is reported through its own channel: it is
      // not a permission failure and saying so sent annotators chasing access
      // problems that did not exist.
      if (res.status === 422) {
        const body = await res.json().catch(() => null);
        return {
          ok: false,
          forbidden: true,
          refused: true,
          detail: (body && body.detail) || 'This save was refused; reload the task and check your changes.',
        };
      }
      if (!res.ok) return { ok: false };

      const data = await res.json().catch(() => null);
      // Fold the replayed write into the live task object if it is still loaded,
      // so the Total readout and the concurrency token both stay truthful
      // without needing a reload.
      const task = (state.gallery || []).find(t => t && t.id === payload.id);
      if (task) {
        if (payload.time_spent_delta) {
          task.time_spent = (task.time_spent || 0) + payload.time_spent_delta;
        }
        if (data && data.updated_at) task.updated_at = data.updated_at;
      }
      return { ok: true, updated_at: data && data.updated_at };
    } catch (e) {
      return { ok: false };
    }
  },

  onForbidden(taskId, payload, detail, { refused = false } = {}) {
    // E-27: a queued write the server refused on permission grounds. Unlike a
    // conflict there is no choice to offer — overwriting is not an option the
    // user has — so this only informs them.
    //
    // The queued payload and the per-task draft are both left in place
    // (rule 18): a permission error must never destroy unsaved work. If access
    // is restored, the next drain replays it.
    const task = (state.gallery || []).find(t => t && t.id === taskId);
    const label = task ? (task.name || `task ${taskId}`) : `task ${taskId}`;
    // Use the permission banner rather than a native alert: the banner stays
    // visible while the annotator keeps working, and a blocking dialog mid-
    // session is disruptive (and unexpected when they're on their own task).
    //
    // A 422 is reported as what it is. Calling it "offline work" that "could not
    // be saved" for lack of permission — as this did for every non-403 refusal —
    // described a network outage and an access revocation to a user who was
    // experiencing neither.
    if (refused) {
      reportSaveRefused(
        `Your change to "${label}" was not saved. ` +
        (detail || 'The server refused that save.')
      );
      return;
    }
    reportSaveForbidden(
      `Your unsaved work on "${label}" could not be saved. ` +
      (detail || 'You no longer have permission to edit this task.')
    );
  },

  onConflict(taskId) {
    // A queued write for a task someone else has since edited. Reuse the same
    // decision the live path offers, but scoped to the task in question.
    const task = (state.gallery || []).find(t => t && t.id === taskId);
    const label = task ? (task.name || `task ${taskId}`) : `task ${taskId}`;
    const overwrite = confirm(
      `Your offline work on ${label} conflicts with a change made by someone else.\n\n` +
      "OK — overwrite with your version.\n" +
      "Cancel — keep theirs and discard your offline copy for this task."
    );
    if (overwrite) {
      // Null token = deliberate overwrite; the next drain pass will be accepted.
      const entry = peekQueuedWrite(taskId);
      if (entry) {
        entry.payload.updated_at = null;
        enqueueWrite(entry.payload);
      }
      drainQueue();
    } else {
      discardQueuedWrite(taskId);
    }
  }
});

// Keep the save indicator and the offline banner in step with the queue.
const offlineBanner = document.getElementById('offlineBanner');
const offlineBannerText = document.getElementById('offlineBannerText');

subscribeQueue(({ pending, unreachable }) => {
  refreshSaveStatus();
  if (!offlineBanner) return;
  // Only count entries that are still being retried. A forbidden (403) or
  // conflicted entry is deliberately not retried — showing "Retrying N unsaved
  // changes" for it pins a scary banner on tasks the user CAN annotate (E-27).
  const retryable = retryablePendingCount();
  const show = unreachable || retryable > 0;
  offlineBanner.classList.toggle('is-active', show);
  if (show && offlineBannerText) {
    const plural = retryable === 1 ? 'change' : 'changes';
    offlineBannerText.textContent = unreachable
      ? `Cannot reach the server. ${retryable} ${plural} saved on this computer — keep this tab open; they will be sent automatically when the server is back.`
      : `Retrying ${retryable} unsaved ${plural}…`;
  }
});

startQueue();
// Housekeeping: drop drafts that are long past useful and have no pending
// write, so the localStorage quota stays available to the ones that matter.
pruneStaleDrafts();

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

// The signed-in user, resolved once at boot. Used for comment authorship and
// the assignment banner. Null until `initIdentityAndPermissions` resolves, so
// every read is optional-chained.
let currentUser = null;

/**
 * Load identity and this project's role, then apply the permission surface.
 *
 * Fire-and-forget from boot: the canvas is fully usable while this resolves,
 * and blocking the first paint on two requests would make every task open feel
 * slower for the common case (an owner or annotator, where nothing changes).
 * The read-only class and the banner appear a moment later; the server refuses
 * writes regardless, so nothing is unsafe in that window.
 */
async function initIdentityAndPermissions() {
  currentUser = await getCurrentUser();
  if (currentUser) {
    setMyTeams(currentUser.teams);
    setMyUserId(currentUser.id);
    const displayUser = document.getElementById("displayUsername");
    if (displayUser) displayUser.textContent = currentUser.username;
  }

  if (!projectId) return;
  const project = await loadProjectPermissions(projectId);
  if (!project) return;

  applyReadOnlyMode();
  refreshTaskPermissionUI();

  // Disable the Export link for annotator-only users. Reviewer+ and managers
  // may export; an annotator bulk-downloading the full dataset is not the
  // intent (04_UI_UX.md § 6.1). Same treatment as the AI buttons: the element
  // stays in the DOM but loses its href and is visually greyed out so the
  // reason is obvious rather than mysterious.
  if (exportLink) {
    if (canReview(currentRole())) {
      // Reviewer+: set the destination now that the role is confirmed.
      exportLink.href = `project.html?id=${encodeURIComponent(projectId)}#/exports`;
    } else {
      // Annotator: remove any href, grey it out, block keyboard activation.
      exportLink.removeAttribute('href');
      exportLink.classList.add('is-disabled');
      exportLink.setAttribute('aria-disabled', 'true');
      exportLink.title = 'Export is not available for annotators';
      exportLink.addEventListener('click', (e) => e.preventDefault(), { capture: true });
    }
  }
}

/**
 * Re-apply the banner and review controls for the task now open.
 * Called after boot and on every task switch.
 */
function refreshTaskPermissionUI() {
  const task = state.gallery && state.galleryIndex >= 0
    ? state.gallery[state.galleryIndex]
    : null;
  if (!task) return;

  updateTaskBanner(task);

  // Wire the save split-button menu (chevron + action options + task status pill).
  renderSaveSplitMenu(task, saveAndComplete, (newStatus) => {
    task.status = newStatus;
    updateTaskStatusPill(task);
  });

  // Keep the legacy reviewControls host empty.
  const reviewHost = document.getElementById("reviewControls");
  if (reviewHost) reviewHost.innerHTML = "";

  // Re-render the sidebar so its per-task write gating is recomputed. The
  // Objects panel decides once per render whether to draw the "Edit object
  // class" control, so without this a control drawn for a writable task would
  // survive onto a task assigned to someone else.
  render();
}

/**
 * Save annotations then change the task status in a single coordinated action.
 *
 * Used by "Save as Complete", "Save as In Progress", etc. The annotations save
 * goes through the normal path (which auto-reverts Completed→InProgress on a
 * plain save), so we patch the status separately afterwards rather than letting
 * syncToBackend auto-revert what the user just asked for.
 */
async function saveAndComplete(targetStatus) {
  const task = state.gallery && state.galleryIndex >= 0
    ? state.gallery[state.galleryIndex]
    : null;
  if (!task || !task.id) return;

  // Force the target status onto the task object before syncing so
  // syncToBackend sends it (it reads currentTask.status).
  const previousStatus = task.status;
  task.status = targetStatus;

  // saveDraft then sync.
  saveDraft();
  setStatus('Saving…');

  if (window.backendSyncTimeout) {
    clearTimeout(window.backendSyncTimeout);
    window.backendSyncTimeout = null;
  }

  try {
    const ok = await Promise.resolve(syncToBackend({ keepStatus: true }));
    if (ok === false) {
      // Sync failed — restore the previous status so the client isn't lying.
      task.status = previousStatus;
      refreshSaveStatus();
    } else {
      setStatus(`Saved as ${targetStatus}`);
      // Re-render the menu so options update (e.g. now Completed → show Approve/Reject).
      updateTaskStatusPill(task);
      const menuTask = task;
      renderSaveSplitMenu(menuTask, saveAndComplete, (newStatus) => {
        menuTask.status = newStatus;
        updateTaskStatusPill(menuTask);
      });
    }
  } catch (e) {
    task.status = previousStatus;
    refreshSaveStatus();
  }
}

// Points the back arrow at the project this workspace was opened from, and
// fills the breadcrumb's project half.
async function initWorkspaceContext() {
  if (!projectId) return;

  if (backToProject) {
    backToProject.href = `project.html?id=${projectId}#/tasks`;
  }
  // exportLink.href is set conditionally by initIdentityAndPermissions after
  // the role is known — annotators get no href, reviewer+ get the exports tab.

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
        // Assignment fields — needed by the permission banner before the
        // per-task detail fetch completes. The server returns all four on the
        // list endpoint (include_annotations=false path in tasks.py).
        assignee_user_id: t.assignee_user_id ?? null,
        assignee_name: t.assignee_name ?? null,
        assigned_team_id: t.assigned_team_id ?? null,
        assigned_team_name: t.assigned_team_name ?? null,
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
  //
  // It doubles as a free liveness probe. This used to swallow every failure with
  // `.catch(() => {})`, throwing away the one signal already on a timer that
  // could have told a room full of annotators the server had moved
  // (.devnotes/offline/01_OFFLINE_RESILIENCE_PLAN.md gap G4).
  setInterval(() => {
    const task = state.gallery && state.galleryIndex >= 0
      ? state.gallery[state.galleryIndex] : null;
    if (!task || !task.id) return;
    // claimTask/heartbeatTask deliberately never reject (annotation must not be
    // blocked by lock errors), so reachability is read from the resolved shape:
    // a `reachable:false` marker is set by task-lock.js on a transport failure.
    heartbeatTask(task.id, clientId())
      .then((result) => {
        if (result && result.reachable === false) noteServerUnreachable();
        else noteServerReachable();
      })
      .catch(() => noteServerUnreachable());
  }, 30_000);

  initWorkspaceContext();
  fetchLabels();
  if (projectId) {
    loadWorkspaceTasks();
  }
  initIdentityAndPermissions();

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

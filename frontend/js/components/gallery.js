/**
 * Gallery Controller
 * 
 * Manages task list loading, navigation (next/prev), annotation hydration on demand,
 * soft-lock acquisition/release on task switch, canvas resizing, and image background rendering.
 */
import { clientId } from "../utils.js?v=3";
import { apiFetch } from "../api.js?v=3";
import { state, resetWorkspaceForNewImage } from "../state.js?v=4";
import { view } from "../canvas/view.js?v=3";
import { commentOverlayRefs } from "../comment-overlay.js?v=1";
import {
  canvas, ctx, backgroundImage, staticCanvas, staticCtx, stageWrap, emptyState
} from "../dom.js?v=2";
import { drawAllLayers } from "../canvas/draw.js?v=6";
import { setStatus, render, restoreDraft, clearStatusHold } from "./workspace.js?v=12";
import { autoDetectObjects, preloadMagicWand, preloadDetectAndTag } from "../ai/detect.js?v=2";
import { syncTaskTime, resetSessionForTask, refreshTimerDisplays } from "./timer.js?v=4";
import { updateZoomDisplay } from "./zoom-control.js?v=3";
import { claimTask, releaseTask } from "../task-lock.js?v=1";
import { showNotAssignedModal } from "./not-assigned-modal.js?v=1";

/**
 * Resizes the internal and static canvases to account for device pixel ratio and oversampling.
 */
export function resizeCanvas() {
  if (!stageWrap) return;
  const rect = stageWrap.getBoundingClientRect();
  // Oversample the internal resolution so that when the client zooms in
  // the page (pinch-zoom or Ctrl++), the canvas remains sharp.
  const oversample = 3;
  const ratio = (window.devicePixelRatio || 1) * oversample;
  const w = Math.floor(rect.width * ratio);
  const h = Math.floor(rect.height * ratio);

  if (staticCanvas && staticCtx) {
    staticCanvas.width = w;
    staticCanvas.height = h;
    staticCtx.setTransform(ratio, 0, 0, ratio, 0, 0);
  }

  if (canvas && ctx) {
    canvas.width = w;
    canvas.height = h;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  }

  if (view.pendingCommentPoint) {
    view.pendingCommentPoint = null;
    if (commentOverlayRefs?.commentOverlay) {
      commentOverlayRefs.commentOverlay.classList.add("is-hidden");
    }
  }
  drawAllLayers();
}

/**
 * Loads an image from the provided URL into the workspace and updates viewport geometry.
 */
export function loadImageFromSource(src, name, { autoDetect = false } = {}) {
  const breadcrumbImage = document.querySelector("#breadcrumbImage");
  // Point the backdrop <img> at the URL *before* kicking off the canvas
  // Image(), so the two share one network fetch instead of racing into two.
  // Setting it in onload (after the canvas image resolved) used to issue a
  // second request, since /uploads sends no Cache-Control and the browser
  // revalidates.
  if (backgroundImage) backgroundImage.src = src;
  view.imageElement = new Image();
  view.imageElement.onload = async () => {
    view.imageLoaded = true;
    if (emptyState) emptyState.classList.add("is-hidden");
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
    if (state.shape === "magicWand") {
      preloadMagicWand();
    }
    preloadDetectAndTag();
  };
  view.imageElement.src = src;
}

/**
 * Updates gallery position indicator and enables/disables prev/next buttons.
 */
export function updateGalleryUI() {
  const galleryPosition = document.querySelector("#galleryPosition");
  const prevImageButton = document.querySelector("#prevImageButton");
  const nextImageButton = document.querySelector("#nextImageButton");
  const total = state.gallery ? state.gallery.length : 0;
  const current = state.galleryIndex + 1;
  if (galleryPosition) galleryPosition.textContent = total > 0 ? `${current} / ${total}` : "0 / 0";
  if (prevImageButton) prevImageButton.disabled = current <= 1;
  if (nextImageButton) nextImageButton.disabled = current >= total || total === 0;
}

/**
 * Preloads adjacent images for instant next/prev navigation.
 */
function preloadAdjacentImages(currentIndex) {
  if (!state.gallery) return;
  const indices = [currentIndex + 1, currentIndex - 1];
  for (const idx of indices) {
    if (idx >= 0 && idx < state.gallery.length && state.gallery[idx]?.url) {
      const img = new Image();
      img.src = state.gallery[idx].url;
    }
  }
}

/**
 * Switches the active task to the given index in the gallery.
 */
export async function switchImage(index) {
  if (!state.gallery || index < 0 || index >= state.gallery.length) return;
  if (state.galleryIndex >= 0 && state.gallery[state.galleryIndex]) {
    const prevTask = state.gallery[state.galleryIndex];
    if (prevTask.isFullyLoaded) {
      prevTask.annotations = [...state.annotations];
    }
    // Drop any debounced autosave still pending for the outgoing task. The
    // syncTaskTime below already writes prevTask.annotations (refreshed just
    // above), so the pending timer has nothing left to contribute — but if it
    // fired after galleryIndex moved it would call syncToBackend(), which reads
    // the *current* task, and write this task's state under the next task's id.
    // Harmless at the old flat 1s debounce, reachable at the larger debounce
    // used for shape-heavy tasks (see SAVE_DEBOUNCE_LARGE_MS in workspace.js).
    if (window.backendSyncTimeout) {
      clearTimeout(window.backendSyncTimeout);
      window.backendSyncTimeout = null;
    }
    // Drains the accumulator against the outgoing task. Bound to prevTask, so
    // it stays correct even though galleryIndex moves before it resolves.
    syncTaskTime(prevTask);
    // Release soft lock on outgoing task
    if (prevTask.isFullyLoaded) {
      releaseTask(prevTask.id, clientId());
    }
  }

  state.galleryIndex = index;
  // Session time is per-task: the new task starts a fresh session, and the
  // Total readout switches to that task's stored total.
  resetSessionForTask();
  const item = state.gallery[index];

  // A wipe-guard halt is per-task and clears when the task is (re)opened: the
  // hydration below is exactly the retry that resolves it. Without this, a
  // halted task would stay unsaveable for the life of the tab even after a
  // successful reload.
  if (item) item.saveHalted = false;
  clearStatusHold();

  resetWorkspaceForNewImage();

  // Persist active task for project and update back link
  if (item && item.id) {
    const urlParams = new URLSearchParams(window.location.search);
    const curProjId = urlParams.get('projectId');
    if (curProjId) {
      sessionStorage.setItem(`last_active_task_${curProjId}`, String(item.id));
      localStorage.setItem(`last_active_task_${curProjId}`, String(item.id));
      const backToProject = document.querySelector("#backToProject");
      if (backToProject) {
        backToProject.href = `project.html?id=${encodeURIComponent(curProjId)}&activeTaskId=${encodeURIComponent(item.id)}#/tasks`;
      }
      // Carry the open task to the Exports page so it can offer a "Current
      // task" filter. Re-pointed on every task open, because the link is built
      // once at init and the open task changes as the gallery is navigated.
      const exportLink = document.querySelector("#exportLink");
      if (exportLink) {
        exportLink.href =
          `project.html?id=${encodeURIComponent(curProjId)}&currentTaskId=${encodeURIComponent(item.id)}#/exports`;
      }
    }
  }

  // Start image network fetch and decoding immediately
  loadImageFromSource(item.url, item.name);
  updateGalleryUI();
  preloadAdjacentImages(index);

  // Hydrate annotations on demand and claim soft task lock in parallel
  if (item.id) {
    const detailPromise = apiFetch(`/api/tasks/${item.id}`)
      .then(async (res) => {
        if (res && res.ok) {
          const detail = await res.json();
          item.annotations = Array.isArray(detail.annotations) ? detail.annotations : [];
          if (detail.updated_at) item.updated_at = detail.updated_at;
          if (detail.time_spent != null) item.time_spent = detail.time_spent;
          if (detail.assignee !== undefined) item.assignee = detail.assignee;
          if (detail.assignees !== undefined) item.assignees = detail.assignees;
          if (detail.status !== undefined) item.status = detail.status;

          // Mirrors _is_task_editor in api/routers/tasks.py: the assignee, the
          // project owner and an appointed reviewer all have full authority
          // over a task. The owner was previously treated as read-only on
          // anyone else's task, so their edits were dropped client-side even
          // though the server allowed them; a reviewer would hit exactly the
          // same bug, since reviewing is by definition work on someone else's
          // task.
          const currentUsername = localStorage.getItem("dataset_username") || "";
          // Every assignee, not only the primary: a task can be held by several
          // people and _is_task_editor admits all of them, so keying this on
          // the single mirrored name would open the canvas read-only for a
          // second assignee editing the task they were actually given.
          const taskAssignees = Array.isArray(item.assignees) && item.assignees.length
            ? item.assignees
            : (item.assignee ? [item.assignee] : []);
          state.isTaskAssignee = taskAssignees.includes(currentUsername);
          const readOnly = taskAssignees.length > 0 &&
            !state.isTaskAssignee && !state.isProjectOwner && !state.isProjectReviewer;
          if (readOnly) {
            setStatus("⚠ Task is assigned to another user (Read-only)");
            // The owner and a reviewer never reach here (excluded above), so
            // only an annotator whose edits would be dropped sees the dialog.
            showNotAssignedModal(taskAssignees);
          }
          item.isFullyLoaded = !readOnly;
        } else if (res && res.status === 403) {
          setStatus("⚠ Task is assigned to another user (Read-only)");
          showNotAssignedModal();
          item.annotations = [];
          item.isFullyLoaded = false;
        } else {
          item.annotations = [];
          item.isFullyLoaded = false;
        }
      })
      .catch((e) => {
        console.error("Failed to hydrate task annotations:", e);
        item.annotations = [];
        // A thrown fetch (network drop, timeout, aborted request) leaves the
        // canvas empty exactly like the non-OK branches above, so it must mark
        // the task not-loaded for the same reason: `isFullyLoaded` is the only
        // thing standing between an unhydrated task and an autosave that sends
        // `annotations: []` over real server data (see syncToBackend in
        // components/workspace.js and the isFullyLoaded gate in
        // components/timer.js). Leaving it true here let a task that was
        // *previously* opened successfully keep its stale true and save the
        // empty canvas — the wipe path in 04_ANNOTATION_SAVE_LOSS.md.
        item.isFullyLoaded = false;
      });

    const lockPromise = detailPromise.then(() => {
      if (!item.isFullyLoaded) return;
      return claimTask(item.id, clientId())
        .then((lock) => {
          if (lock && lock.status === 'locked') {
            const secsLeft = lock.seconds_remaining || 60;
            setStatus(`⚠ Task in use by another annotator (~${secsLeft}s remaining)`);
          }
        })
        .catch((e) => {
          console.warn('[task-lock] claim on open failed:', e);
        });
    });

    await Promise.all([detailPromise, lockPromise]);
  }

  state.annotations = [...item.annotations];
  // Recover local draft if server lacks latest unsaved changes
  if (restoreDraft(item)) {
    setStatus("Recovered unsaved changes");
  }
  render();
}

/**
 * Initializes previous/next image button event listeners.
 */
export function initGalleryNavigation() {
  const prevImageButton = document.querySelector("#prevImageButton");
  const nextImageButton = document.querySelector("#nextImageButton");
  if (prevImageButton) {
    prevImageButton.addEventListener("click", () => {
      switchImage(state.galleryIndex - 1);
    });
  }
  if (nextImageButton) {
    nextImageButton.addEventListener("click", () => {
      switchImage(state.galleryIndex + 1);
    });
  }
  window.addEventListener("resize", resizeCanvas);
}

/**
 * Loads project tasks without annotation blobs and opens the initial or target task.
 */
export async function loadWorkspaceTasks(projectId, targetTaskId = null) {
  if (!projectId) return;
  try {
    const res = await apiFetch(`/api/tasks/sequence/${projectId}`);
    if (res && res.ok) {
      const tasks = await res.json();
      state.gallery = tasks.map(t => ({
        id: t.id,
        name: t.description,
        url: "/" + t.image_path.replace(/\\/g, "/"),
        annotations: [],
        width: 0,
        height: 0,
        status: "New",
        assignee: null,
        time_spent: 0,
        updated_at: null,
        isFullyLoaded: false
      }));

      if (state.gallery.length > 0) {
        if (ctx && canvas) ctx.clearRect(0, 0, canvas.width, canvas.height);
        let initialIndex = 0;
        if (targetTaskId) {
          const foundIndex = state.gallery.findIndex(t => String(t.id) === String(targetTaskId));
          if (foundIndex !== -1) initialIndex = foundIndex;
        }
        await switchImage(initialIndex);
      } else {
        if (ctx && canvas) ctx.clearRect(0, 0, canvas.width, canvas.height);
        updateGalleryUI();
        refreshTimerDisplays();
      }
    }
  } catch (e) {
    console.error("Failed to load workspace tasks:", e);
  }
}

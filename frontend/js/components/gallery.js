/**
 * Gallery Controller
 * 
 * Manages task list loading, navigation (next/prev), annotation hydration on demand,
 * soft-lock acquisition/release on task switch, canvas resizing, and image background rendering.
 */
import { clientId } from "../utils.js?v=1";
import { apiFetch } from "../api.js?v=1";
import { state, resetWorkspaceForNewImage } from "../state.js?v=1";
import { view } from "../canvas/view.js?v=1";
import { commentOverlayRefs } from "../comment-overlay.js?v=1";
import {
  canvas, ctx, backgroundImage, staticCanvas, staticCtx, stageWrap, emptyState
} from "../dom.js?v=1";
import { drawAllLayers } from "../canvas/draw.js?v=1";
import { setStatus, render, restoreDraft } from "./workspace.js?v=2";
import { autoDetectObjects, preloadMagicWand, preloadDetectAndTag } from "../ai/detect.js?v=1";
import { syncTaskTime, resetSessionForTask, refreshTimerDisplays } from "./timer.js?v=2";
import { updateZoomDisplay } from "./zoom-control.js?v=1";
import { claimTask, releaseTask } from "../task-lock.js?v=1";

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
    if (backgroundImage) backgroundImage.src = src;
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
          if (detail.status !== undefined) item.status = detail.status;

          const currentUsername = localStorage.getItem("dataset_username") || "";
          if (item.assignee && item.assignee !== currentUsername) {
            setStatus("⚠ Task is assigned to another user (Read-only)");
            item.isFullyLoaded = false;
          } else {
            item.isFullyLoaded = true;
          }
        } else if (res && res.status === 403) {
          setStatus("⚠ Task is assigned to another user (Read-only)");
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

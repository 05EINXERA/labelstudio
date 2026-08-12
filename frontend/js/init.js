/**
 * Main workspace entry point and lifecycle orchestrator.
 */
import { clientId } from "./utils.js?v=1";
import { apiFetch } from "./api.js?v=1";
import { state } from "./state.js?v=1";
import {
  setStatus, syncToBackend, loadSaved, saveDraft, render
} from "./components/workspace.js?v=1";
import {
  syncTimeToServer, setActiveTaskResolver, setConflictHandler
} from "./components/timer.js?v=1";
import { setZoomChangeHandler } from "./canvas/interactions.js?v=1";
import { initContextMenu } from "./canvas/context-menu.js?v=1";
import { initSidebarResize } from "./components/sidebar-resize.js?v=1";
import { initZoomControl, updateZoomDisplay } from "./components/zoom-control.js?v=1";
import { releaseTask, heartbeatTask } from "./task-lock.js?v=1";
import { initFftControls } from "./fft-controls.js?v=1";
import {
  switchImage, initGalleryNavigation, loadWorkspaceTasks, resizeCanvas
} from "./components/gallery.js?v=1";
import { initModals } from "./components/modals.js?v=1";
import { initModeControls } from "./components/mode-controls.js?v=1";
import { initOpacityControl } from "./components/opacity-control.js?v=1";

if (!localStorage.getItem('logged_in')) {
  window.location.replace('/');
}

const urlParams = new URLSearchParams(window.location.search);
const projectId = urlParams.get('projectId');
const targetTaskId = urlParams.get('taskId');

const logoutBtnApp = document.querySelector("#logoutBtnApp");
if (logoutBtnApp) {
  logoutBtnApp.addEventListener("click", async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch (e) { }
    localStorage.removeItem("dataset_username");
    localStorage.removeItem("image-annotation-mvp-v1");
    localStorage.removeItem("logged_in");
    localStorage.removeItem("access_token");
    window.location.replace("/");
  });
}

/**
 * Flush both pending saves and task session time delta before page unload / hide.
 */
function flushPendingSaves({ useBeacon = false } = {}) {
  if (window.backendSyncTimeout) {
    clearTimeout(window.backendSyncTimeout);
    window.backendSyncTimeout = null;
  }
  syncToBackend({ useBeacon });
  syncTimeToServer({ useBeacon });
}

/**
 * Releases soft task lock on the currently open task.
 */
function _releaseCurrentLock({ useBeacon = false } = {}) {
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
});

window.addEventListener('pagehide', () => {
  flushPendingSaves({ useBeacon: true });
  _releaseCurrentLock({ useBeacon: true });
});

/**
 * Concurrency conflict resolution handler.
 */
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
    task.updated_at = null;
    setStatus("Keeping your version — will overwrite on next save");
  }
});

/**
 * Fetches label classes for the active project.
 */
async function fetchLabels() {
  if (!projectId) {
    state.labels = [];
    render();
    return;
  }
  try {
    const res = await apiFetch(`/api/labels?projectId=${projectId}`);
    if (res && res.ok) {
      state.labels = await res.json();
      render();
    }
  } catch (err) {
    console.error("Failed to fetch labels from backend:", err);
  }
}

/**
 * Initializes workspace navigation breadcrumbs and links.
 */
async function initWorkspaceContext() {
  if (!projectId) return;

  const backToProject = document.querySelector("#backToProject");
  const exportLink = document.querySelector("#exportLink");
  const breadcrumbProject = document.querySelector("#breadcrumbProject");

  if (backToProject) {
    const lastActive = targetTaskId || sessionStorage.getItem(`last_active_task_${projectId}`) || localStorage.getItem(`last_active_task_${projectId}`);
    const activeQuery = lastActive ? `&activeTaskId=${encodeURIComponent(lastActive)}` : "";
    backToProject.href = `project.html?id=${encodeURIComponent(projectId)}${activeQuery}#/tasks`;
  }
  if (exportLink) {
    exportLink.href = `project.html?id=${encodeURIComponent(projectId)}#/exports`;
  }

  try {
    const res = await apiFetch(`/api/projects/${encodeURIComponent(projectId)}`);
    if (!res || !res.ok) return;
    const project = await res.json();
    if (project && breadcrumbProject) {
      breadcrumbProject.textContent = project.name || "Untitled project";
      breadcrumbProject.title = project.name || "Untitled project";
      
      const username = localStorage.getItem("dataset_username") || "";
      if (project.creator && project.creator === username) {
        document.querySelectorAll(".owner-only-status").forEach(el => {
          el.style.display = "block";
        });
      }
    }
  } catch (e) {
    console.error("Failed to resolve project name for breadcrumb", e);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  initSidebarResize();
  initContextMenu();
  setZoomChangeHandler(updateZoomDisplay);
  initZoomControl();
  initModeControls();
  initGalleryNavigation();
  initModals({
    onTaskCompleteContinue: () => {
      if (state.gallery && state.galleryIndex < state.gallery.length - 1) {
        switchImage(state.galleryIndex + 1);
      }
    }
  });

  // Task timer active task resolver
  setActiveTaskResolver(() => {
    if (typeof state === 'undefined' || !state) return null;
    if (state.galleryIndex < 0 || !state.gallery) return null;
    return state.gallery[state.galleryIndex] || null;
  });

  // Soft lock heartbeat (every 30s)
  setInterval(() => {
    const task = state.gallery && state.galleryIndex >= 0
      ? state.gallery[state.galleryIndex] : null;
    if (task && task.id) {
      heartbeatTask(task.id, clientId()).catch(() => { });
    }
  }, 30_000);

  initFftControls();
  initOpacityControl();
  loadSaved();
  resizeCanvas();
  render();

  // Parallel high-speed workspace data bootstrap
  Promise.all([
    initWorkspaceContext(),
    fetchLabels(),
    projectId ? loadWorkspaceTasks(projectId, targetTaskId) : Promise.resolve(),
  ]).catch((err) => {
    console.error("Failed to bootstrap workspace data in parallel:", err);
  });
});

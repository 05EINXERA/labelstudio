/**
 * Main workspace entry point and lifecycle orchestrator.
 */
import { clientId } from "./utils.js?v=1";
import { apiFetch } from "./api.js?v=3";
import { state } from "./state.js?v=2";
import {
  setStatus, syncToBackend, loadSaved, saveDraft, render, loadTeamForWorkspace
} from "./components/workspace.js?v=6";
import {
  syncTimeToServer, setActiveTaskResolver, setConflictHandler, handleVisibilityChange as handleTimerVisibility
} from "./components/timer.js?v=2";
import { setZoomChangeHandler } from "./canvas/interactions.js?v=4";
import { initContextMenu } from "./canvas/context-menu.js?v=2";
import { initSidebarResize } from "./components/sidebar-resize.js?v=1";
import { initZoomControl, updateZoomDisplay } from "./components/zoom-control.js?v=1";
import { releaseTask, heartbeatTask } from "./task-lock.js?v=1";
import { initFftControls } from "./fft-controls.js?v=1";
import { toolAvailability } from "./feature-flags.js?v=1";
import {
  switchImage, initGalleryNavigation, loadWorkspaceTasks, resizeCanvas
} from "./components/gallery.js?v=2";
import { initModals } from "./components/modals.js?v=1";
import { initModeControls } from "./components/mode-controls.js?v=1";
import { initOpacityControl } from "./components/opacity-control.js?v=1";
import { initConnectionMonitor, onConnectionChange } from "./connection.js?v=3";

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
  if (task && task.id && task.isFullyLoaded) {
    releaseTask(task.id, clientId(), { useBeacon });
  }
}

window.addEventListener('visibilitychange', () => {
  handleTimerVisibility();
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
let conflictTask = null;
const conflictModal = document.getElementById('conflictModal');

if (conflictModal) {
  document.getElementById('conflictReloadBtn')?.addEventListener('click', () => {
    window.location.reload();
  });

  const dismissConflict = () => {
    conflictModal.classList.remove('is-active');
    if (conflictTask) {
      conflictTask.updated_at = null;
      setStatus("Keeping your version — will overwrite on next save");
    }
  };

  document.getElementById('conflictKeepBtn')?.addEventListener('click', dismissConflict);
  document.getElementById('conflictCloseBtn')?.addEventListener('click', dismissConflict);
}

setConflictHandler((task) => {
  saveDraft();
  conflictTask = task;
  if (conflictModal) {
    conflictModal.classList.add('is-active');
  }
});

/**
 * Asks the server whether AI features are switched on for this deployment and
 * mirrors the answer into toolAvailability.ai, which renderControls() already
 * reads to enable/disable the whole AI toolbar. Without this the controls stay
 * live against a server that refuses every AI job with a 503.
 *
 * Left enabled on failure: an unreachable endpoint is not evidence that AI is
 * off, and a disabled-by-mistake toolbar is worse than a click that surfaces
 * the server's own error.
 */
async function fetchAiAvailability() {
  try {
    const res = await apiFetch("/api/detect/availability");
    if (res && res.ok) {
      const data = await res.json();
      toolAvailability.ai = data.enabled !== false;
      render();
    }
  } catch (err) {
    console.error("Could not resolve AI availability; leaving controls enabled:", err);
  }
}

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
    if (!project) return;

    // Owner-or-not decides whether the workspace treats a task assigned to
    // someone else as read-only (see gallery.js). It comes from the server's
    // is_owner, which resolves through project.owner_id — the account — rather
    // than comparing the annotator's display name, which differs from the
    // account name as soon as anyone picks a profile on the shared login.
    state.isProjectOwner = Boolean(project.is_owner);

    if (breadcrumbProject) {
      breadcrumbProject.textContent = project.name || "Untitled project";
      breadcrumbProject.title = project.name || "Untitled project";
    }
    if (project.is_owner) {
      document.querySelectorAll(".owner-only-status").forEach(el => {
        el.style.display = "block";
      });
    }
  } catch (e) {
    console.error("Failed to resolve project name for breadcrumb", e);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  // Started first: every autosave, heartbeat and lock call below swallows its
  // own network errors, so the banner is the only thing that will tell the
  // annotator their work is no longer reaching the server.
  initConnectionMonitor();

  // On reconnect, push whatever the server missed. The per-task draft has kept
  // it safe meanwhile; this is what actually gets it off the annotator's
  // machine, without waiting for their next edit to trigger a save.
  onConnectionChange((up) => {
    if (!up) return;
    setStatus("Connection restored — saving…");
    Promise.resolve(syncToBackend())
      .then((ok) => setStatus(ok === false ? "⚠ Still not saved — retrying" : "Saved"))
      .catch(() => setStatus("⚠ Still not saved — retrying"));
  });

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
    if (task && task.id && task.isFullyLoaded) {
      heartbeatTask(task.id, clientId()).catch(() => { });
    }
  }, 30_000);

  initFftControls();
  initOpacityControl();
  loadSaved();
  resizeCanvas();
  render();

  // Parallel high-speed workspace data bootstrap.
  //
  // Ownership is the one ordering constraint: opening a task decides whether it
  // is read-only, and that decision reads state.isProjectOwner (gallery.js). If
  // the tasks won the race the owner would be treated as a non-owner on any task
  // assigned to someone else — read-only, isFullyLoaded false, and every save
  // dropped before it reached the network. So the context resolves first, and
  // the rest still loads alongside it.
  const contextReady = initWorkspaceContext();
  Promise.all([
    contextReady,
    fetchLabels(),
    fetchAiAvailability(),
    projectId ? loadTeamForWorkspace(projectId) : Promise.resolve(),
    projectId
      ? contextReady
          .catch(() => {})
          .then(() => loadWorkspaceTasks(projectId, targetTaskId))
      : Promise.resolve(),
  ]).catch((err) => {
    console.error("Failed to bootstrap workspace data in parallel:", err);
  });
});

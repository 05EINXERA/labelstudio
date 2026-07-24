import { generateUUID, clamp, round, normalizeClassName, formatTime } from "./utils.js?v=1";
import { apiFetch, pollJob } from "./api.js?v=1";
import {
  state, storageKey, snapshot, resetWorkspaceForNewImage
} from "./state.js?v=1";
import { view } from "./canvas/view.js?v=1";
import { commentOverlayRefs } from "./comment-overlay.js?v=1";
import {
  canvas, ctx, imageCanvas, imageCtx, staticCanvas, staticCtx, stageWrap,
  emptyState, drawMode, selectMode, boxMode, polygonMode, commentMode, magicWandMode,
  autoDetectButton, undoButton, deleteButton, clearButton, exportLink
} from "./dom.js?v=1";
import { drawAllLayers } from "./canvas/draw.js?v=1";
import {
  setStatus, syncToBackend, save, loadSaved,
  render
} from "./components/workspace.js?v=1";
import { autoDetectObjects, autoTagObjects } from "./ai/detect.js?v=1";
import {
  syncTaskTime, syncTimeToServer, drainTaskTime, setActiveTaskResolver,
  resetSessionForTask, refreshTimerDisplays
} from "./components/timer.js?v=1";
import {
  finalizePolygon, deleteSelected, undoAction, setZoomChangeHandler
} from "./canvas/interactions.js?v=1";
import { initSidebarResize } from "./components/sidebar-resize.js?v=1";
import { initZoomControl, updateZoomDisplay } from "./components/zoom-control.js?v=1";

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

window.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') {
    // The page may never come back, so this flush must survive unload too.
    flushPendingSaves({ useBeacon: true });
  }
});

window.addEventListener('pagehide', () => {
  flushPendingSaves({ useBeacon: true });
});

function resizeCanvas() {
  const rect = stageWrap.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  const w = Math.floor(rect.width * ratio);
  const h = Math.floor(rect.height * ratio);

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
    } else {
      save();
    }
  };
  view.imageElement.src = src;
}


function switchImage(index) {
  if (index < 0 || index >= state.gallery.length) return;
  if (state.galleryIndex >= 0 && state.gallery[state.galleryIndex]) {
    const prevTask = state.gallery[state.galleryIndex];
    prevTask.annotations = [...state.annotations];
    // Drains the accumulator against the outgoing task. Bound to prevTask, so
    // it stays correct even though galleryIndex moves before it resolves.
    syncTaskTime(prevTask);
  }
  state.galleryIndex = index;
  // Session time is per-task: the new task starts a fresh session, and the
  // Total readout switches to that task's stored total.
  resetSessionForTask();
  const item = state.gallery[index];

  snapshot();
  resetWorkspaceForNewImage();
  state.annotations = [...item.annotations];
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
  prevImageButton.addEventListener("click", () => switchImage(state.galleryIndex - 1));
}
if (nextImageButton) {
  nextImageButton.addEventListener("click", () => switchImage(state.galleryIndex + 1));
}

drawMode.addEventListener("click", () => {
  if (!state.activeLabelId) {
    setStatus("Pick a class first, then draw");
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
  setStatus("All annotations cleared");
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

window.addEventListener("storage", (e) => {
  if (e.key === storageKey) {
    loadSaved();
    render();
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
    setStatus("Detection Model Size Changed");
  });
}

if (aiSamModel) {
  aiSamModel.value = localStorage.getItem("ai_sam_model") || "mobile_sam.pt";
  aiSamModel.addEventListener('change', e => {
    localStorage.setItem("ai_sam_model", e.target.value);
    setStatus("Magic Wand Model Changed");
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

    setStatus("AI Settings Applied");
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
      setStatus("Username updated");
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

// Team Validation Modal Logic
const teamValidationForm = document.getElementById("teamValidationForm");
if (teamValidationForm) {
  teamValidationForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const nameInput = document.getElementById("teamValidationName").value.trim();
    const errorDiv = document.getElementById("teamValidationError");

    let team = [];
    try {
      const res = await apiFetch('/api/team');
      if (res.ok) {
        const data = await res.json();
        team = data.map(t => t.name);
      }
    } catch (err) {
      console.error(err);
    }

    if (team.includes(nameInput)) {
      errorDiv.style.display = "none";
      localStorage.setItem('dataset_username', nameInput);
      currentUserForTimer = nameInput;

      const displayUser = document.getElementById("displayUsername");
      if (displayUser) displayUser.textContent = nameInput;

      document.getElementById("teamValidationModal").classList.remove("is-active");
      const userPanel = document.getElementById("userPanel");
      if (userPanel) userPanel.style.display = "block";
    } else {
      errorDiv.style.display = "block";
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
    const res = await apiFetch(`/api/tasks?projectId=${projectId}`);
    if (res.ok) {
      const tasks = await res.json();
      state.gallery = tasks.map(t => ({
        id: t.id,
        name: t.description,
        url: "/" + t.image_path.replace(/\\/g, "/"),
        annotations: t.annotations || [],
        width: 0,
        height: 0,
        status: t.status,
        assignee: t.assignee,
        // Persisted per-task total; the workspace "Total" readout is scoped to
        // the open task, so it needs this as its base.
        time_spent: t.time_spent || 0
      }));

      if (state.gallery.length > 0) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        let initialIndex = 0;
        const targetTaskId = urlParams.get('taskId');
        if (targetTaskId) {
          const foundIndex = state.gallery.findIndex(t => t.id == targetTaskId);
          if (foundIndex !== -1) initialIndex = foundIndex;
        }
        switchImage(initialIndex);
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
  setZoomChangeHandler(updateZoomDisplay);
  initZoomControl();
  // Resolves the open task, or null. Task time is only billed while a task is
  // actually open (F8), and Stop uses this to flush the right task (F6).
  setActiveTaskResolver(() => {
    if (typeof state === 'undefined' || !state) return null;
    if (state.galleryIndex < 0 || !state.gallery) return null;
    return state.gallery[state.galleryIndex] || null;
  });
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
    switchImage(state.galleryIndex + 1);
  }
}

if (tcClose) tcClose.addEventListener('click', closeTaskCompletedModal);
if (tcOk) tcOk.addEventListener('click', closeTaskCompletedModal);

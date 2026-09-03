import { generateUUID, normalizeClassName } from "../utils.js?v=1";
import { apiFetch } from "../api.js?v=3";
import { isOnline } from "../connection.js?v=3";
import {
  state, storageKey, draftKey, colorForName, labelByName, labelById,
  labelDisplayName, snapshot, selectedAnnotation
} from "../state.js?v=2";
import { annotationPoints, updateAnnotationBounds } from "../canvas/geometry.js?v=4";
import { view } from "../canvas/view.js?v=1";
import { drainTaskTime } from "./timer.js?v=2";
import { detectState } from "../ai/detect-state.js?v=1";
import { draw, drawAllLayers } from "../canvas/draw.js?v=4";
import {
  emptyState, classesList, annotationList, annotationCount, selectedInfo,
  drawMode, selectMode, boxMode, polygonMode, commentMode, magicWandMode,
  autoDetectButton, aiSettingsMenuButton, autoTagButton, fftToolGroup,
  undoButton, redoButton, deleteButton, clearButton, exportLink,
  shapeHint, saveStatus
} from "../dom.js?v=1";
import { commentOverlayRefs } from "../comment-overlay.js?v=1";
import { toolAvailability } from "../feature-flags.js?v=1";


export function setStatus(text) {
  saveStatus.textContent = text;
  window.clearTimeout(setStatus.timer);
  setStatus.timer = window.setTimeout(() => {
    // Never idle back to "Saved" while the server is unreachable — that is the
    // one claim we know to be false, and it is exactly the reassurance an
    // annotator would act on before closing the tab.
    saveStatus.textContent = isOnline() ? "Saved" : "Not saved — offline";
  }, 3000);
}

export function ensureLabel(className, customColor = null) {
  const name = normalizeClassName(className);
  const existing = labelByName(name);
  if (existing) return existing;

  const label = {
    id: generateUUID(),
    name,
    color: customColor || colorForName(name)
  };
  state.labels.push(label);

  const projectId = new URLSearchParams(window.location.search).get('projectId');
  if (projectId) {
    // Persist to backend asynchronously
    apiFetch('/api/labels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...label, projectId: Number(projectId) })
    }).catch(err => console.error("Failed to save label to backend:", err));
  }

  return label;
}

export function repairLabelsFromAnnotations() {
  state.annotations = state.annotations.map((annotation) => {
    if (annotation.type === "comment") return annotation;
    const existing = state.labels.find((label) => label.id === annotation.labelId);
    if (existing) return annotation;

    const label = ensureLabel(annotation.detectedClass || "Unlabeled");
    return { ...annotation, labelId: label.id };
  });
}

export function syncToBackend({ useBeacon = false, targetStatus = null } = {}) {
  if (typeof state === 'undefined' || state.galleryIndex < 0 || !state.gallery || !state.gallery[state.galleryIndex]) return;
  const currentTask = state.gallery[state.galleryIndex];
  if (!currentTask.id) return;
  if (!currentTask.isFullyLoaded) {
    setStatus("Cannot save: Task not fully loaded");
    return Promise.resolve(false);
  }

  // Only pass a status when this save actually intends to change it.
  // Echoing currentTask.status back on every plain annotation autosave made
  // the server unable to tell "no status change" from "change to the same
  // value it already has" — which matters once terminal statuses are
  // status-locked (see api/routers/tasks.py): the echo could get misread as
  // an attempted change and, depending on how that guard is enforced, block
  // or discard the very annotation edit this save exists to persist.
  // `drainTaskTime` still owns the New → In Progress auto-promotion when no
  // explicit status is given (see its own `status` param handling).
  const previousStatus = currentTask.status;
  let taskStatus = targetStatus;
  if (taskStatus === 'New') taskStatus = 'In Progress';
  if (taskStatus) currentTask.status = taskStatus;
  currentTask.annotations = [...state.annotations];

  // Time accounting (drain, retry-on-failure, task binding) lives in timer.js
  // so there is exactly one drain point for taskSessionSeconds. See
  // docs/TIMER_AUDIT.md F3/F4.
  return Promise.resolve(drainTaskTime(currentTask, {
    status: taskStatus,
    annotations: currentTask.annotations,
    useBeacon
  })).then((ok) => {
    // The draft exists to cover work the server does not have. Once it has
    // taken the write, the draft is stale and must go, or the next load would
    // "recover" it over fresher server data.
    if (ok !== false && currentTask.id) {
      clearDraft(currentTask.id);
    } else if (ok === false && taskStatus) {
      // The status write above was optimistic; the server rejected this save
      // (e.g. a non-owner attempting to change a locked task's status), so
      // undo it rather than leave the UI showing a status that was never
      // actually persisted. Only relevant when this save carried an explicit
      // status change — a plain annotation autosave never touched
      // currentTask.status in the first place (see above).
      currentTask.status = previousStatus;
    }
    return ok;
  });
}

/**
 * Substitute an explicit offline reason for a generic save-failure message.
 * "Not saved—retrying" reads like a transient hiccup; when we know the server
 * is unreachable the annotator needs to know retrying will not help until the
 * connection is back.
 */
function offlineOr(message) {
  return isOnline() ? message : "⚠ Not saved — connection lost";
}

/** The task currently open, or null. */
function currentTask() {
  if (!state.gallery || state.galleryIndex < 0) return null;
  return state.gallery[state.galleryIndex] || null;
}

/**
 * Write a local draft for the open task.
 *
 * The draft is the safety net for everything the server has not acknowledged
 * yet. It is deliberately per-task (see state.draftKey) and is cleared only
 * once a save succeeds, so a refresh mid-edit — or after a failed save —
 * recovers the work instead of losing it.
 */
export function saveDraft() {
  const task = currentTask();
  if (!task || !task.id) return;

  // An empty canvas is not work worth protecting, and writing it anyway meant
  // every task the annotator merely opened left a draft behind for good. Those
  // accumulate until localStorage hits its quota (at which point the real
  // drafts stop being written), and each one prompts a pointless "restore your
  // draft?" on the next open. Drop any stale draft instead of storing nothing.
  if (!state.annotations || state.annotations.length === 0) {
    clearDraft(task.id);
    return;
  }

  try {
    localStorage.setItem(draftKey(task.id), JSON.stringify({
      annotations: state.annotations,
      labels: state.labels,
      savedAt: Date.now()
    }));
  } catch (e) {
    // Quota exceeded: the draft is best-effort, the server save is the real
    // path. Losing the net is worth knowing about but must not break editing.
    console.warn('Could not write local draft', e);
  }
}

export function clearDraft(taskId) {
  try {
    localStorage.removeItem(draftKey(taskId));
  } catch (e) {
    console.warn('Could not clear local draft', e);
  }
}

/**
 * Restore a draft for `task` if it holds work the server does not have.
 *
 * Returns true if anything was restored. Called after the task's server-side
 * annotations are already in state, so the draft only wins when it actually
 * differs — otherwise every reload would report a phantom recovery.
 */
export function restoreDraft(task) {
  if (!task || !task.id) return false;

  // Only meaningful once the server's own annotations are in state. When the
  // detail fetch failed (timeout, 5xx, dropped LAN link) the caller leaves
  // annotations empty and isFullyLoaded false — indistinguishable here from a
  // task the server says is genuinely empty. Restoring against that would
  // compare the draft to a phantom empty server copy: the "server has
  // annotations" guard below could not fire, so the draft would be adopted
  // silently, and a later save could push it over real server data. Keep the
  // draft untouched and let the next successful open recover it.
  if (!task.isFullyLoaded) return false;

  let raw;
  try {
    raw = localStorage.getItem(draftKey(task.id));
  } catch {
    return false;
  }
  if (!raw) return false;

  try {
    const draft = JSON.parse(raw);
    if (!Array.isArray(draft.annotations)) {
      clearDraft(task.id);
      return false;
    }
    // Same content as the server's copy: nothing to recover.
    if (JSON.stringify(draft.annotations) === JSON.stringify(state.annotations)) {
      clearDraft(task.id);
      return false;
    }
    // An empty draft never carries work. It is the residue of a task that was
    // opened and closed without drawing, and prompting about it asks the
    // annotator to arbitrate between two empty sets.
    if (draft.annotations.length === 0) {
      clearDraft(task.id);
      return false;
    }
    // Nothing on the server to overwrite: adopting the draft can only add the
    // annotator's unsaved work back, so there is no decision to put to them.
    if (!state.annotations || state.annotations.length === 0) {
      state.annotations = draft.annotations;
      if (Array.isArray(draft.labels) && draft.labels.length) {
        state.labels = draft.labels;
      }
      repairLabelsFromAnnotations();
      return true;
    }
    // Both sides hold real, differing work — the only case where the annotator
    // has something to lose either way, and so the only case worth a prompt.
    if (!confirm(
      `This task has ${state.annotations.length} annotation(s) saved on the server, ` +
      `and ${draft.annotations.length} in an unsaved local draft.\n\n` +
      `OK — use your local draft (${draft.annotations.length})\n` +
      `Cancel — keep the server's version (${state.annotations.length}), discarding the draft`
    )) {
      clearDraft(task.id);
      return false;
    }

    state.annotations = draft.annotations;
    if (Array.isArray(draft.labels) && draft.labels.length) {
      state.labels = draft.labels;
    }
    repairLabelsFromAnnotations();
    return true;
  } catch {
    clearDraft(task.id);
    return false;
  }
}

export function save() {
  saveDraft();
  setStatus("Saving…");

  if (window.backendSyncTimeout) {
    clearTimeout(window.backendSyncTimeout);
  }
  window.backendSyncTimeout = setTimeout(() => {
    window.backendSyncTimeout = null;
    // "Saved" is only claimed once the server has actually taken the write.
    // Reporting it on the localStorage write alone told annotators their work
    // was safe while it existed nowhere but their own browser.
    const task = currentTask();
    Promise.resolve(syncToBackend())
      .then((ok) => {
        if (ok === false && task?.lastSaveError) {
          setStatus(`⚠ ${task.lastSaveError}`);
          task.lastSaveError = null;
        } else if (ok === false) {
          setStatus(offlineOr("Not saved—retrying"));
        } else {
          setStatus("Saved");
        }
      })
      .catch(() => setStatus(offlineOr("Not saved—retrying")));
  }, 1000);
}

/**
 * Manual save with UI feedback.
 * 
 * This is called when the user clicks the Save button. It shows a spinner overlay
 * while saving, then displays "Saved Successfully" for 3 seconds.
 */
export async function manualSaveWithUI(targetStatus = null) {
  const overlay = document.getElementById('saveOverlay');
  if (!overlay) return;

  // Show the overlay
  overlay.classList.add('is-active');

  try {
    // Save the draft locally
    saveDraft();

    // Sync to backend
    const ok = await syncToBackend({ targetStatus });

    // Update status message. A rejected save may have left a specific reason
    // on the task (e.g. a non-owner status change on a locked task) — show
    // that instead of the generic retry message when present.
    const currentTask = state.gallery && state.galleryIndex >= 0 ? state.gallery[state.galleryIndex] : null;
    let message = ok === false ? offlineOr("Not saved—retrying") : "Saved Successfully";
    if (ok === false && currentTask?.lastSaveError) {
      message = `⚠ ${currentTask.lastSaveError}`;
      currentTask.lastSaveError = null;
    }
    setStatus(message);
    
    // Keep the overlay visible for a brief moment, then fade it out
    await new Promise(resolve => setTimeout(resolve, 800));
    
    overlay.classList.remove('is-active');
    
    // Update the visual status label
    if (targetStatus && state.gallery && state.galleryIndex >= 0) {
      const currentStatusLabel = document.getElementById('currentTaskStatus');
      if (currentStatusLabel) currentStatusLabel.textContent = state.gallery[state.galleryIndex].status;
    }

    // Keep the outcome message for 3 seconds, then settle on the resting label.
    // A save that failed must not settle on "Saved".
    await new Promise(resolve => setTimeout(resolve, 3000));
    setStatus(ok === false ? offlineOr("Not saved—retrying") : "Saved");
  } catch (err) {
    console.error('Manual save failed:', err);
    setStatus(offlineOr("Not saved—retrying"));
    overlay.classList.remove('is-active');
  }
}

/**
 * Drop per-task drafts that hold no annotations.
 *
 * Until saveDraft started skipping them, every task that was merely opened
 * left an empty draft behind permanently. On a long-lived browser those
 * accumulate until localStorage hits its quota — at which point writes of
 * *real* drafts start failing — and each one triggers a spurious "restore
 * your draft?" prompt on open. Runs once at startup; cheap, and bounded by
 * how many tasks this browser has touched.
 */
function pruneEmptyDrafts() {
  const prefix = draftKey("");
  let removed = 0;
  try {
    for (const key of Object.keys(localStorage)) {
      if (!key.startsWith(prefix)) continue;
      try {
        const draft = JSON.parse(localStorage.getItem(key));
        if (!draft || !Array.isArray(draft.annotations) || draft.annotations.length === 0) {
          localStorage.removeItem(key);
          removed += 1;
        }
      } catch {
        // Unparseable draft can never be restored; it only occupies quota.
        localStorage.removeItem(key);
        removed += 1;
      }
    }
  } catch (e) {
    // Storage unavailable (private mode, blocked site data). Nothing to prune.
    console.warn('Could not prune local drafts', e);
  }
  if (removed) console.info(`Pruned ${removed} empty local draft(s)`);
}

export function loadSaved() {
  pruneEmptyDrafts();

  // Legacy global-slot draft. Kept only to migrate anything a previous version
  // left behind; drafts are per-task now (see restoreDraft).
  const saved = localStorage.getItem(storageKey);
  if (!saved) return;

  try {
    const payload = JSON.parse(saved);
    if (Array.isArray(payload.labels)) {
      state.labels = payload.labels;
    }
    repairLabelsFromAnnotations();
  } catch {
    // fall through to the removal below
  }
  // Drop the global slot either way: it is shared across tasks and across
  // tabs, so keeping it would let one task's annotations leak into another's.
  localStorage.removeItem(storageKey);
}

// Eye / eye-off pair, sized to match the existing 20x20 row action buttons.
const EYE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>';
const EYE_OFF_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>';

// The keystroke that activates the class at this position, or "" past the
// reachable range so unreachable rows do not advertise a key that does nothing.
function hotkeyChipHTML(index) {
  if (index >= HOTKEY_LABEL_LIMIT) return "";
  const digit = (index + 1) % 10;
  const shifted = index >= 10;
  const combo = shifted ? `Shift+${digit}` : String(digit);
  return `<kbd class="class-hotkey" title="Press ${combo} to select this class">${combo}</kbd>`;
}

function visibilityButtonHTML(isHidden, title) {
  return `<span class="eye-btn${isHidden ? " is-hidden-state" : ""}" title="${title}">${isHidden ? EYE_OFF_SVG : EYE_SVG}</span>`;
}

// Digit hotkeys address the first 20 classes: 1-9,0 for the first ten and
// Shift+1-9,0 for the next ten. Beyond that the sidebar click is the only way in.
export const HOTKEY_LABEL_LIMIT = 20;

// The one place a class becomes active, shared by the sidebar click and the
// digit hotkeys so the two can never drift apart.
export function activateLabel(label) {
  if (!label) return;

  state.activeLabelId = label.id;
  state.needsLabelSelection = false;
  // The pending shape has been dealt with by this, so the next canvas click is
  // an ordinary one — leaving this set would deselect spuriously.
  state.justFinalized = false;

  // Switching class part-way through a polygon retags the shape being drawn
  // rather than abandoning it, so the annotator can correct a wrong class
  // without losing the vertices already placed.
  const drawingPolygon = view.drag?.type === "draw-polygon";
  if (drawingPolygon) {
    const inProgress = state.annotations.find((item) => item.id === view.drag.annotationId);
    if (inProgress) inProgress.labelId = label.id;
  }

  // Picking a class with nothing selected means "start the next annotation", so
  // drop back into draw mode instead of making the user press Draw. With a
  // selection it means "relabel that", which must not change the mode. Neither
  // applies mid-polygon: that drag owns the mode until it finalizes.
  if (state.selectedIds.size === 0 && !drawingPolygon) {
    state.mode = "draw";
  }

  // Reassign class to selected annotations
  if (state.selectedIds.size > 0) {
    snapshot();
    let changed = false;
    state.annotations.forEach(a => {
      if (state.selectedIds.has(a.id) && a.type !== "comment" && a.labelId !== label.id) {
        a.labelId = label.id;
        changed = true;
      }
    });
    if (changed) {
      save();
    } else {
      state.history.pop();
    }
  }

  render();
}

export function renderClasses() {
  classesList.innerHTML = "";

  if (!state.labels.length) {
    const empty = document.createElement("p");
    empty.className = "chip-count";
    empty.textContent = "No classes defined";
    classesList.appendChild(empty);
  }

  // Note: we deliberately do NOT auto-activate the first class here.
  // The annotator must explicitly pick a class to start drawing, so that
  // the first canvas click is never silently attributed to an unintended class.

  state.labels.forEach((label, index) => {
    const item = document.createElement("button");
    item.type = "button";
    const labelHidden = state.hiddenLabelIds.has(label.id);
    item.className = `class-item${label.id === state.activeLabelId ? " is-active" : ""}${labelHidden ? " is-row-hidden" : ""}`;
    item.style.display = "flex";
    item.style.alignItems = "center";
    item.style.justifyContent = "space-between";
    const classAnns = state.annotations.filter(a => a.labelId === label.id && a.type !== "comment");
    const uniqueGroups = new Set();
    let count = 0;
    classAnns.forEach(a => {
      if (a.groupId) {
        if (!uniqueGroups.has(a.groupId)) {
          uniqueGroups.add(a.groupId);
          count++;
        }
      } else {
        count++;
      }
    });

    // Classes are fixed per project and managed from the dashboard, so the list
    // is read-only here: no edit or delete affordances.
    item.innerHTML = `
      <div style="display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0;">
        <span class="swatch" style="background:${label.color || '#65727f'}; flex-shrink: 0;"></span>
        ${hotkeyChipHTML(index)}
        <strong class="class-name" style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"></strong>
        <span class="class-count" style="font-size: 0.75rem; color: var(--muted); margin-left: 4px; flex-shrink: 0;">(${count})</span>
      </div>
      <div class="class-actions" style="display: flex; align-items: center; flex-shrink: 0;">
        ${visibilityButtonHTML(labelHidden, labelHidden ? "Show class annotations" : "Hide class annotations")}
      </div>
    `;
    item.querySelector(".class-name").textContent = labelDisplayName(label);

    item.querySelector(".eye-btn").addEventListener("click", (e) => {
      // Without this the row's own handler would also fire and make a class
      // active merely because its visibility was toggled.
      e.stopPropagation();
      if (labelHidden) state.hiddenLabelIds.delete(label.id);
      else state.hiddenLabelIds.add(label.id);
      // View-only: not undoable, nothing persisted changed.
      render();
    });

    // Click on the item itself sets it as active
    item.addEventListener("click", () => {
      activateLabel(label);
    });

    classesList.appendChild(item);
  });
}

const ITEM_HEIGHT = 32;
const VIRTUAL_BUFFER = 10;
let virtualizationState = {
  initialized: false,
  startIndex: 0,
  endIndex: 20,
  scrollTop: 0
};

export function renderAnnotations() {
  annotationList.innerHTML = "";

  if (!state.annotations.length) {
    const empty = document.createElement("p");
    empty.className = "chip-count";
    empty.textContent = "No annotations yet";
    annotationList.appendChild(empty);
    annotationCount.textContent = "0";
    
    const selected = state.annotations.find((item) => item.id === state.selectedId);
    if (selected) {
      if (selected.type === "comment") {
        const author = selected.author || (selected.extra && selected.extra.author) || "User";
        selectedInfo.innerHTML = `Comment by ${author} <button id="editCommentBtn" class="icon-button" style="font-size: 0.8rem; margin-left: 8px;">✏️ Edit</button>`;
        document.getElementById('editCommentBtn').addEventListener('click', () => {
          view.pendingCommentEditId = selected.id;
          const screenX = view.imageBox.x + selected.x * view.imageBox.scale;
          const screenY = view.imageBox.y + selected.y * view.imageBox.scale;
          commentOverlayRefs.commentOverlay.style.left = `${screenX + 15}px`;
          commentOverlayRefs.commentOverlay.style.top = `${screenY - 15}px`;
          commentOverlayRefs.commentOverlayInput.value = selected.text || "";
          commentOverlayRefs.commentOverlay.classList.remove("is-hidden");
          commentOverlayRefs.commentOverlayInput.focus();
        });
      } else {
        selectedInfo.textContent = labelDisplayName(labelById(selected.labelId));
      }
    } else {
      selectedInfo.textContent = "None";
    }
    return;
  }

  const displayItems = [];
  const processedGroups = new Set();
  const groupMap = new Map();
  for (const a of state.annotations) {
    if (a.groupId) {
      if (!groupMap.has(a.groupId)) groupMap.set(a.groupId, []);
      groupMap.get(a.groupId).push(a);
    }
  }

  state.annotations.forEach((annotation) => {
    if (annotation.groupId) {
      if (processedGroups.has(annotation.groupId)) return;
      processedGroups.add(annotation.groupId);
    }
    const isGroup = !!annotation.groupId;
    const groupAnns = isGroup ? groupMap.get(annotation.groupId) : [annotation];
    displayItems.push({ annotation, isGroup, groupAnns });
  });

  annotationCount.textContent = String(displayItems.length);

  const scrollContainer = annotationList.closest('.pane-body');
  if (scrollContainer && !virtualizationState.initialized) {
    virtualizationState.initialized = true;
    scrollContainer.addEventListener('scroll', () => {
      const st = scrollContainer.scrollTop;
      const clientHeight = scrollContainer.clientHeight;
      const start = Math.max(0, Math.floor(st / ITEM_HEIGHT) - VIRTUAL_BUFFER);
      const end = Math.min(displayItems.length, start + Math.ceil(clientHeight / ITEM_HEIGHT) + 2 * VIRTUAL_BUFFER);
      
      if (start !== virtualizationState.startIndex || end !== virtualizationState.endIndex) {
        virtualizationState.scrollTop = st;
        virtualizationState.startIndex = start;
        virtualizationState.endIndex = end;
        renderAnnotations();
      }
    });
  }

  if (scrollContainer) {
    const clientHeight = scrollContainer.clientHeight || 800;
    virtualizationState.startIndex = Math.max(0, Math.floor(virtualizationState.scrollTop / ITEM_HEIGHT) - VIRTUAL_BUFFER);
    virtualizationState.endIndex = Math.min(displayItems.length, virtualizationState.startIndex + Math.ceil(clientHeight / ITEM_HEIGHT) + 2 * VIRTUAL_BUFFER);
  } else {
    virtualizationState.startIndex = 0;
    virtualizationState.endIndex = displayItems.length;
  }

  if (virtualizationState.startIndex > 0) {
    const topSpacer = document.createElement("div");
    // We subtract 4px to account for the flex gap that will be added between this spacer and the first item
    topSpacer.style.height = `${(virtualizationState.startIndex * ITEM_HEIGHT) - 4}px`;
    topSpacer.style.flexShrink = "0";
    annotationList.appendChild(topSpacer);
  }

  const visibleItems = displayItems.slice(virtualizationState.startIndex, virtualizationState.endIndex);

  visibleItems.forEach((itemObj, visibleIndex) => {
    const { annotation, isGroup, groupAnns } = itemObj;
    const displayCount = virtualizationState.startIndex + visibleIndex + 1;

    const label = annotation.type === "comment" ? { name: "Comment", color: "#e85d75" } : labelById(annotation.labelId);
    const totalPoints = groupAnns.reduce((sum, a) => sum + annotationPoints(a).length, 0);

    const item = document.createElement("button");
    item.type = "button";
    const isActive = state.selectedIds.has(annotation.id);
    const annHidden = state.hiddenAnnotationIds.has(annotation.id);
    const classHidden = !!annotation.labelId && state.hiddenLabelIds.has(annotation.labelId);
    item.className = `annotation-item${isActive ? " is-active" : ""}${annHidden || classHidden ? " is-row-hidden" : ""}`;
    item.style.display = "flex";
    item.style.alignItems = "center";
    item.style.justifyContent = "space-between";
    item.innerHTML = `
      <div style="display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0;">
        <span class="swatch" style="background:${label.color || '#65727f'}; flex-shrink: 0;"></span>
        <strong class="ann-name" style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"></strong>
        <span class="ann-pts"></span>
      </div>
      <div class="annotation-actions" style="display: flex; align-items: center; gap: 4px; flex-shrink: 0;">
        <span class="edit-ann-btn" title="Edit object class" style="cursor: pointer; color: var(--muted); display: grid; place-items: center; width: 20px; height: 20px;">
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
        </span>
        ${visibilityButtonHTML(annHidden, annHidden ? "Show object" : "Hide object")}
      </div>
    `;

    let text = annotation.type === "comment" ? `💬 ${annotation.text || "Comment"}` : `${displayCount}. ${labelDisplayName(label)}`;
    if (isGroup) {
      text = `${displayCount}. ${labelDisplayName(label)} (Group of ${groupAnns.length})`;
    }
    item.querySelector(".ann-name").textContent = text;
    const ptsEl = item.querySelector(".ann-pts");
    ptsEl.textContent = annotation.type === "comment" ? "" : String(totalPoints);
    if (annotation.type !== "comment") ptsEl.title = `${totalPoints} points`;

    const escapeHTML = (str) => String(str).replace(/[&<>'"]/g, match => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[match]));

    item.querySelector(".edit-ann-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      const currentName = label.name;
      const options = state.labels.map(l => `<option value="${escapeHTML(l.name)}"></option>`).join("");
      item.innerHTML = `
        <form class="edit-ann-form" style="display: flex; gap: 4px; width: 100%; align-items: center;" onsubmit="event.preventDefault();">
          <input type="text" list="classNamesDatalist_${annotation.id}" class="edit-ann-input" value="${escapeHTML(currentName)}" style="flex: 1; min-width: 0; padding: 2px 4px; font-size: 0.85rem;" onclick="event.stopPropagation()">
          <datalist id="classNamesDatalist_${annotation.id}">
            ${options}
          </datalist>
          <input type="color" class="edit-ann-color" value="${label.color}" style="width: 24px; height: 24px; padding: 0; border: none; flex-shrink: 0;" onclick="event.stopPropagation()">
          <button type="submit" class="primary save-edit-btn" style="padding: 2px 6px; font-size: 0.75rem; border: none; border-radius: 4px; flex-shrink: 0;" onclick="event.stopPropagation()">Save</button>
          <button type="button" class="cancel-edit-btn" style="padding: 2px 6px; font-size: 0.75rem; background: var(--panel-2); border: 1px solid var(--line); border-radius: 4px; flex-shrink: 0;" onclick="event.stopPropagation()">Cancel</button>
        </form>
      `;
      const form = item.querySelector(".edit-ann-form");
      const input = item.querySelector(".edit-ann-input");
      const colorInput = item.querySelector(".edit-ann-color");

      const finishEdit = (saveChanges) => {
        if (saveChanges) {
          const newName = input.value.trim();
          const newColor = colorInput.value;
          if (newName) {
            const newLabel = ensureLabel(newName, newColor);
            if (newLabel.id !== annotation.labelId || newLabel.color !== newColor) {
              snapshot();
              if (newLabel.color !== newColor) {
                newLabel.color = newColor;
              }
              if (newLabel.id !== annotation.labelId) {
                if (isGroup) {
                  groupAnns.forEach(a => a.labelId = newLabel.id);
                } else {
                  annotation.labelId = newLabel.id;
                }
              }
              save();
            }
          }
        }
        render(); // re-render
      };

      form.addEventListener("submit", (ev) => {
        ev.preventDefault();
        finishEdit(true);
      });
      item.querySelector(".cancel-edit-btn").addEventListener("click", (ev) => {
        ev.stopPropagation();
        finishEdit(false);
      });
      form.addEventListener("click", (ev) => ev.stopPropagation());
    });

    item.querySelector(".eye-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      const ids = isGroup ? groupAnns.map(a => a.id) : [annotation.id];
      const nowHidden = !state.hiddenAnnotationIds.has(annotation.id);
      ids.forEach((id) => {
        if (nowHidden) state.hiddenAnnotationIds.add(id);
        else state.hiddenAnnotationIds.delete(id);
      });
      render();
    });

    item.addEventListener("click", (event) => {
      state.mode = "select";
      if (event.shiftKey) {
        const toSelect = isGroup ? groupAnns.map(a => a.id) : [annotation.id];
        if (state.selectedIds.has(annotation.id)) {
          toSelect.forEach(id => state.selectedIds.delete(id));
        } else {
          toSelect.forEach(id => state.selectedIds.add(id));
        }
        state._selectedId = state.selectedIds.size > 0 ? Array.from(state.selectedIds)[0] : null;
      } else {
        state.selectedIds.clear();
        if (isGroup) {
          groupAnns.forEach(a => state.selectedIds.add(a.id));
        } else {
          state.selectedIds.add(annotation.id);
        }
        state.selectedId = annotation.id;
      }
      render();
      draw();
    });
    annotationList.appendChild(item);
  });

  const remaining = displayItems.length - virtualizationState.endIndex;
  if (remaining > 0) {
    const bottomSpacer = document.createElement("div");
    bottomSpacer.style.height = `${(remaining * ITEM_HEIGHT) - (virtualizationState.startIndex === 0 ? 0 : 4)}px`;
    bottomSpacer.style.flexShrink = "0";
    annotationList.appendChild(bottomSpacer);
  }

  const selected = state.annotations.find((item) => item.id === state.selectedId);
  if (selected) {
    if (selected.type === "comment") {
      const author = selected.author || (selected.extra && selected.extra.author) || "User";
      selectedInfo.innerHTML = `Comment by ${author} <button id="editCommentBtn" class="icon-button" style="font-size: 0.8rem; margin-left: 8px;">✏️ Edit</button>`;
      document.getElementById('editCommentBtn').addEventListener('click', () => {
        view.pendingCommentEditId = selected.id;
        const screenX = view.imageBox.x + selected.x * view.imageBox.scale;
        const screenY = view.imageBox.y + selected.y * view.imageBox.scale;
        commentOverlayRefs.commentOverlay.style.left = `${screenX + 15}px`;
        commentOverlayRefs.commentOverlay.style.top = `${screenY - 15}px`;
        commentOverlayRefs.commentOverlayInput.value = selected.text || "";
        commentOverlayRefs.commentOverlay.classList.remove("is-hidden");
        commentOverlayRefs.commentOverlayInput.focus();
      });
    } else {
      selectedInfo.textContent = labelDisplayName(labelById(selected.labelId));
    }
  } else {
    selectedInfo.textContent = "None";
  }

  if (scrollContainer && virtualizationState.initialized) {
    scrollContainer.scrollTop = virtualizationState.scrollTop;
  }
}

export function renderControls() {
  drawMode.classList.toggle("is-active", state.mode === "draw");
  selectMode.classList.toggle("is-active", state.mode === "select");
  boxMode.classList.toggle("is-active", state.shape === "box");
  polygonMode.classList.toggle("is-active", state.shape === "polygon");
  commentMode.classList.toggle("is-active", state.shape === "comment");
  magicWandMode.classList.toggle("is-active", state.shape === "magicWand");
  if (state.shape === "polygon") {
    if (state.mode === "select") {
      shapeHint.textContent = state.activeLabelId
        ? "Class selected — press Draw or click a class to start drawing."
        : "Pick a class from the list to start drawing a polygon.";
    } else if (state.needsLabelSelection) {
      shapeHint.textContent = "Polygon drawn — pick a class to assign it.";
    } else if (!state.activeLabelId) {
      shapeHint.textContent = "Pick a class from the list to start drawing a polygon.";
    } else {
      shapeHint.textContent = "Click to add points. Click the first point to close the polygon.";
    }
  } else if (state.shape === "comment") {
    shapeHint.textContent = "Click anywhere on the image to leave a comment.";
  } else {
    // box / magicWand
    if (state.mode === "select") {
      shapeHint.textContent = state.activeLabelId
        ? "Class selected — press Draw or click a class to start drawing."
        : "Pick a class from the list to start drawing a bounding box.";
    } else if (!state.activeLabelId) {
      shapeHint.textContent = "Pick a class from the list to start drawing a bounding box.";
    } else {
      shapeHint.textContent = "Click and drag to draw a bounding box.";
    }
  }
  // ── AI section ────────────────────────────────────────────────────────────
  // When toolAvailability.ai is false every AI control is permanently disabled
  // regardless of any other runtime state.
  const aiBlocked = !toolAvailability.ai;
  autoDetectButton.disabled = aiBlocked || detectState.detectionBusy || !view.imageLoaded;
  const labelSpan = autoDetectButton.querySelector(".btn-label");
  if (labelSpan) {
    labelSpan.textContent = detectState.detectionBusy ? "Detecting..." : "Detect";
  }
  autoDetectButton.title = selectedAnnotation() ? "Detect objects inside the selected area" : "Detect objects in the whole image";
  if (aiSettingsMenuButton) aiSettingsMenuButton.disabled = aiBlocked;
  if (autoTagButton)        autoTagButton.disabled        = aiBlocked;
  // Magic Wand is in the Tools group but is AI-dependent — block it too.
  magicWandMode.disabled = aiBlocked;

  // ── Smooth section ────────────────────────────────────────────────────────
  // Disable every interactive control inside the FFT tool-group when
  // toolAvailability.smooth is false.  The container itself is not hidden so
  // the toolbar layout stays stable; the controls are just non-interactive.
  if (fftToolGroup) {
    fftToolGroup.querySelectorAll("button, input").forEach(el => {
      el.disabled = !toolAvailability.smooth;
    });
  }

  // ── Edit section ──────────────────────────────────────────────────────────
  undoButton.disabled = state.history.length === 0;
  redoButton.disabled = state.redoHistory.length === 0;
  deleteButton.disabled = state.selectedIds.size === 0;
  const groupButton = document.querySelector("#groupButton");
  if (groupButton) {
    const selectedList = state.annotations.filter(a => state.selectedIds.has(a.id));
    const allSameGroup = selectedList.length > 1 && selectedList.every(a => a.groupId && a.groupId === selectedList[0].groupId);
    groupButton.disabled = state.selectedIds.size <= 1 || allSameGroup;
  }
  const ungroupButton = document.querySelector("#ungroupButton");
  if (ungroupButton) {
    ungroupButton.disabled = !state.annotations.some(a => state.selectedIds.has(a.id) && a.groupId);
  }
  clearButton.disabled = state.annotations.length === 0;
  const noData = !view.imageLoaded && state.annotations.length === 0;
  // An <a> has no .disabled — dim it and drop it out of the tab order instead.
  if (exportLink) {
    exportLink.classList.toggle("is-disabled", noData);
    exportLink.tabIndex = noData ? -1 : 0;
  }
  emptyState.classList.toggle("is-hidden", view.imageLoaded);
}

let pendingRender = false;

function doRenderSync() {
  pendingRender = false;
  renderClasses();
  renderImageClasses();
  renderAnnotations();
  renderControls();
  renderAssignee();
  drawAllLayers();
}

export function render() {
  if (!pendingRender) {
    pendingRender = true;
    requestAnimationFrame(doRenderSync);
  }
}

export function renderAssignee() {
  const selectEl = document.getElementById("workspaceAssignee");
  if (!selectEl) return;
  const task = currentTask();
  const currentAssignee = task ? task.assignee : "";

  // Ensure the task's current assignee is an option
  if (currentAssignee) {
    let exists = Array.from(selectEl.options).some(opt => opt.value === currentAssignee);
    if (!exists) {
      const opt = document.createElement("option");
      opt.value = currentAssignee;
      opt.textContent = currentAssignee;
      selectEl.appendChild(opt);
    }
  }

  // Ensure the current local user is an option
  const localUser = localStorage.getItem("dataset_username");
  if (localUser) {
    let exists = Array.from(selectEl.options).some(opt => opt.value === localUser);
    if (!exists) {
      const opt = document.createElement("option");
      opt.value = localUser;
      opt.textContent = localUser + " (Me)";
      selectEl.appendChild(opt);
    }
  }

  if (task && selectEl.value !== (currentAssignee || "")) {
    selectEl.value = currentAssignee || "";
  }
}

export async function loadTeamForWorkspace(projectId) {
  const selectEl = document.getElementById("workspaceAssignee");
  if (!selectEl) return;

  try {
    const [teamRes, projectRes] = await Promise.all([
      apiFetch("/api/team"),
      apiFetch(`/api/projects/${encodeURIComponent(projectId)}`)
    ]);
    
    if (!teamRes || !teamRes.ok || !projectRes || !projectRes.ok) return;
    
    const team = await teamRes.json();
    const project = await projectRes.json();
    
    const username = localStorage.getItem("dataset_username") || "";
    if (project.creator && project.creator !== username) {
      selectEl.disabled = true;
      selectEl.title = "Only the project owner can change the assignee";
    }
    
    const byTeam = {};
    const unassigned = [];
    const projectTeamId = project.team_id;

    team.forEach((m) => {
      if (projectTeamId != null) {
        const belongsToProjectTeam = m.teams && m.teams.some(t => t.id === projectTeamId);
        if (!belongsToProjectTeam) return;
      }
      if (m.teams && m.teams.length > 0) {
        const primaryTeamName = m.teams[0].name;
        if (!byTeam[primaryTeamName]) byTeam[primaryTeamName] = [];
        byTeam[primaryTeamName].push(m);
      } else {
        unassigned.push(m);
      }
    });

    selectEl.innerHTML = '<option value="">Unassigned</option>';
    for (const [teamName, members] of Object.entries(byTeam)) {
      const group = document.createElement("optgroup");
      group.label = teamName;
      members.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.name;
        opt.textContent = m.name;
        group.appendChild(opt);
      });
      selectEl.appendChild(group);
    }
    if (unassigned.length > 0) {
      const group = document.createElement("optgroup");
      group.label = "Unassigned";
      unassigned.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.name;
        opt.textContent = m.name;
        group.appendChild(opt);
      });
      selectEl.appendChild(group);
    }

    // Bind change event
    selectEl.addEventListener("change", async (e) => {
      const task = currentTask();
      if (!task || !task.id) return;
      
      const newAssignee = e.target.value;
      task.assignee = newAssignee;
      
      try {
        setStatus("Saving…");
        const res = await apiFetch(`/api/tasks/${task.id}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ assignee: newAssignee })
        });
        if (res && res.ok) {
          setStatus("Saved");
        } else {
          setStatus("Not saved—retrying");
        }
      } catch (err) {
        setStatus("Not saved—retrying");
      }
    });

    renderAssignee(); // Update initial state
  } catch (err) {
    console.error("Failed to load team for workspace", err);
  }
}

export function renderImageClasses() {
  const imageClassesList = document.getElementById("imageClassesList");
  if (!imageClassesList) return;

  const presentLabels = new Set();
  (state.annotations || []).forEach(ann => {
    if (ann.type !== "comment" && ann.labelId) {
      presentLabels.add(ann.labelId);
    }
  });

  imageClassesList.innerHTML = '';

  if (presentLabels.size === 0) {
    imageClassesList.innerHTML = '<p class="hint">No classes in current image.</p>';
    return;
  }

  Array.from(presentLabels).forEach(labelId => {
    const classDef = labelById(labelId);
    if (!classDef) return;

    const div = document.createElement("div");
    div.className = "class-item";
    div.style.gridTemplateColumns = "auto 1fr auto";

    const colorIndicator = document.createElement("div");
    colorIndicator.style.width = "12px";
    colorIndicator.style.height = "12px";
    colorIndicator.style.borderRadius = "50%";
    colorIndicator.style.background = classDef.color;

    const nameSpan = document.createElement("div");
    nameSpan.className = "chip-name";
    nameSpan.textContent = classDef.name;

    const countSpan = document.createElement("span");
    countSpan.style.fontSize = "0.75rem";
    countSpan.style.color = "var(--muted)";

    const classAnns = (state.annotations || []).filter(a => a.labelId === labelId && a.type !== "comment");
    const uniqueGroups = new Set();
    let count = 0;
    classAnns.forEach(a => {
      if (a.groupId) {
        if (!uniqueGroups.has(a.groupId)) {
          uniqueGroups.add(a.groupId);
          count++;
        }
      } else {
        count++;
      }
    });

    countSpan.textContent = `(${count})`;

    div.appendChild(colorIndicator);
    div.appendChild(nameSpan);
    div.appendChild(countSpan);
    imageClassesList.appendChild(div);
  });
}

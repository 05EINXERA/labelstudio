import { generateUUID, normalizeClassName } from "../utils.js?v=1";
import { apiFetch } from "../api.js?v=1";
import {
  state, storageKey, draftKey, colorForName, labelByName, labelById,
  labelDisplayName, snapshot, selectedAnnotation
} from "../state.js?v=1";
import { annotationPoints, updateAnnotationBounds } from "../canvas/geometry.js?v=1";
import { view } from "../canvas/view.js?v=1";
import { drainTaskTime } from "./timer.js?v=1";
import { detectState } from "../ai/detect-state.js?v=1";
import { draw, drawAllLayers } from "../canvas/draw.js?v=1";
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
    saveStatus.textContent = "Saved";
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
    const existing = state.labels.find((label) => label.id === annotation.labelId);
    if (existing) return annotation;

    const label = ensureLabel(annotation.detectedClass || "object");
    return { ...annotation, labelId: label.id };
  });
}

export function syncToBackend({ useBeacon = false } = {}) {
  if (typeof state === 'undefined' || state.galleryIndex < 0 || !state.gallery || !state.gallery[state.galleryIndex]) return;
  const currentTask = state.gallery[state.galleryIndex];
  if (!currentTask.id) return;

  let taskStatus = currentTask.status;
  if (taskStatus === 'New') taskStatus = 'In Progress';
  currentTask.status = taskStatus;
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
    if (ok !== false && currentTask.id) clearDraft(currentTask.id);
    return ok;
  });
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
    Promise.resolve(syncToBackend())
      .then((ok) => setStatus(ok === false ? "Not saved—retrying" : "Saved"))
      .catch(() => setStatus("Not saved—retrying"));
  }, 1000);
}

/**
 * Manual save with UI feedback.
 * 
 * This is called when the user clicks the Save button. It shows a spinner overlay
 * while saving, then displays "Saved Successfully" for 3 seconds.
 */
export async function manualSaveWithUI() {
  const overlay = document.getElementById('saveOverlay');
  if (!overlay) return;

  // Show the overlay
  overlay.classList.add('is-active');

  try {
    // Save the draft locally
    saveDraft();
    
    // Sync to backend
    const ok = await syncToBackend();
    
    // Update status message
    const message = ok === false ? "Not saved—retrying" : "Saved Successfully";
    setStatus(message);
    
    // Keep the overlay visible for a brief moment, then fade it out
    await new Promise(resolve => setTimeout(resolve, 800));
    
    overlay.classList.remove('is-active');
    
    // Keep "Saved Successfully" message for 3 seconds, then revert to "Saved"
    await new Promise(resolve => setTimeout(resolve, 3000));
    setStatus("Saved");
  } catch (err) {
    console.error('Manual save failed:', err);
    setStatus("Not saved—retrying");
    overlay.classList.remove('is-active');
  }
}

export function loadSaved() {
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

function visibilityButtonHTML(isHidden, title) {
  return `<span class="eye-btn${isHidden ? " is-hidden-state" : ""}" title="${title}">${isHidden ? EYE_OFF_SVG : EYE_SVG}</span>`;
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
        <strong class="class-name" style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"></strong>
        <span class="class-count" style="font-size: 0.75rem; color: var(--muted); margin-left: 4px; flex-shrink: 0;">(${count})</span>
      </div>
      <div class="class-actions" style="display: flex; align-items: center; flex-shrink: 0;">
        ${visibilityButtonHTML(labelHidden, labelHidden ? "Show class annotations" : "Hide class annotations")}
      </div>
    `;
    item.querySelector(".class-name").textContent = `${index + 1}. ${labelDisplayName(label)}`;

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
    item.addEventListener("click", (e) => {
      state.activeLabelId = label.id;
      state.needsLabelSelection = false;
      // The pending shape has been dealt with by this click, so the next canvas
      // click is an ordinary one — leaving this set would deselect spuriously.
      state.justFinalized = false;

      // Picking a class with nothing selected means "start the next annotation",
      // so drop back into draw mode instead of making the user press Draw. With a
      // selection the click means "relabel that", which must not change the mode.
      if (state.selectedIds.size === 0) {
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
    });

    classesList.appendChild(item);
  });
}

export function renderAnnotations() {
  annotationList.innerHTML = "";

  if (!state.annotations.length) {
    const empty = document.createElement("p");
    empty.className = "chip-count";
    empty.textContent = "No annotations yet";
    annotationList.appendChild(empty);
  }

  const processedGroups = new Set();
  let displayCount = 0;

  state.annotations.forEach((annotation, index) => {
    if (annotation.groupId) {
      if (processedGroups.has(annotation.groupId)) return;
      processedGroups.add(annotation.groupId);
    }

    displayCount++;
    const isGroup = !!annotation.groupId;
    const groupAnns = isGroup ? state.annotations.filter(a => a.groupId === annotation.groupId) : [annotation];

    const label = annotation.type === "comment" ? { name: "Comment", color: "#e85d75" } : labelById(annotation.labelId);
    const totalPoints = groupAnns.reduce((sum, a) => sum + annotationPoints(a).length, 0);

    const item = document.createElement("button");
    item.type = "button";
    const isActive = state.selectedIds.has(annotation.id);
    // Own toggle only — a class-level hide is reflected separately so the row
    // still shows this annotation's individual state underneath it.
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
    // Bare count, no " pts" suffix — the unit costs sidebar width and the
    // number alone is what matters (e.g. spotting a malformed polygon).
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
      // A grouped row stands for every member, so it toggles them together —
      // matching how the row is drawn and selected as a unit.
      const ids = isGroup ? groupAnns.map(a => a.id) : [annotation.id];
      const nowHidden = !state.hiddenAnnotationIds.has(annotation.id);
      ids.forEach((id) => {
        if (nowHidden) state.hiddenAnnotationIds.add(id);
        else state.hiddenAnnotationIds.delete(id);
      });
      // Visibility is a view concern: no snapshot() (not undoable) and no
      // save() (nothing persisted changed).
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

  annotationCount.textContent = String(displayCount);

  const selected = state.annotations.find((item) => item.id === state.selectedId);
  if (selected) {
    if (selected.type === "comment") {
      selectedInfo.innerHTML = `Comment by ${selected.author || "User"} <button id="editCommentBtn" class="icon-button" style="font-size: 0.8rem; margin-left: 8px;">✏️ Edit</button>`;
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

export function render() {
  renderClasses();
  renderImageClasses();
  renderAnnotations();
  renderControls();
  drawAllLayers();
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

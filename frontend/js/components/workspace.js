import { generateUUID, normalizeClassName } from "../utils.js?v=1";
import { apiFetch } from "../api.js?v=1";
import {
  state, storageKey, colorForName, labelByName, labelById,
  labelDisplayName, snapshot, selectedAnnotation
} from "../state.js?v=1";
import { annotationPoints, updateAnnotationBounds } from "../canvas/geometry.js?v=1";
import { view } from "../canvas/view.js?v=1";
import { timerState } from "../timer-state.js?v=1";
import { detectState } from "../ai/detect-state.js?v=1";
import { draw, drawAllLayers } from "../canvas/draw.js?v=1";
import {
  emptyState, classesList, annotationList, annotationCount, selectedInfo,
  drawMode, selectMode, boxMode, polygonMode, commentMode, magicWandMode,
  autoDetectButton, undoButton, deleteButton, clearButton, exportMenuButton,
  labelStudioButton, shapeHint, saveStatus
} from "../dom.js?v=1";
import { commentOverlayRefs } from "../comment-overlay.js?v=1";

let labelStudioBusy = false;

export function setStatus(text) {
  saveStatus.textContent = text;
  window.clearTimeout(setStatus.timer);
  setStatus.timer = window.setTimeout(() => {
    saveStatus.textContent = "Saved";
  }, 1200);
}

export function setLabelStudioBusy(isBusy) {
  labelStudioBusy = isBusy;
  labelStudioButton.disabled = isBusy || !view.imageLoaded || state.annotations.length === 0;
  labelStudioButton.textContent = isBusy ? "Sending..." : "Send annotations";
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

  // Persist to backend asynchronously
  apiFetch('/api/labels', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(label)
  }).catch(err => console.error("Failed to save label to backend:", err));

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

export function syncToBackend() {
  if (typeof state === 'undefined' || state.galleryIndex < 0 || !state.gallery || !state.gallery[state.galleryIndex]) return;
  const currentTask = state.gallery[state.galleryIndex];
  if (!currentTask.id) return;

  const timeDelta = timerState.taskSessionSeconds;
  timerState.taskSessionSeconds = 0;
  const username = localStorage.getItem('dataset_username') || 'Unknown';
  let taskStatus = currentTask.status;
  if (taskStatus === 'New') taskStatus = 'In Progress';
  currentTask.status = taskStatus;
  currentTask.annotations = [...state.annotations];

  apiFetch('/api/tasks', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      id: currentTask.id,
      status: taskStatus,
      time_spent_delta: timeDelta,
      assignee: username,
      annotations: JSON.stringify(currentTask.annotations),
      updated_at: currentTask.updated_at
    })
  })
    .then(async res => {
      if (res.status === 409) {
        const errorMsg = await res.json();
        alert(`Conflict: ${errorMsg.detail}`);
        currentTask.id = null; // Prevent further autosaves for this task
        return;
      }
      if (res.ok) {
        const data = await res.json();
        if (data && data.updated_at) {
          currentTask.updated_at = data.updated_at;
        }
      }
    })
    .catch(e => console.error("Auto-save failed", e));
}

export function save() {
  const payload = {
    labels: state.labels,
    annotations: state.annotations,
    image: state.image
  };
  localStorage.setItem(storageKey, JSON.stringify(payload));
  setStatus("Saved");

  if (window.backendSyncTimeout) {
    clearTimeout(window.backendSyncTimeout);
  }
  window.backendSyncTimeout = setTimeout(() => {
    window.backendSyncTimeout = null;
    syncToBackend();
  }, 1000);
}

export function loadSaved() {
  const saved = localStorage.getItem(storageKey);
  if (!saved) return;

  try {
    const payload = JSON.parse(saved);
    if (Array.isArray(payload.labels)) {
      state.labels = payload.labels;
    }
    if (Array.isArray(payload.annotations)) {
      state.annotations = payload.annotations;
    }
    repairLabelsFromAnnotations();
    // Removed auto-loading of previous session image based on user request
  } catch {
    localStorage.removeItem(storageKey);
  }
}

export function deleteClass(classId) {
  snapshot();
  state.labels = state.labels.filter(l => l.id !== classId);
  // Also delete associated annotations
  state.annotations = state.annotations.filter(a => a.labelId !== classId);
  if (state.activeLabelId === classId) {
    state.activeLabelId = state.labels.length > 0 ? state.labels[0].id : null;
  }
  if (state.selectedId && !state.annotations.find(a => a.id === state.selectedId)) {
    state.selectedId = null;
    view.drag = null;
  }
  render();
  save();
}

export function renderClasses() {
  classesList.innerHTML = "";

  if (!state.labels.length) {
    const empty = document.createElement("p");
    empty.className = "chip-count";
    empty.textContent = "No classes defined";
    classesList.appendChild(empty);
  }

  // Ensure there's always an active label if labels exist
  if (!state.activeLabelId && state.labels.length > 0) {
    state.activeLabelId = state.labels[0].id;
  }

  state.labels.forEach((label) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = `class-item${label.id === state.activeLabelId ? " is-active" : ""}`;
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

    item.innerHTML = `
      <div style="display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0;">
        <span class="swatch" style="background:${label.color || '#65727f'}; flex-shrink: 0;"></span>
        <strong class="class-name" style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"></strong>
        <span class="class-count" style="font-size: 0.75rem; color: var(--muted); margin-left: 4px; flex-shrink: 0;">(${count})</span>
      </div>
      <div class="class-actions" style="display: flex; align-items: center; gap: 4px; flex-shrink: 0;">
        <span class="edit-class-btn" title="Edit class" style="cursor: pointer; color: var(--muted); display: grid; place-items: center; width: 20px; height: 20px;">
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
        </span>
        <span class="delete-class-btn" title="Delete class" style="cursor: pointer; color: #ff6b6b; display: grid; place-items: center; width: 20px; height: 20px;">
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
        </span>
      </div>
    `;
    item.querySelector(".class-name").textContent = labelDisplayName(label);

    // Click on the item itself sets it as active
    item.addEventListener("click", (e) => {
      if (e.target.closest('.class-actions') || e.target.closest('.edit-class-form')) return;
      state.activeLabelId = label.id;

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

    // Click on delete button
    item.querySelector(".delete-class-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      if (confirm(`Delete class "${labelDisplayName(label)}" and all its annotations?`)) {
        deleteClass(label.id);
      }
    });

    // Click on edit button
    item.querySelector(".edit-class-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      item.innerHTML = `
        <form class="edit-class-form" style="display: flex; gap: 4px; width: 100%; align-items: center;" onsubmit="event.preventDefault();">
          <input type="text" class="edit-class-name" value="${label.name}" required style="flex: 1; min-width: 0; padding: 2px 4px; font-size: 0.85rem;" onclick="event.stopPropagation()">
          <input type="color" class="edit-class-color" value="${label.color}" style="width: 24px; height: 24px; padding: 0; border: none; flex-shrink: 0;" onclick="event.stopPropagation()">
          <button type="submit" class="primary save-edit-btn" style="padding: 2px 6px; font-size: 0.75rem; border: none; border-radius: 4px; flex-shrink: 0;" onclick="event.stopPropagation()">Save</button>
          <button type="button" class="cancel-edit-btn" style="padding: 2px 6px; font-size: 0.75rem; background: var(--panel-2); border: 1px solid var(--line); border-radius: 4px; flex-shrink: 0;" onclick="event.stopPropagation()">Cancel</button>
        </form>
      `;
      const form = item.querySelector(".edit-class-form");
      const nameInput = item.querySelector(".edit-class-name");
      const colorInput = item.querySelector(".edit-class-color");
      nameInput.focus();

      const finishEdit = (saveChanges) => {
        if (saveChanges) {
          const newName = nameInput.value.trim();
          if (newName && (newName !== label.name || colorInput.value !== label.color)) {
            snapshot();
            label.name = newName;
            label.color = colorInput.value;
            save();
            setStatus(`Updated class: ${label.name}`);
          }
        }
        render(); // This will re-render classes list with original structure or new values
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
    item.className = `annotation-item${isActive ? " is-active" : ""}`;
    item.style.display = "flex";
    item.style.alignItems = "center";
    item.style.justifyContent = "space-between";
    item.innerHTML = `
      <div style="display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0;">
        <span class="swatch" style="background:${label.color || '#65727f'}; flex-shrink: 0;"></span>
        <strong class="ann-name" style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"></strong>
        <span class="ann-pts" style="font-size: 0.75rem; color: var(--muted); margin-left: 4px; flex-shrink: 0;"></span>
      </div>
      <div class="annotation-actions" style="display: flex; align-items: center; gap: 4px; flex-shrink: 0;">
        <span class="edit-ann-btn" title="Edit object class" style="cursor: pointer; color: var(--muted); display: grid; place-items: center; width: 20px; height: 20px;">
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
        </span>
        <span class="delete-ann-btn" title="Delete object" style="cursor: pointer; color: #ff6b6b; display: grid; place-items: center; width: 20px; height: 20px;">
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
        </span>
      </div>
    `;

    let text = annotation.type === "comment" ? `💬 ${annotation.text || "Comment"}` : `${displayCount}. ${labelDisplayName(label)}`;
    if (isGroup) {
      text = `${displayCount}. ${labelDisplayName(label)} (Group of ${groupAnns.length})`;
    }
    item.querySelector(".ann-name").textContent = text;
    item.querySelector(".ann-pts").textContent = annotation.type === "comment" ? "" : `${totalPoints} pts`;

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

    item.querySelector(".delete-ann-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      if (confirm(`Delete this object?`)) {
        snapshot();
        if (isGroup) {
          state.annotations = state.annotations.filter(a => a.groupId !== annotation.groupId);
        } else {
          state.annotations = state.annotations.filter(a => a.id !== annotation.id);
        }
        state.selectedIds.clear();
        state.selectedId = null;
        save();
        render();
      }
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
      selectedInfo.textContent = `${labelDisplayName(labelById(selected.labelId))}, ${annotationPoints(selected).length} points`;
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
    shapeHint.textContent = "Select a class, then draw a polygon.";
  } else if (state.shape === "comment") {
    shapeHint.textContent = "Click anywhere on the image to leave a comment.";
  } else {
    shapeHint.textContent = "Select a class, then draw a bounding box.";
  }
  autoDetectButton.disabled = detectState.detectionBusy || !view.imageLoaded;
  const labelSpan = autoDetectButton.querySelector(".btn-label");
  if (labelSpan) {
    labelSpan.textContent = detectState.detectionBusy ? "Detecting..." : "Detect";
  }
  autoDetectButton.title = selectedAnnotation() ? "Detect objects inside the selected area" : "Detect objects in the whole image";
  undoButton.disabled = state.history.length === 0;
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
  exportMenuButton.disabled = noData;
  labelStudioButton.disabled = labelStudioBusy || noData;
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

export async function sendToEndpoint() {
  const url = labelStudioProxyInput.value.trim();
  if (!url) {
    setStatus("URL required");
    return;
  }
  if (!view.imageLoaded) {
    setStatus("Load image first");
    return;
  }
  if (!state.annotations.length) {
    setStatus("No annotations");
    return;
  }

  localStorage.setItem(labelStudioStorageKey, url);
  setLabelStudioBusy(true);
  setStatus("Sending");

  try {
    const payload = buildCocoExport();
    const response = await apiFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      throw new Error(`Endpoint returned ${response.status}`);
    }

    setStatus(`Sent successfully`);
  } catch (error) {
    console.error(error);
    setStatus("Sync failed");
    window.alert(error.message || "Sync failed.");
  } finally {
    setLabelStudioBusy(false);
  }
}

export function importData(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const payload = JSON.parse(reader.result);
      snapshot();

      let importedLabels = [];
      let importedAnnotations = [];

      if (Array.isArray(payload)) {
        payload.forEach(task => {
          if (Array.isArray(task.annotations)) {
            task.annotations.forEach(ann => {
              importedAnnotations.push({ ...ann, _imgWidth: task.width, _imgHeight: task.height });
            });
          }
        });
      } else {
        if (Array.isArray(payload.labels)) importedLabels = payload.labels;
        if (Array.isArray(payload.annotations)) importedAnnotations = payload.annotations;
      }

      if (importedLabels.length) {
        state.labels = importedLabels.map((label) => ({
          id: label.id || generateUUID(),
          name: normalizeClassName(label.name || label.label || "object"),
          color: label.color || colorForName(label.name || label.label || "object")
        }));
      }

      if (importedAnnotations.length) {
        const currentImageWidth = view.imageLoaded ? (view.imageElement.naturalWidth || 1) : 1;
        const currentImageHeight = view.imageLoaded ? (view.imageElement.naturalHeight || 1) : 1;

        state.annotations = importedAnnotations.map((item) => {
          const labelName = item.title || item.label || item.detectedClass || labelById(item.labelId)?.name || "object";
          const label = ensureLabel(labelName);

          let parsedPoints = null;
          if (Array.isArray(item.points) && item.points.length >= 3) {
            if (typeof item.points[0] === 'number') {
              parsedPoints = [];
              for (let i = 0; i < item.points.length; i += 2) {
                parsedPoints.push({ x: Number(item.points[i]) || 0, y: Number(item.points[i + 1]) || 0 });
              }
            } else {
              parsedPoints = item.points.map((point) => ({ x: Number(point.x) || 0, y: Number(point.y) || 0 }));
            }
          }

          let scaleX = 1;
          let scaleY = 1;
          if (item._imgWidth && item._imgHeight && view.imageLoaded) {
            if (item._imgWidth !== currentImageWidth || item._imgHeight !== currentImageHeight) {
              scaleX = currentImageWidth / item._imgWidth;
              scaleY = currentImageHeight / item._imgHeight;
            }
          }

          if (parsedPoints) {
            if (scaleX !== 1 || scaleY !== 1) {
              parsedPoints = parsedPoints.map(p => ({ x: p.x * scaleX, y: p.y * scaleY }));
            }
          }

          let box = item.bbox || [item.x, item.y, item.width, item.height];
          if (scaleX !== 1 || scaleY !== 1) {
            const bx = (Number(box[0]) || 0) * scaleX;
            const by = (Number(box[1]) || 0) * scaleY;
            const bw = (Number(box[2]) || 1) * scaleX;
            const bh = (Number(box[3]) || 1) * scaleY;
            box = [bx, by, bw, bh];
          }

          const annotation = {
            id: item.id || generateUUID(),
            labelId: label.id,
            score: item.score,
            source: item.source,
            detectedClass: item.detectedClass,
            labelStudioTaskId: item.labelStudioTaskId,
            labelStudioAnnotationId: item.labelStudioAnnotationId
          };

          if (parsedPoints) {
            annotation.points = parsedPoints;
          } else {
            const x = Number(box[0]) || 0;
            const y = Number(box[1]) || 0;
            const width = Math.max(1, Number(box[2]) || 1);
            const height = Math.max(1, Number(box[3]) || 1);
            annotation.points = [
              { x, y },
              { x: x + width, y },
              { x: x + width, y: y + height },
              { x, y: y + height }
            ];
          }

          updateAnnotationBounds(annotation);
          return annotation;
        });
      }
      repairLabelsFromAnnotations();
      state.selectedId = null;
      render();
      save();
    } catch (e) {
      console.error(e);
      setStatus("Import failed");
    }
  };
  reader.readAsText(file);
}

export function importCsvData(file) {
  const reader = new FileReader();
  reader.onload = () => {
    try {
      const csv = reader.result;
      const lines = csv.split("\n");
      if (lines.length <= 1) return;

      const headerLine = lines[0].toLowerCase();
      const hasImgDims = headerLine.includes("imgwidth") && headerLine.includes("imgheight");

      snapshot();
      const newAnnotations = [];
      const currentImageWidth = view.imageLoaded ? (view.imageElement.naturalWidth || 1) : 1;
      const currentImageHeight = view.imageLoaded ? (view.imageElement.naturalHeight || 1) : 1;

      for (let i = 1; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) continue;

        const firstQuoteIdx = line.indexOf('"[{"');
        let cols = [];
        let pointsStr = null;

        if (firstQuoteIdx !== -1) {
          const before = line.substring(0, firstQuoteIdx);
          cols = before.split(",").map(c => c.trim()).filter(c => c !== "");
          const after = line.substring(firstQuoteIdx);
          if (after.startsWith('"') && after.endsWith('"')) {
            pointsStr = after.substring(1, after.length - 1).replace(/""/g, '"');
          }
        } else {
          cols = line.split(",");
        }

        if (cols.length >= 7) {
          const labelName = cols[1];
          let x = Number(cols[3]);
          let y = Number(cols[4]);
          let width = Number(cols[5]);
          let height = Number(cols[6]);

          let imgW = 0, imgH = 0;
          if (hasImgDims && cols.length >= 9) {
            imgW = Number(cols[7]);
            imgH = Number(cols[8]);
          }

          const label = ensureLabel(labelName);

          let points = [];
          if (pointsStr) {
            try {
              points = JSON.parse(pointsStr);
            } catch (e) {
              console.error("Failed to parse points", pointsStr);
            }
          }

          let scaleX = 1;
          let scaleY = 1;
          if (imgW && imgH && view.imageLoaded) {
            if (imgW !== currentImageWidth || imgH !== currentImageHeight) {
              scaleX = currentImageWidth / imgW;
              scaleY = currentImageHeight / imgH;
            }
          }

          if (points && points.length > 0) {
            if (scaleX !== 1 || scaleY !== 1) {
              points = points.map(p => ({ x: p.x * scaleX, y: p.y * scaleY }));
            }
          } else {
            x *= scaleX;
            y *= scaleY;
            width *= scaleX;
            height *= scaleY;
            points = [
              { x, y },
              { x: x + width, y },
              { x: x + width, y: y + height },
              { x, y: y + height }
            ];
          }

          const annotation = {
            id: generateUUID(),
            labelId: label.id,
            points: points
          };
          updateAnnotationBounds(annotation);
          newAnnotations.push(annotation);
        }
      }

      state.annotations = newAnnotations;
      repairLabelsFromAnnotations();
      state.selectedId = null;
      render();
      save();
      setStatus("Imported CSV");
    } catch (e) {
      console.error(e);
      setStatus("Import failed");
    }
  };
  reader.readAsText(file);
}

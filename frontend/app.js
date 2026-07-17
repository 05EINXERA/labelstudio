if (!localStorage.getItem('logged_in')) {
  window.location.href = '/';
}
function generateUUID() {
  if (typeof crypto !== 'undefined' && crypto['randomUUID']) {
    return crypto['randomUUID']();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
    var r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

async function apiFetch(url, options = {}) {
  const logged_in = localStorage.getItem('logged_in');
  if (!logged_in) {
    window.location.href = '/';
    return;
  }
  options.headers = { ...options.headers };
  const res = await fetch(url, options);
  if (res.status === 401) {
    localStorage.removeItem('logged_in');
    localStorage.removeItem('dataset_username');
    window.location.href = '/';
  }
  return res;
}

const canvas = document.querySelector("#annotationCanvas");
const ctx = canvas.getContext("2d");
const imageCanvas = document.querySelector("#imageCanvas");
const imageCtx = imageCanvas.getContext("2d");
const staticCanvas = document.querySelector("#staticCanvas");
const staticCtx = staticCanvas.getContext("2d");

const imageInput = document.querySelector("#imageInput");
const imageName = document.querySelector("#imageName");
const imageSize = document.querySelector("#imageSize");
const emptyState = document.querySelector("#emptyState");
const addClassButton = document.querySelector("#addClassButton");
const classesList = document.querySelector("#classesList");
const newClassForm = document.querySelector("#newClassForm");
const newClassName = document.querySelector("#newClassName");
const newClassColor = document.querySelector("#newClassColor");
const saveStatus = document.querySelector("#saveStatus");
const annotationList = document.querySelector("#annotationList");
const annotationCount = document.querySelector("#annotationCount");
const selectedInfo = document.querySelector("#selectedInfo");
const drawMode = document.querySelector("#drawMode");
const selectMode = document.querySelector("#selectMode");
const boxMode = document.querySelector("#boxMode");
const polygonMode = document.querySelector("#polygonMode");
const commentMode = document.querySelector("#commentMode");
const magicWandMode = document.querySelector("#magicWandMode");
const autoDetectButton = document.querySelector("#autoDetectButton");
const autoTagButton = document.querySelector("#autoTagButton");
const undoButton = document.querySelector("#undoButton");
const deleteButton = document.querySelector("#deleteButton");
const clearButton = document.querySelector("#clearButton");
const aiSettingsMenuButton = document.querySelector("#aiSettingsMenuButton");
const aiSettingsDropdownContainer = document.querySelector("#aiSettingsDropdownContainer");
const importMenuButton = document.querySelector("#importMenuButton");
const importDropdown = document.querySelector("#importDropdown").parentElement;
const importJsonButton = document.querySelector("#importJsonButton");
const importCsvButton = document.querySelector("#importCsvButton");
const importJsonInput = document.querySelector("#importJsonInput");
const importCsvInput = document.querySelector("#importCsvInput");
const exportMenuButton = document.querySelector("#exportMenuButton");
const exportDropdown = document.querySelector("#exportDropdown").parentElement;
const exportJsonButton = document.querySelector("#exportJsonButton");
const exportCsvButton = document.querySelector("#exportCsvButton");
const labelStudioProxyInput = document.querySelector("#labelStudioProxyInput");
const labelStudioProjectInput = document.querySelector("#labelStudioProjectInput");
const labelStudioTaskInput = document.querySelector("#labelStudioTaskInput");
const labelStudioFromInput = document.querySelector("#labelStudioFromInput");
const labelStudioToInput = document.querySelector("#labelStudioToInput");
const labelStudioButton = document.querySelector("#labelStudioButton");
const stageWrap = document.querySelector(".stage-wrap");
const shapeHint = document.querySelector("#shapeHint");
const prevImageButton = document.querySelector("#prevImageButton");
const nextImageButton = document.querySelector("#nextImageButton");
const galleryPosition = document.querySelector("#galleryPosition");
const clearGalleryButton = document.querySelector("#clearGalleryButton");
const logoutBtnApp = document.querySelector("#logoutBtnApp");

let commentOverlay = document.querySelector("#commentOverlay");
let commentOverlayInput = document.querySelector("#commentOverlayInput");

if (!commentOverlay) {
  const styleHtml = `
    <style>
      .comment-overlay {
        position: absolute;
        top: 0;
        left: 0;
        background-color: var(--panel);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px;
        width: 240px;
        box-shadow: var(--shadow);
        z-index: 100;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .comment-overlay textarea {
        width: 100%;
        resize: vertical;
        background-color: var(--bg);
        border: 1px solid var(--line);
        border-radius: 4px;
        color: var(--ink);
        padding: 8px;
        font-family: inherit;
        font-size: 0.9rem;
      }
      .comment-overlay textarea:focus {
        outline: none;
        border-color: var(--accent);
        box-shadow: 0 0 0 2px rgba(15, 139, 141, 0.2);
      }
      .comment-overlay.is-hidden {
        display: none;
      }
    </style>
  `;
  document.head.insertAdjacentHTML('beforeend', styleHtml);
  
  const overlayHtml = `
    <div id="commentOverlay" class="comment-overlay is-hidden">
      <textarea id="commentOverlayInput" placeholder="Enter comment and press Enter..." rows="3"></textarea>
    </div>
  `;
  stageWrap.insertAdjacentHTML('beforeend', overlayHtml);
  commentOverlay = document.querySelector("#commentOverlay");
  commentOverlayInput = document.querySelector("#commentOverlayInput");
}
let pendingCommentPoint = null;
let pendingCommentEditId = null;

const storageKey = "image-annotation-mvp-v1";
const labelStudioStorageKey = "image-annotation-label-studio-settings";
const handleSize = 9;
const closeThreshold = 8;
const labelPalette = [
  "#0f8b8d", "#e85d75", "#f4a261", "#2a9d8f", "#7b2cbf",
  "#3f88c5", "#d95d39", "#65727f", "#8d6e63", "#4dabf7",
  "#c84c4c", "#096769", "#b5179e", "#4895ef"
];

let state = {
  labels: [],
  annotations: [],
  image: null,
  gallery: [],
  galleryIndex: -1,
  _selectedId: null,
  selectedIds: new Set(),
  activeLabelId: null,
  mode: "draw",
  shape: "box",
  history: []
};

Object.defineProperty(state, "selectedId", {
  get() {
    return this._selectedId;
  },
  set(id) {
    this._selectedId = id;
    if (id === null) {
      this.selectedIds.clear();
    } else {
      if (!this.selectedIds.has(id)) {
        this.selectedIds.clear();
        const ann = this.annotations.find(a => a.id === id);
        if (ann && ann.groupId) {
          this.annotations.forEach(a => {
            if (a.groupId === ann.groupId) this.selectedIds.add(a.id);
          });
        } else {
          this.selectedIds.add(id);
        }
      }
    }
  }
});

let imageElement = new Image();
let imageLoaded = false;
let viewZoom = 1;
let viewPan = { x: 0, y: 0 };
let isPanning = false;
let panStart = { x: 0, y: 0, panX: 0, panY: 0 };
let imageBox = { x: 0, y: 0, width: 0, height: 0, scale: 1 };
let drag = null;
let hoverHandle = null;
let hoveredLineIndex = -1;
let selectedLineIndex = -1;
let hoveredPointIndex = -1;
let labelStudioBusy = false;
let detectionBusy = false;

function normalizeClassName(className) {
  return String(className || "object").trim().toLowerCase().replace(/_/g, " ");
}

function formatClassName(className) {
  return normalizeClassName(className)
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

function labelDisplayName(label) {
  return formatClassName(label?.name || "object");
}

function exportLabelName(annotation, label) {
  return annotation.detectedClass || label?.name || "object";
}

let nextColorIndex = -1;

function colorForName(name) {
  if (nextColorIndex === -1) {
    nextColorIndex = state.labels.length;
  }
  const color = labelPalette[nextColorIndex % labelPalette.length];
  nextColorIndex++;
  return color;
}

function labelByName(name) {
  const normalized = normalizeClassName(name);
  return state.labels.find((label) => label.name === normalized) || null;
}

function ensureLabel(className, customColor = null) {
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

function pruneUnusedLabels() {
  // Unused labels are no longer pruned to allow pre-defining classes
}

function repairLabelsFromAnnotations() {
  state.annotations = state.annotations.map((annotation) => {
    const existing = state.labels.find((label) => label.id === annotation.labelId);
    if (existing) return annotation;

    const label = ensureLabel(annotation.detectedClass || "object");
    return { ...annotation, labelId: label.id };
  });
}

function resetWorkspaceForNewImage() {
  // state.labels is deliberately not cleared to persist classes across images
  state.annotations = [];
  state.selectedId = null;
}

function labelById(id) {
  const label = state.labels.find((item) => item.id === id);
  if (label) return label;
  return { id, name: "object", color: "#65727f" };
}

function setStatus(text) {
  saveStatus.textContent = text;
  window.clearTimeout(setStatus.timer);
  setStatus.timer = window.setTimeout(() => {
    saveStatus.textContent = "Saved";
  }, 1200);
}

function setLabelStudioBusy(isBusy) {
  labelStudioBusy = isBusy;
  labelStudioButton.disabled = isBusy || !imageLoaded || state.annotations.length === 0;
  labelStudioButton.textContent = isBusy ? "Sending..." : "Send annotations";
}

function setDetectionBusy(isBusy) {
  detectionBusy = isBusy;
  autoDetectButton.disabled = isBusy || !imageLoaded;
  const labelSpan = autoDetectButton.querySelector(".btn-label");
  if (labelSpan) {
    labelSpan.textContent = isBusy ? "Detecting..." : "Detect";
  }
}

/**
 * Returns the image source suitable for the AI API.
 * If the current image src is a blob: URL (local file), converts it to a
 * base64 data URL via a canvas, since blob URLs are browser-only and
 * cannot be fetched by the backend server.
 */
async function getImageSrcForAPI() {
  const src = state.image?.src || imageElement?.src;
  if (!src) return null;
  // If it's already a normal URL or base64 data URL, send as-is
  if (!src.startsWith("blob:")) return src;
  // Convert blob URL -> base64 via canvas
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => {
      const cvs = document.createElement("canvas");
      cvs.width = img.naturalWidth;
      cvs.height = img.naturalHeight;
      cvs.getContext("2d").drawImage(img, 0, 0);
      resolve(cvs.toDataURL("image/jpeg", 0.92));
    };
    img.onerror = reject;
    img.src = src;
  });
}

async function pollJob(jobId, controller) {
  while (true) {
    if (controller && controller.signal.aborted) throw new Error("Aborted");
    const res = await apiFetch(`${window.location.origin}/api/detect/status/${jobId}`);
    if (res.status === 404) throw new Error("Job not found or expired");
    if (!res.ok) throw new Error(`Polling failed (${res.status})`);
    
    const data = await res.json();
    if (data.status === "completed") return data.result;
    if (data.status === "failed") throw new Error(data.error);
    
    await new Promise(r => setTimeout(r, 1000));
  }
}

async function autoDetectObjects({ replace = true } = {}) {
  if (!imageLoaded || detectionBusy) return 0;

  const selected = selectedAnnotation();
  const selection = selected
    ? (Array.isArray(selected.points) && selected.points.length >= 3
      ? {
          points: selected.points.map((point) => ({
            x: round(point.x),
            y: round(point.y)
          }))
        }
      : {
          x: round(selected.x),
          y: round(selected.y),
          width: round(selected.width),
          height: round(selected.height)
        })
    : null;

  setDetectionBusy(true);
  setStatus(selection ? "Detecting selection" : "Detecting");

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 60000);

  try {
    const imageSrc = await getImageSrcForAPI();
    const response = await apiFetch(`${window.location.origin}/api/detect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        image: imageSrc,
        selection,
        model_size: localStorage.getItem("ai_model_size") || "n",
        confidence: parseFloat(localStorage.getItem("ai_conf") || "0.35"),
        nms_threshold: parseFloat(localStorage.getItem("ai_nms") || "0.45")
      })
    });
    const payload = await response.json();
    if (!response.ok) {
      let detailMsg = payload.detail;
      if (typeof detailMsg === 'object') detailMsg = JSON.stringify(detailMsg);
      throw new Error(detailMsg || payload.error || `Detection failed (${response.status})`);
    }

    const { job_id } = payload;
    const result = await pollJob(job_id, controller);
    clearTimeout(timeoutId);

    const predictions = result.predictions || [];
    snapshot();

    if (!predictions.length) {
      if (replace) {
        if (selected) {
          state.annotations = state.annotations.filter((item) => item.id === selected.id || item.source !== "auto-detect");
          state.selectedId = selected.id;
        } else {
          state.annotations = state.annotations.filter(item => item.source !== "auto-detect");
          state.selectedId = null;
        }
        pruneUnusedLabels();
      }
      render();
      save();
      setStatus("No objects found");
      return 0;
    }

    const detected = predictionsToAnnotations(predictions);
    if (replace) {
      if (selected) {
        const preserved = state.annotations.filter((item) => item.id === selected.id || item.source !== "auto-detect");
        state.annotations = [...preserved, ...detected];
        state.selectedId = selected.id;
      } else {
        const preserved = state.annotations.filter((item) => item.source !== "auto-detect");
        state.annotations = [...preserved, ...detected];
        state.selectedId = null;
      }
      pruneUnusedLabels();
    } else {
      state.annotations.push(...detected);
    }
    render();
    save();
    setStatus(`Found ${detected.length} objects`);
    return detected.length;
  } catch (error) {
    console.error(error);
    setStatus("Detect failed");
    if (error.name === 'AbortError') {
      window.alert("AI could not detect");
    } else {
      window.alert(error.message || "Automatic object detection failed. Is server.py running?");
    }
    return 0;
  } finally {
    setDetectionBusy(false);
  }
}

async function autoTagObjects() {
  if (!imageLoaded || detectionBusy) return;

  const selected = selectedAnnotation();
  const selection = selected
    ? (Array.isArray(selected.points) && selected.points.length >= 3
      ? {
          points: selected.points.map((point) => ({
            x: round(point.x),
            y: round(point.y)
          }))
        }
      : {
          x: round(selected.x),
          y: round(selected.y),
          width: round(selected.width),
          height: round(selected.height)
        })
    : null;

  setDetectionBusy(true);
  setStatus(selection ? "Auto-tagging selection..." : "Auto-tagging image...");

  try {
    const payload = {
      image: await getImageSrcForAPI(),
      selection
    };

    const response = await apiFetch(`${window.location.origin}/api/detect/classify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    const data = await response.json();
    if (!response.ok) {
      throw new Error(`Auto-tag failed (${response.status})`);
    }

    const { job_id } = data;
    const result = await pollJob(job_id, null);
    const tags = result.tags || [];

    if (tags && tags.length > 0) {
      setStatus(`Found ${tags.length} tags`);
      showAutoTagModal(tags);
    } else {
      setStatus("No tags found");
    }
  } catch (error) {
    console.error(error);
    setStatus("Auto-tag failed");
    window.alert(error.message || "Auto-tagging failed. Is server.py running?");
  } finally {
    setDetectionBusy(false);
  }
}

function showAutoTagModal(tags) {
  const modal = document.getElementById("autoTagModal");
  const suggestionsContainer = document.getElementById("autoTagSuggestions");
  const input = document.getElementById("autoTagCustomInput");
  const applyBtn = document.getElementById("autoTagApplyBtn");
  const cancelBtn = document.getElementById("autoTagCancelBtn");
  const closeBtn = document.getElementById("autoTagClose");
  const colorsContainer = document.getElementById("autoTagSelectedColors");
  const tagColors = {};

  suggestionsContainer.innerHTML = '';
  input.value = tags[0]?.class || "";

  function getSelectedTags() {
    return input.value.split(',').map(s => s.trim()).filter(s => s);
  }

  function updateSuggestionStyles() {
    const selected = getSelectedTags();
    Array.from(suggestionsContainer.children).forEach(btn => {
      if (selected.includes(btn.dataset.tagClass)) {
        btn.classList.add("primary");
        btn.style.opacity = "1";
      } else {
        btn.classList.remove("primary");
        btn.style.opacity = "0.7";
      }
    });

    if (colorsContainer) {
      colorsContainer.innerHTML = '';
      if (selected.length > 0) {
        colorsContainer.style.display = 'flex';
        selected.forEach(tag => {
          if (!tagColors[tag]) tagColors[tag] = labelByName(tag)?.color || colorForName(tag);
          
          const row = document.createElement("div");
          row.style.display = "flex";
          row.style.alignItems = "center";
          row.style.gap = "8px";
          
          const colorPicker = document.createElement("input");
          colorPicker.type = "color";
          colorPicker.value = tagColors[tag];
          colorPicker.style.width = "30px";
          colorPicker.style.height = "30px";
          colorPicker.style.padding = "0";
          colorPicker.style.border = "none";
          colorPicker.style.borderRadius = "4px";
          colorPicker.style.cursor = "pointer";
          
          colorPicker.addEventListener("input", (e) => {
            tagColors[tag] = e.target.value;
          });
          
          const label = document.createElement("span");
          label.textContent = tag;
          label.style.fontSize = "0.9rem";
          
          row.appendChild(colorPicker);
          row.appendChild(label);
          colorsContainer.appendChild(row);
        });
      } else {
        colorsContainer.style.display = 'none';
      }
    }
  }

  tags.forEach(tag => {
    const btn = document.createElement("button");
    btn.className = "tool-button";
    btn.style.padding = "6px 12px";
    btn.style.borderRadius = "20px";
    btn.style.fontSize = "0.85rem";
    btn.style.transition = "all 0.2s ease";
    btn.dataset.tagClass = tag.class;
    btn.textContent = `${tag.class} (${(tag.score * 100).toFixed(1)}%)`;
    
    btn.onclick = () => {
      let selected = getSelectedTags();
      if (selected.includes(tag.class)) {
        selected = selected.filter(s => s !== tag.class);
      } else {
        selected.push(tag.class);
      }
      input.value = selected.join(", ");
      updateSuggestionStyles();
    };
    suggestionsContainer.appendChild(btn);
  });

  input.addEventListener("input", updateSuggestionStyles);
  updateSuggestionStyles();

  const closeModal = () => {
    modal.classList.remove('is-active');
    input.removeEventListener("input", updateSuggestionStyles);
    applyBtn.removeEventListener("click", onApply);
    cancelBtn.removeEventListener("click", closeModal);
    closeBtn.removeEventListener("click", closeModal);
  };

  const onApply = () => {
    const classNames = getSelectedTags();

    if (classNames.length > 0) {
      classNames.forEach(className => ensureLabel(className, tagColors[className]));
      setStatus(`Added tags: ${classNames.join(", ")}`);
      render();
    }
    closeModal();
  };

  applyBtn.addEventListener("click", onApply);
  cancelBtn.addEventListener("click", closeModal);
  closeBtn.addEventListener("click", closeModal);

  modal.classList.add('is-active');
}

async function performMagicWandSegmentation(point, bbox = null, isShift = false, isAlt = false) {
  if (!imageLoaded || detectionBusy) return;

  setDetectionBusy(true);
  setStatus("Segmenting object...");

  try {
    const activeLabelId = state.activeLabelId;
    const label = state.labels.find(l => l.id === activeLabelId);
    const labelName = label ? label.name : null;
    
    let existingAnnotation = null;
    if ((isShift || isAlt) && state.selectedId) {
      existingAnnotation = state.annotations.find(a => a.id === state.selectedId && a.source === "magic-wand");
    }

    let promptPoints = [];
    let promptLabels = [];

    if (existingAnnotation && existingAnnotation.promptPoints) {
      promptPoints = [...existingAnnotation.promptPoints];
      promptLabels = [...existingAnnotation.promptLabels];
    }

    promptPoints.push({ x: Math.round(point.x), y: Math.round(point.y) });
    promptLabels.push(isAlt ? 0 : 1);
    
    const precisionSlider = document.getElementById("magicWandPrecision");
    const precisionVal = precisionSlider ? parseInt(precisionSlider.value) : 70;
    const epsilonMult = 0.01 - (precisionVal / 100) * 0.0099;

    const imageSrc = await getImageSrcForAPI();
    const response = await apiFetch(`${window.location.origin}/api/detect/segment`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image: imageSrc,
        points: promptPoints,
        labels: promptLabels,
        prompt: labelName,
        precision: epsilonMult,
        bbox: bbox,
        sam_model: localStorage.getItem("ai_sam_model") || "mobile_sam.pt"
      })
    });
    
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.detail || `Segmentation failed (${response.status})`);
    }

    const { job_id } = payload;
    const result = await pollJob(job_id, null);
    const points = result.points || [];
    if (!points.length) {
      setStatus("No object found at points");
      return;
    }

    snapshot();

    if (existingAnnotation) {
      existingAnnotation.points = points;
      existingAnnotation.promptPoints = promptPoints;
      existingAnnotation.promptLabels = promptLabels;
      updateAnnotationBounds(existingAnnotation);
    } else {
      const labelId = state.activeLabelId || ensureLabel("object").id;
      const annotation = {
        id: generateUUID(),
        labelId: labelId,
        points: points,
        promptPoints: promptPoints,
        promptLabels: promptLabels,
        source: "magic-wand"
      };
      updateAnnotationBounds(annotation);
      
      state.annotations.push(annotation);
      state.selectedId = annotation.id;
    }
    
    render();
    save();
    setStatus("Segmented object");
  } catch (error) {
    console.error(error);
    setStatus("Segmentation failed");
    window.alert(error.message || "Segmentation failed.");
  } finally {
    setDetectionBusy(false);
  }
}

function predictionsToAnnotations(predictions) {
  return predictions.map((prediction) => {
    const [x, y, width, height] = prediction.bbox;
    const label = ensureLabel(prediction.class);
    const box = {
      x: round(Math.max(0, x)),
      y: round(Math.max(0, y)),
      width: round(Math.max(1, width)),
      height: round(Math.max(1, height))
    };
    return {
      id: generateUUID(),
      labelId: label.id,
      points: prediction.points || [
        { x: box.x, y: box.y },
        { x: box.x + box.width, y: box.y },
        { x: box.x + box.width, y: box.y + box.height },
        { x: box.x, y: box.y + box.height }
      ],
      x: box.x,
      y: box.y,
      width: box.width,
      height: box.height,
      score: round(prediction.score),
      source: "auto-detect",
      type: prediction.points ? "polygon" : "box",
      detectedClass: prediction.class
    };
  });
}

function snapshot() {
  state.history.push(JSON.stringify({
    labels: state.labels,
    annotations: state.annotations,
    selectedId: state.selectedId
  }));
  if (state.history.length > 50) {
    state.history.shift();
  }
}

let backendSyncTimeout = null;

function syncToBackend() {
  if (typeof state === 'undefined' || state.galleryIndex < 0 || !state.gallery || !state.gallery[state.galleryIndex]) return;
  const currentTask = state.gallery[state.galleryIndex];
  if (!currentTask.id) return;
  
  const timeDelta = taskSessionSeconds;
  taskSessionSeconds = 0;
  const username = localStorage.getItem('dataset_username') || 'Unknown';
  let taskStatus = currentTask.status;
  if (taskStatus === 'New') taskStatus = 'In Progress';
  currentTask.status = taskStatus;
  currentTask.annotations = [...state.annotations];
  
  apiFetch('/api/tasks', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      id: currentTask.id,
      status: taskStatus,
      time_spent_delta: timeDelta,
      assignee: username,
      annotations: JSON.stringify(currentTask.annotations),
      updated_at: currentTask.updated_at
    }),
    keepalive: true
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

function save() {
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

function flushPendingSaves() {
  if (window.backendSyncTimeout) {
    clearTimeout(window.backendSyncTimeout);
    window.backendSyncTimeout = null;
    syncToBackend();
  }
  if (typeof syncTimeToServer === 'function') {
    syncTimeToServer();
  }
}

window.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') {
    flushPendingSaves();
  }
});

window.addEventListener('beforeunload', () => {
  flushPendingSaves();
});

function loadSaved() {
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
  
  if (pendingCommentPoint) {
    pendingCommentPoint = null;
    commentOverlay.classList.add("is-hidden");
  }
  drawAllLayers();
}

function computeImageBox() {
  if (!imageLoaded) {
    imageBox = { x: 0, y: 0, width: 0, height: 0, scale: 1 };
    return;
  }

  const rect = canvas.getBoundingClientRect();
  const baseScale = Math.min(rect.width / imageElement.naturalWidth, rect.height / imageElement.naturalHeight);
  const scale = baseScale * viewZoom;
  const width = imageElement.naturalWidth * scale;
  const height = imageElement.naturalHeight * scale;
  
  imageBox = {
    x: (rect.width - width) / 2 + viewPan.x,
    y: (rect.height - height) / 2 + viewPan.y,
    width,
    height,
    scale
  };
}

function drawImageLayer() {
  const rect = imageCanvas.getBoundingClientRect();
  imageCtx.clearRect(0, 0, rect.width, rect.height);
  if (!imageLoaded) return;
  imageCtx.drawImage(imageElement, imageBox.x, imageBox.y, imageBox.width, imageBox.height);
}

function drawStaticLayer() {
  const rect = staticCanvas.getBoundingClientRect();
  staticCtx.clearRect(0, 0, rect.width, rect.height);
  if (!imageLoaded) return;
  
  state.annotations.forEach((annotation) => {
    const isSelected = state.selectedIds.has(annotation.id);
    const isDragging = drag?.annotationId === annotation.id || drag?.originals?.find(a => a.id === annotation.id);
    if (!isSelected && !isDragging) {
      drawAnnotation(annotation, false, staticCtx);
    }
  });
}

function drawAllLayers() {
  computeImageBox();
  drawImageLayer();
  drawStaticLayer();
  draw();
}

function draw() {
  const rect = canvas.getBoundingClientRect();
  ctx.clearRect(0, 0, rect.width, rect.height);
  computeImageBox();

  if (!imageLoaded) return;

  state.annotations.forEach((annotation) => {
    const isSelected = state.selectedIds.has(annotation.id);
    const isDragging = drag?.annotationId === annotation.id || drag?.originals?.find(a => a.id === annotation.id);
    if (isSelected || isDragging) {
      drawAnnotation(annotation, isSelected, ctx);
    }
  });

  if (drag?.draft) {
    drawAnnotation(drag.draft, true, ctx);
  }

  if (pendingCommentPoint) {
    const screenX = imageBox.x + pendingCommentPoint.x * imageBox.scale;
    const screenY = imageBox.y + pendingCommentPoint.y * imageBox.scale;
    ctx.save();
    ctx.beginPath();
    ctx.arc(screenX, screenY, 8, 0, Math.PI * 2);
    ctx.fillStyle = "#f4a261";
    ctx.fill();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.stroke();
    ctx.restore();
  }

  // Draw close-point indicator and preview line for active polygon drawing
  if (drag?.type === "draw-polygon") {
    const annotation = state.annotations.find((item) => item.id === drag.annotationId);
    const pts = annotation?.points || [];
    const label = annotation ? labelById(annotation.labelId) : null;
    const edgeColor = label ? label.color : "#0f8b8d";
    const fillColor = label ? hexToRgba(label.color, 0.2) : "rgba(15, 139, 141, 0.35)";

    // The starting point is now distinguished by filling it with the class color via drawVertexHandles
    // Draw preview line from last point to cursor
    if (pts.length >= 1 && drag.preview) {
      const last = pts[pts.length - 1];
      const sx = imageBox.x + last.x * imageBox.scale;
      const sy = imageBox.y + last.y * imageBox.scale;
      const ex = imageBox.x + drag.preview.x * imageBox.scale;
      const ey = imageBox.y + drag.preview.y * imageBox.scale;
      const mouseX = drag.previewCanvas ? drag.previewCanvas.x : ex;
      const mouseY = drag.previewCanvas ? drag.previewCanvas.y : ey;
 
      let isHoveringStart = false;
      let startX, startY;
      if (pts.length >= 3) {
        startX = imageBox.x + pts[0].x * imageBox.scale;
        startY = imageBox.y + pts[0].y * imageBox.scale;
        if (Math.hypot(startX - mouseX, startY - mouseY) < closeThreshold) {
          isHoveringStart = true;
        }
      }

      if (isHoveringStart) {
        ctx.save();
        ctx.fillStyle = fillColor;
        ctx.beginPath();
        pts.forEach((pt, i) => {
          const px = imageBox.x + pt.x * imageBox.scale;
          const py = imageBox.y + pt.y * imageBox.scale;
          if (i === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        });
        ctx.closePath();
        ctx.fill();
        ctx.restore();
      }

      ctx.save();
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = edgeColor;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(isHoveringStart ? startX : ex, isHoveringStart ? startY : ey);
      ctx.stroke();
      ctx.restore();
    }
  }
}

function annotationPoints(annotation) {
  if (Array.isArray(annotation?.points) && annotation.points.length >= 1) {
    return annotation.points.map((point) => ({
      x: Number(point.x) || 0,
      y: Number(point.y) || 0
    }));
  }

  const x = Number(annotation?.x) || 0;
  const y = Number(annotation?.y) || 0;
  const width = Math.max(1, Number(annotation?.width) || 1);
  const height = Math.max(1, Number(annotation?.height) || 1);
  return [
    { x, y },
    { x: x + width, y },
    { x: x + width, y: y + height },
    { x, y: y + height }
  ];
}

function updateAnnotationBounds(annotation) {
  const points = annotationPoints(annotation);
  if (!points.length) return;

  const xs = points.map((point) => point.x);
  const ys = points.map((point) => point.y);
  annotation.x = round(Math.min(...xs));
  annotation.y = round(Math.min(...ys));
  annotation.width = round(Math.max(...xs) - annotation.x);
  annotation.height = round(Math.max(...ys) - annotation.y);
  annotation.points = points.map((point) => ({ x: round(point.x), y: round(point.y) }));
}

function drawAnnotation(annotation, selected = false, targetCtx = ctx) {
  if (annotation.type === "comment") {
    const screenPoint = {
      x: imageBox.x + annotation.x * imageBox.scale,
      y: imageBox.y + annotation.y * imageBox.scale
    };
    targetCtx.save();
    targetCtx.fillStyle = selected ? "#f4a261" : "#e85d75";
    targetCtx.beginPath();
    targetCtx.arc(screenPoint.x, screenPoint.y, 8, 0, Math.PI * 2);
    targetCtx.fill();
    targetCtx.strokeStyle = "#ffffff";
    targetCtx.lineWidth = 2;
    targetCtx.stroke();

    const text = `${annotation.author || 'User'}: ${annotation.text}`;
    targetCtx.font = "600 12px Inter, system-ui, sans-serif";
    const tw = targetCtx.measureText(text).width + 12;
    targetCtx.fillStyle = "rgba(0,0,0,0.75)";
    targetCtx.beginPath();
    targetCtx.roundRect(screenPoint.x + 12, screenPoint.y - 12, tw, 24, 4);
    targetCtx.fill();
    targetCtx.fillStyle = "#ffffff";
    targetCtx.fillText(text, screenPoint.x + 18, screenPoint.y + 4);
    targetCtx.restore();
    return;
  }

  const label = labelById(annotation.labelId);
  const points = annotationPoints(annotation);
  const isPolygon = annotation.type === "polygon" || (points && points.length !== 4);
  const screenPoints = points.map((point) => ({
    x: imageBox.x + point.x * imageBox.scale,
    y: imageBox.y + point.y * imageBox.scale
  }));

  targetCtx.save();
  targetCtx.lineWidth = selected ? 3 : 2;
  targetCtx.strokeStyle = label.color;
  targetCtx.fillStyle = hexToRgba(label.color, selected ? 0.2 : 0.12);

  if (!screenPoints.length) {
    targetCtx.restore();
    return;
  }

  targetCtx.beginPath();
  screenPoints.forEach((point, index) => {
    if (index === 0) {
      targetCtx.moveTo(point.x, point.y);
    } else {
      targetCtx.lineTo(point.x, point.y);
    }
  });
  const isBeingDrawn = drag?.type === "draw-polygon" && drag?.annotationId === annotation.id;
  if (screenPoints.length >= 3 && !isBeingDrawn) {
    targetCtx.closePath();
    targetCtx.fill();
  }
  targetCtx.stroke();

  const firstPoint = screenPoints[0];
  const tag = labelDisplayName(label);
  const bounds = screenPoints.reduce((accumulator, point) => ({
    minX: Math.min(accumulator.minX, point.x),
    minY: Math.min(accumulator.minY, point.y),
    maxX: Math.max(accumulator.maxX, point.x),
    maxY: Math.max(accumulator.maxY, point.y)
  }), { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity });
  const tagWidth = Math.min(targetCtx.measureText(tag).width + 18, Math.max(bounds.maxX - bounds.minX, 54));
  const tagY = Math.max(4, bounds.minY - 24);
  targetCtx.font = "700 12px Inter, system-ui, sans-serif";
  targetCtx.fillStyle = label.color;
  targetCtx.fillRect(bounds.minX, tagY, tagWidth, 22);
  targetCtx.fillStyle = "#ffffff";
  targetCtx.fillText(tag, bounds.minX + 8, tagY + 15, tagWidth - 14);

  // Draw highlighted/selected line segments on the selected annotation
  if (selected && annotation.id === state.selectedId && screenPoints.length >= 3) {
    // Draw hovered line highlight
    if (hoveredLineIndex !== -1 && hoveredLineIndex !== selectedLineIndex) {
      const p1 = screenPoints[hoveredLineIndex];
      const p2 = screenPoints[(hoveredLineIndex + 1) % screenPoints.length];
      targetCtx.save();
      targetCtx.beginPath();
      targetCtx.moveTo(p1.x, p1.y);
      targetCtx.lineTo(p2.x, p2.y);
      targetCtx.strokeStyle = "rgba(255, 107, 107, 0.6)";
      targetCtx.lineWidth = 5;
      targetCtx.stroke();
      targetCtx.restore();
    }
    // Draw selected line highlight
    if (selectedLineIndex !== -1 && selectedLineIndex < screenPoints.length) {
      const p1 = screenPoints[selectedLineIndex];
      const p2 = screenPoints[(selectedLineIndex + 1) % screenPoints.length];
      targetCtx.save();
      targetCtx.beginPath();
      targetCtx.moveTo(p1.x, p1.y);
      targetCtx.lineTo(p2.x, p2.y);
      targetCtx.strokeStyle = "#ff4444";
      targetCtx.lineWidth = 5;
      targetCtx.stroke();
      // Draw small "×" delete hint at the midpoint
      const mx = (p1.x + p2.x) / 2;
      const my = (p1.y + p2.y) / 2;
      targetCtx.beginPath();
      targetCtx.arc(mx, my, 10, 0, Math.PI * 2);
      targetCtx.fillStyle = "rgba(255, 68, 68, 0.9)";
      targetCtx.fill();
      targetCtx.font = "bold 14px Inter, system-ui, sans-serif";
      targetCtx.fillStyle = "#ffffff";
      targetCtx.textAlign = "center";
      targetCtx.textBaseline = "middle";
      targetCtx.fillText("×", mx, my);
      targetCtx.restore();
    }
  }

  if (selected) {
    drawVertexHandles(screenPoints, label.color, targetCtx, isBeingDrawn);
  }
  targetCtx.restore();
}

function drawVertexHandles(points, color, targetCtx = ctx, isBeingDrawn = false) {
  const half = handleSize / 2;
  targetCtx.strokeStyle = color;
  targetCtx.lineWidth = 2;
  points.forEach((point, i) => {
    targetCtx.beginPath();
    targetCtx.arc(point.x, point.y, half, 0, Math.PI * 2);
    if (i === 0 && isBeingDrawn) {
      targetCtx.fillStyle = color;
    } else {
      targetCtx.fillStyle = "#ffffff";
    }
    targetCtx.fill();
    targetCtx.stroke();
  });
}

function hexToRgba(hex, alpha) {
  const clean = hex.replace("#", "");
  const value = parseInt(clean.length === 3 ? clean.split("").map((c) => c + c).join("") : clean, 16);
  const r = (value >> 16) & 255;
  const g = (value >> 8) & 255;
  const b = value & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

function canvasPoint(event) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: event.clientX - rect.left,
    y: event.clientY - rect.top
  };
}

function imagePoint(point) {
  return {
    x: clamp((point.x - imageBox.x) / imageBox.scale, 0, imageElement.naturalWidth),
    y: clamp((point.y - imageBox.y) / imageBox.scale, 0, imageElement.naturalHeight)
  };
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function pointInPolygon(point, polygon) {
  if (!polygon?.length) return false;

  let inside = false;
  for (let index = 0, nextIndex = polygon.length - 1; index < polygon.length; nextIndex = index, index += 1) {
    const current = polygon[index];
    const previous = polygon[nextIndex];
    const intersects = ((current.y > point.y) !== (previous.y > point.y)) &&
      (point.x < ((previous.x - current.x) * (point.y - current.y) / (previous.y - current.y + Number.EPSILON)) + current.x);
    if (intersects) inside = !inside;
  }
  return inside;
}

function hitTest(point) {
  const img = imagePoint(point);
  for (let index = state.annotations.length - 1; index >= 0; index -= 1) {
    const annotation = state.annotations[index];
    // Fast bbox check (handles simple boxes and any annotations with x/y/width/height)
    const ax = Number(annotation.x) || 0;
    const ay = Number(annotation.y) || 0;
    const aw = Number(annotation.width) || 0;
    const ah = Number(annotation.height) || 0;
    const isPolygon = annotation.type === "polygon" || (annotation.points && annotation.points.length !== 4);
    if (!isPolygon) {
      if (img.x >= ax && img.x <= ax + aw && img.y >= ay && img.y <= ay + ah) return annotation.id;
    } else {
      const polygon = annotationPoints(annotation);
      if (pointInPolygon(img, polygon)) return annotation.id;
    }
  }
  return null;
}

function hitTestPoint(point, annotation) {
  if (!annotation || !annotation.points) return -1;
  const img = imagePoint(point);
  const threshold = 6 / imageBox.scale;
  for (let i = 0; i < annotation.points.length; i++) {
    const pt = annotation.points[i];
    if (Math.hypot(pt.x - img.x, pt.y - img.y) < threshold) {
      return i;
    }
  }
  return -1;
}

function hitTestLine(point, annotation) {
  if (!annotation || !annotation.points || annotation.points.length < 3) return -1;
  const img = imagePoint(point);
  const threshold = 6 / imageBox.scale;
  const pts = annotation.points;
  for (let i = 0; i < pts.length; i++) {
    const p1 = pts[i];
    const p2 = pts[(i + 1) % pts.length];
    
    const l2 = (p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2;
    if (l2 === 0) continue;
    
    let t = ((img.x - p1.x) * (p2.x - p1.x) + (img.y - p1.y) * (p2.y - p1.y)) / l2;
    t = Math.max(0, Math.min(1, t));
    
    const projX = p1.x + t * (p2.x - p1.x);
    const projY = p1.y + t * (p2.y - p1.y);
    
    if (Math.hypot(img.x - projX, img.y - projY) < threshold) {
      return i;
    }
  }
  return -1;
}

function selectedAnnotation() {
  return state.annotations.find((item) => item.id === state.selectedId) || null;
}

function replaceAnnotation(updated) {
  state.annotations = state.annotations.map((item) => (
    item.id === updated.id ? updated : item
  ));
}

function annotationChanged(before, after) {
  const beforePoints = annotationPoints(before);
  const afterPoints = annotationPoints(after);
  if (beforePoints.length !== afterPoints.length) return true;
  return beforePoints.some((point, index) => point.x !== afterPoints[index].x || point.y !== afterPoints[index].y);
}

function updateCanvasCursor(point) {
  if (!imageLoaded) {
    canvas.style.cursor = "default";
    return;
  }

  if (state.mode === "select") {
    if (state.selectedId) {
      const selected = state.annotations.find(a => a.id === state.selectedId);
      if (selected && hitTestPoint(point, selected) !== -1) {
        canvas.style.cursor = "crosshair";
        return;
      }
    }
    if (hitTest(point)) {
      canvas.style.cursor = "move";
      return;
    }
  }

  canvas.style.cursor = state.mode === "draw" ? "crosshair" : "default";
}

function deleteClass(classId) {
  snapshot();
  state.labels = state.labels.filter(l => l.id !== classId);
  // Also delete associated annotations
  state.annotations = state.annotations.filter(a => a.labelId !== classId);
  if (state.activeLabelId === classId) {
    state.activeLabelId = state.labels.length > 0 ? state.labels[0].id : null;
  }
  if (state.selectedId && !state.annotations.find(a => a.id === state.selectedId)) {
    state.selectedId = null;
    drag = null;
  }
  render();
  save();
}

function renderClasses() {
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

function renderAnnotations() {
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
        pendingCommentEditId = selected.id;
        const screenX = imageBox.x + selected.x * imageBox.scale;
        const screenY = imageBox.y + selected.y * imageBox.scale;
        commentOverlay.style.left = `${screenX + 15}px`;
        commentOverlay.style.top = `${screenY - 15}px`;
        commentOverlayInput.value = selected.text || "";
        commentOverlay.classList.remove("is-hidden");
        commentOverlayInput.focus();
      });
    } else {
      selectedInfo.textContent = `${labelDisplayName(labelById(selected.labelId))}, ${annotationPoints(selected).length} points`;
    }
  } else {
    selectedInfo.textContent = "None";
  }
}

function renderControls() {
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
  autoDetectButton.disabled = detectionBusy || !imageLoaded;
  const labelSpan = autoDetectButton.querySelector(".btn-label");
  if (labelSpan) {
    labelSpan.textContent = detectionBusy ? "Detecting..." : "Detect";
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
  const noData = !imageLoaded && state.annotations.length === 0;
  exportMenuButton.disabled = noData;
  labelStudioButton.disabled = labelStudioBusy || noData;
  emptyState.classList.toggle("is-hidden", imageLoaded);
}

if (logoutBtnApp) {
  logoutBtnApp.addEventListener("click", async () => {
    try {
      await fetch('/api/auth/logout', { method: 'POST' });
    } catch(e) {}
    localStorage.removeItem("dataset_username");
    localStorage.removeItem("image-annotation-mvp-v1");
    localStorage.removeItem("logged_in");
    window.location.href = "index.html";
  });
}

function render() {
  renderClasses();
  renderImageClasses();
  renderAnnotations();
  renderControls();
  drawAllLayers();
}

function renderImageClasses() {
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

function loadImageFromSource(src, name, { autoDetect = false } = {}) {
  imageElement = new Image();
  imageElement.onload = async () => {
    imageLoaded = true;
    emptyState.classList.add("is-hidden");
    imageName.textContent = name;
    imageSize.textContent = `${imageElement.naturalWidth} x ${imageElement.naturalHeight}`;
    state.image = { src, name, width: imageElement.naturalWidth, height: imageElement.naturalHeight };
    if (state.galleryIndex >= 0 && state.gallery[state.galleryIndex]) {
      state.gallery[state.galleryIndex].width = imageElement.naturalWidth;
      state.gallery[state.galleryIndex].height = imageElement.naturalHeight;
    }
    resizeCanvas();
    render();
    if (autoDetect) {
      await autoDetectObjects({ replace: true });
    } else {
      save();
    }
  };
  imageElement.src = src;
}

function loadLabelStudioSettings() {
  const saved = localStorage.getItem(labelStudioStorageKey);
  if (!saved) return;
  try {
    const parsed = JSON.parse(saved);
    labelStudioProxyInput.value = parsed.proxyUrl || "";
  } catch {
    labelStudioProxyInput.value = saved;
  }
}

function buildCocoExport() {
  if (state.galleryIndex >= 0 && state.gallery[state.galleryIndex]) {
    state.gallery[state.galleryIndex].annotations = [...state.annotations];
  }

  const categories = state.labels.map((label, index) => ({
    id: index + 1,
    name: label.name,
    supercategory: "none"
  }));

  const labelToCategoryId = {};
  categories.forEach(c => labelToCategoryId[c.name] = c.id);

  const images = [];
  const annotations = [];
  let annId = 1;

  const items = state.gallery.length > 0 ? state.gallery : [{
    name: state.image?.name || "image.jpg",
    width: state.image?.width || imageElement?.naturalWidth || 0,
    height: state.image?.height || imageElement?.naturalHeight || 0,
    annotations: state.annotations
  }];

  items.forEach((item, imgIndex) => {
    const image_id = imgIndex + 1;
    images.push({
      id: image_id,
      width: item.width || 0,
      height: item.height || 0,
      file_name: item.name
    });

    const grouped = {};
    const ungrouped = [];
    item.annotations.forEach(ann => {
      if (ann.type === "comment") return;
      if (ann.groupId) {
        if (!grouped[ann.groupId]) grouped[ann.groupId] = [];
        grouped[ann.groupId].push(ann);
      } else {
        ungrouped.push([ann]);
      }
    });

    const exportGroups = [...Object.values(grouped), ...ungrouped];

    exportGroups.forEach(group => {
      const baseAnn = group[0];
      const label = labelById(baseAnn.labelId);
      const category_id = labelToCategoryId[label.name] || 1;
      
      const segmentation = [];
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      
      group.forEach(ann => {
        const points = annotationPoints(ann);
        segmentation.push(points.flatMap(p => [round(p.x), round(p.y)]));
        points.forEach(p => {
          minX = Math.min(minX, p.x);
          minY = Math.min(minY, p.y);
          maxX = Math.max(maxX, p.x);
          maxY = Math.max(maxY, p.y);
        });
      });
      
      const bbox = [round(minX), round(minY), round(maxX - minX), round(maxY - minY)];
      const area = bbox[2] * bbox[3];

      annotations.push({
        id: annId++,
        image_id: image_id,
        category_id: category_id,
        segmentation: segmentation,
        area: round(area),
        bbox: bbox,
        iscrowd: 0,
        num_objects: group.length
      });
    });
  });

  return { images, categories, annotations };
}

async function sendToEndpoint() {
  const url = labelStudioProxyInput.value.trim();
  if (!url) {
    setStatus("URL required");
    return;
  }
  if (!imageLoaded) {
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

function annotationScreenPoints(annotation) {
  return annotationPoints(annotation).map((point) => ({
    x: round(imageBox.x + point.x * imageBox.scale),
    y: round(imageBox.y + point.y * imageBox.scale)
  }));
}

function getImageDimensions() {
  return {
    width: state.image?.width || imageElement?.naturalWidth || 0,
    height: state.image?.height || imageElement?.naturalHeight || 0
  };
}

function toExportValue(labelName) {
  return String(labelName || "object")
    .trim()
    .replace(/\s+/g, "");
}

function buildExportAnnotation(annotation, index) {
  const label = labelById(annotation.labelId);
  const labelName = exportLabelName(annotation, label);
  const points = annotationPoints(annotation);
  const flatPoints = points.flatMap((point) => [round(point.x), round(point.y)]);

  return {
    type: "polygon",
    title: labelDisplayName(label) || labelName,
    value: toExportValue(labelName),
    color: label.color,
    order: index + 1,
    attributes: [],
    points: flatPoints,
    rotation: 0,
    keypoints: [],
    confidenceScore: -1
  };
}

function buildExportTasks() {
  if (state.galleryIndex >= 0 && state.gallery[state.galleryIndex]) {
    state.gallery[state.galleryIndex].annotations = [...state.annotations];
  }

  const items = state.gallery.length > 0 ? state.gallery : [{
    name: state.image?.name || "image",
    width: state.image?.width || imageElement?.naturalWidth || 0,
    height: state.image?.height || imageElement?.naturalHeight || 0,
    annotations: state.annotations
  }];

  const createdAt = new Date().toISOString();
  
  return items.map(item => ({
    name: item.name,
    status: "completed",
    externalStatus: "registered",
    width: item.width || 0,
    height: item.height || 0,
    secondsToAnnotate: 0,
    annotations: item.annotations.filter(a => a.type !== "comment").map((annotation, index) => buildExportAnnotation(annotation, index)),
    relations: [],
    tags: [],
    metadatas: [],
    assignee: "",
    reviewer: "",
    approver: "",
    externalAssignee: "",
    externalReviewer: "",
    externalApprover: "",
    createdAt,
    updatedAt: createdAt,
    completedAt: createdAt
  }));
}

function exportJsonData() {
  try {
    const payload = buildCocoExport();

    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `dataset_annotations.json`;
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    window.setTimeout(() => {
      URL.revokeObjectURL(url);
      link.remove();
    }, 0);
  } catch (error) {
    console.error(error);
    window.alert(error.message || "Export failed.");
  }
}

function exportCsvData() {
  try {
    if (state.galleryIndex >= 0 && state.gallery[state.galleryIndex]) {
      state.gallery[state.galleryIndex].annotations = [...state.annotations];
    }
    const items = state.gallery.length > 0 ? state.gallery : [{
      name: state.image?.name || "image",
      width: state.image?.width || imageElement?.naturalWidth || 0,
      height: state.image?.height || imageElement?.naturalHeight || 0,
      annotations: state.annotations
    }];

    const header = ["image", "label", "type", "x", "y", "width", "height", "imgWidth", "imgHeight", "points"];
    const allRows = [];
    
    items.forEach(item => {
      const rows = item.annotations.filter(a => a.type !== "comment").map(annotation => {
        const label = labelById(annotation.labelId);
        const labelName = exportLabelName(annotation, label);
        const pts = annotationPoints(annotation);
        const isPolygon = annotation.points && annotation.points.length !== 4; 
        const type = isPolygon ? "polygon" : "box";
        const x = annotation.x;
        const y = annotation.y;
        const w = annotation.width;
        const h = annotation.height;
        const pointsStr = JSON.stringify(pts).replace(/"/g, '""');
        return [item.name, labelName, type, x, y, w, h, item.width || 0, item.height || 0, `"${pointsStr}"`].join(",");
      });
      allRows.push(...rows);
    });

    const csvContent = [header.join(","), ...allRows].join("\n");
    const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `dataset_annotations.csv`;
    link.style.display = "none";
    document.body.appendChild(link);
    link.click();
    window.setTimeout(() => {
      URL.revokeObjectURL(url);
      link.remove();
    }, 0);
  } catch (error) {
    console.error(error);
    window.alert(error.message || "Export failed.");
  }
}

function round(value) {
  return Math.round(value * 100) / 100;
}

function importData(file) {
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
        const currentImageWidth = imageLoaded ? (imageElement.naturalWidth || 1) : 1;
        const currentImageHeight = imageLoaded ? (imageElement.naturalHeight || 1) : 1;
        
        state.annotations = importedAnnotations.map((item) => {
          const labelName = item.title || item.label || item.detectedClass || labelById(item.labelId)?.name || "object";
          const label = ensureLabel(labelName);
          
          let parsedPoints = null;
          if (Array.isArray(item.points) && item.points.length >= 3) {
            if (typeof item.points[0] === 'number') {
              parsedPoints = [];
              for (let i = 0; i < item.points.length; i += 2) {
                parsedPoints.push({ x: Number(item.points[i]) || 0, y: Number(item.points[i+1]) || 0 });
              }
            } else {
              parsedPoints = item.points.map((point) => ({ x: Number(point.x) || 0, y: Number(point.y) || 0 }));
            }
          }
          
          let scaleX = 1;
          let scaleY = 1;
          if (item._imgWidth && item._imgHeight && imageLoaded) {
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

function importCsvData(file) {
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
      const currentImageWidth = imageLoaded ? (imageElement.naturalWidth || 1) : 1;
      const currentImageHeight = imageLoaded ? (imageElement.naturalHeight || 1) : 1;
      
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
          if (imgW && imgH && imageLoaded) {
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

function loadGallery(fileList) {
  const imageFiles = Array.from(fileList).filter(f => f.type.startsWith("image/"));
  if (!imageFiles.length) return;

  state.gallery.forEach(item => URL.revokeObjectURL(item.url));

  state.gallery = imageFiles.map(file => ({
    file: file,
    name: file.name,
    url: URL.createObjectURL(file),
    annotations: [],
    width: 0,
    height: 0
  }));
  
  if (state.gallery.length > 0) {
    switchImage(0);
    // Show validation modal after importing
    const teamValidationModal = document.getElementById('teamValidationModal');
    if (teamValidationModal) {
      teamValidationModal.classList.add('is-active');
      const nameInput = document.getElementById('teamValidationName');
      if (nameInput) {
        nameInput.value = localStorage.getItem('dataset_username') || '';
        nameInput.focus();
      }
    }
  }
}

function switchImage(index) {
  if (index < 0 || index >= state.gallery.length) return;
  if (state.galleryIndex >= 0 && state.gallery[state.galleryIndex]) {
    const prevTask = state.gallery[state.galleryIndex];
    prevTask.annotations = [...state.annotations];
    syncTaskTime(prevTask);
  }
  state.galleryIndex = index;
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
  galleryPosition.textContent = total > 0 ? `${current} / ${total}` : "0 / 0";
  prevImageButton.disabled = current <= 1;
  nextImageButton.disabled = current >= total || total === 0;
  if (clearGalleryButton) {
    clearGalleryButton.disabled = total === 0 && !imageLoaded;
  }
}

prevImageButton.addEventListener("click", () => switchImage(state.galleryIndex - 1));
nextImageButton.addEventListener("click", () => switchImage(state.galleryIndex + 1));

if (clearGalleryButton) {
  clearGalleryButton.addEventListener("click", () => {
    state.gallery.forEach(item => URL.revokeObjectURL(item.url));
    state.gallery = [];
    state.galleryIndex = -1;
    imageLoaded = false;
    imageElement = new Image();
    state.image = null;
    
    resetWorkspaceForNewImage();
    
    imageName.textContent = "None loaded";
    imageSize.textContent = "-";
    emptyState.classList.remove("is-hidden");
    
    updateGalleryUI();
    render();
    save();
    setStatus("Images cleared");
  });
}

imageInput.addEventListener("change", (event) => {
  loadGallery(event.target.files);
  imageInput.value = "";
});

drawMode.addEventListener("click", () => {
  state.mode = "draw";
  render();
});

selectMode.addEventListener("click", () => {
  if (drag?.type === "draw-polygon") {
    finalizePolygon();
  }
  state.mode = "select";
  render();
});

boxMode.addEventListener("click", () => {
  if (drag?.type === "draw-polygon") {
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
  if (drag?.type === "draw-polygon") {
    finalizePolygon();
  }
  state.mode = "draw";
  state.shape = "comment";
  render();
});

magicWandMode.addEventListener("click", () => {
  if (drag?.type === "draw-polygon") {
    finalizePolygon();
  }
  state.mode = "draw";
  state.shape = "magicWand";
  render();
});

commentOverlayInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    const text = commentOverlayInput.value;
    if (text && text.trim() !== "") {
      if (pendingCommentEditId) {
        const annotation = state.annotations.find(a => a.id === pendingCommentEditId);
        if (annotation) {
          snapshot();
          annotation.text = text.trim();
          render();
          save();
          setStatus("Comment updated");
        }
        pendingCommentEditId = null;
        commentOverlay.classList.add("is-hidden");
      } else if (pendingCommentPoint) {
        snapshot();
        const annotation = {
          id: generateUUID(),
          type: "comment",
          text: text.trim(),
          author: localStorage.getItem('dataset_username') || "Unknown",
          x: round(pendingCommentPoint.x),
          y: round(pendingCommentPoint.y),
          width: 20,
          height: 20,
          points: [
            { x: pendingCommentPoint.x - 10, y: pendingCommentPoint.y - 10 },
            { x: pendingCommentPoint.x + 10, y: pendingCommentPoint.y - 10 },
            { x: pendingCommentPoint.x + 10, y: pendingCommentPoint.y + 10 },
            { x: pendingCommentPoint.x - 10, y: pendingCommentPoint.y + 10 }
          ]
        };
        state.annotations.push(annotation);
        state.selectedId = annotation.id;
        pendingCommentPoint = null;
        commentOverlay.classList.add("is-hidden");
        render();
        save();
        setStatus("Comment added");
      }
    } else {
      // If empty, treat as cancel
      pendingCommentPoint = null;
      pendingCommentEditId = null;
      commentOverlay.classList.add("is-hidden");
      render();
    }
  } else if (e.key === "Escape") {
    e.preventDefault();
    pendingCommentPoint = null;
    pendingCommentEditId = null;
    commentOverlay.classList.add("is-hidden");
    render();
  }
});

undoButton.addEventListener("click", () => {
  const previous = state.history.pop();
  if (!previous) return;
  const restored = JSON.parse(previous);
  state.labels = restored.labels;
  state.annotations = restored.annotations;
  state.selectedId = restored.selectedId;
  // Clear polygon draw state if the annotation was undone
  if (drag?.type === "draw-polygon") {
    const exists = state.annotations.some((item) => item.id === drag.annotationId);
    if (!exists) drag = null;
  }
  render();
  save();
});

deleteButton.addEventListener("click", () => {
  deleteSelected();
});

function deleteSelected() {
  if (state.selectedIds.size === 0) return;
  snapshot();
  // If deleting the polygon being drawn, clean up drag state
  if (drag?.type === "draw-polygon" && state.selectedIds.has(drag.annotationId)) {
    drag = null;
  }
  state.annotations = state.annotations.filter((item) => !state.selectedIds.has(item.id));
  state.selectedIds.clear();
  state.selectedId = null;
  selectedLineIndex = -1;
  hoveredLineIndex = -1;
  hoveredPointIndex = -1;
  render();
  save();
}

const groupButton = document.querySelector("#groupButton");
if (groupButton) {
  groupButton.addEventListener("click", () => {
    groupSelectedAnnotations();
  });
}

function groupSelectedAnnotations() {
  if (state.selectedIds.size <= 1) return;
  
  snapshot();
  
  const selectedList = state.annotations.filter(a => state.selectedIds.has(a.id) && a.type !== "comment");
  if (selectedList.length <= 1) {
    state.history.pop();
    return;
  }
  
  const baseAnnotation = selectedList[0];
  const groupId = generateUUID();
  
  state.annotations.forEach(a => {
    if (state.selectedIds.has(a.id) && a.type !== "comment") {
      a.groupId = groupId;
      a.labelId = baseAnnotation.labelId;
    }
  });
  
  render();
  save();
  setStatus("Grouped annotations");
}

const ungroupButton = document.querySelector("#ungroupButton");
if (ungroupButton) {
  ungroupButton.addEventListener("click", () => {
    snapshot();
    let ungrouped = false;
    state.annotations.forEach(a => {
      if (state.selectedIds.has(a.id) && a.groupId) {
        delete a.groupId;
        ungrouped = true;
      }
    });
    if (ungrouped) {
      render();
      save();
      setStatus("Ungrouped annotations");
    } else {
      state.history.pop();
    }
  });
}

clearButton.addEventListener("click", () => {
  if (!state.annotations.length) return;
  snapshot();
  state.annotations = [];
  state.selectedId = null;
  drag = null;
  render();
  save();
  setStatus("All annotations cleared");
});

importMenuButton.addEventListener("click", (e) => {
  e.stopPropagation();
  importDropdown.classList.toggle("show");
});
importJsonButton.addEventListener("click", () => {
  importDropdown.classList.remove("show");
  importJsonInput.click();
});
importCsvButton.addEventListener("click", () => {
  importDropdown.classList.remove("show");
  importCsvInput.click();
});
exportMenuButton.addEventListener("click", (e) => {
  e.stopPropagation();
  exportDropdown.classList.toggle("show");
});
if (aiSettingsMenuButton) {
  aiSettingsMenuButton.addEventListener("click", (e) => {
    e.stopPropagation();
    aiSettingsDropdownContainer.classList.toggle("show");
  });
}
document.addEventListener("click", (e) => {
  if (!exportDropdown.contains(e.target)) {
    exportDropdown.classList.remove("show");
  }
  if (!importDropdown.contains(e.target)) {
    importDropdown.classList.remove("show");
  }
  if (aiSettingsDropdownContainer && !aiSettingsDropdownContainer.contains(e.target)) {
    aiSettingsDropdownContainer.classList.remove("show");
  }
});

exportJsonButton.addEventListener("click", (e) => {
  exportDropdown.classList.remove("show");
  exportJsonData();
});
exportCsvButton.addEventListener("click", (e) => {
  exportDropdown.classList.remove("show");
  exportCsvData();
});
autoDetectButton.addEventListener("click", () => autoDetectObjects({ replace: true }));
if (autoTagButton) {
  autoTagButton.addEventListener("click", () => autoTagObjects());
}

addClassButton.addEventListener("click", () => {
  newClassForm.classList.toggle("is-hidden");
  if (!newClassForm.classList.contains("is-hidden")) {
    newClassName.focus();
  }
});

newClassForm.addEventListener("submit", (event) => {
  event.preventDefault();
  const name = newClassName.value.trim();
  const color = newClassColor.value;
  if (!name) return;

  snapshot();
  const label = ensureLabel(name, color);
  state.activeLabelId = label.id;
  newClassName.value = "";
  newClassForm.classList.add("is-hidden");
  render();
  save();
  setStatus(`Added class: ${name}`);
});

const newClassObjectsForm = document.getElementById("newClassObjectsForm");
const newClassNameObj = document.getElementById("newClassNameObj");
const newClassColorObj = document.getElementById("newClassColorObj");
if (newClassObjectsForm) {
  newClassObjectsForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const name = newClassNameObj.value.trim();
    const color = newClassColorObj.value;
    if (!name) return;

    snapshot();
    const label = ensureLabel(name, color);
    state.activeLabelId = label.id;
    newClassNameObj.value = "";
    render();
    save();
    setStatus(`Added class: ${name}`);
  });
}

const importClassesBtn = document.getElementById("importClassesBtn");
const exportClassesBtn = document.getElementById("exportClassesBtn");
const importClassesInput = document.getElementById("importClassesInput");

if (importClassesBtn && importClassesInput) {
  importClassesBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    importClassesInput.click();
  });

  importClassesInput.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const importedLabels = JSON.parse(e.target.result);
        if (!Array.isArray(importedLabels)) {
          alert("Invalid classes file format. Expected a JSON array.");
          return;
        }
        let count = 0;
        for (const lbl of importedLabels) {
          const name = lbl.title || lbl.name;
          if (name) {
            ensureLabel(name, lbl.color || null);
            count++;
          }
        }
        render();
        save();
        setStatus(`Imported ${count} classes.`);
      } catch (err) {
        console.error(err);
        alert("Error parsing JSON file.");
      }
    };
    reader.readAsText(file);
    importClassesInput.value = ""; // reset
  });
}

if (exportClassesBtn) {
  exportClassesBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!state.labels || state.labels.length === 0) {
      alert("No classes to export.");
      return;
    }
    // Create a clean array of classes for export matching the requested schema
    const exportData = state.labels.map((l, index) => ({
      type: "polygon",
      title: l.name,
      value: l.name.replace(/[^a-zA-Z0-9]/g, ''),
      color: l.color,
      order: index + 1,
      useBBox: false,
      useRotation: false,
      defaultWidth: 0,
      defaultHeight: 0,
      defaultLength: 0,
      minWidth: 0,
      minHeight: 0,
      isAllowMinAtLeastOne: false,
      minLength: 0,
      maxWidth: 0,
      maxHeight: 0,
      isAllowMaxAtLeastOne: false,
      maxLength: 0,
      verticalRatio: null,
      horizontalRatio: null,
      maxAreaCount: null,
      minArea: null,
      maxInstanceCount: 0,
      vertex: 0,
      isOverlapFrameSelect: false,
      isOutsideAnnotationFrameSelect: false,
      isUniformSizeAcrossFrames: false,
      isFrameGapRestricted: false,
      lockRotationX: false,
      lockRotationY: false,
      lockRotationZ: false,
      attributes: [],
      keypoints: []
    }));
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(exportData, null, 2));
    const dlAnchorElem = document.createElement('a');
    dlAnchorElem.setAttribute("href", dataStr);
    dlAnchorElem.setAttribute("download", "classes_export.json");
    document.body.appendChild(dlAnchorElem);
    dlAnchorElem.click();
    document.body.removeChild(dlAnchorElem);
  });
}

const importObjectsBtn = document.getElementById("importObjectsBtn");
const exportObjectsBtn = document.getElementById("exportObjectsBtn");
const importObjectsInput = document.getElementById("importObjectsInput");

if (importObjectsBtn && importObjectsInput) {
  importObjectsBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    importObjectsInput.click();
  });

  importObjectsInput.addEventListener("change", (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => {
      try {
        const importedData = JSON.parse(e.target.result);
        let importedAnnotations = [];
        
        // Detect COCO Format
        if (importedData.images && importedData.annotations && importedData.categories) {
          // Map COCO category id (int) to our labelId (uuid)
          const catIdToLabelId = {};
          for (const cat of importedData.categories) {
            const existing = ensureLabel(cat.title || cat.name, cat.color);
            catIdToLabelId[cat.id] = existing.id;
          }
          
          for (const ann of importedData.annotations) {
            const labelId = catIdToLabelId[ann.category_id];
            if (!labelId) continue;
            
            let points = [];
            if (ann.segmentation && ann.segmentation.length > 0 && ann.segmentation[0].length > 0) {
              const seg = ann.segmentation[0];
              for (let i = 0; i < seg.length; i += 2) {
                points.push({ x: seg[i], y: seg[i+1] });
              }
            } else if (ann.bbox && ann.bbox.length === 4) {
              // Convert bbox to polygon
              const [x, y, w, h] = ann.bbox;
              points = [
                {x: x, y: y}, {x: x + w, y: y}, {x: x + w, y: y + h}, {x: x, y: y + h}
              ];
            }
            
            if (points.length > 0) {
              const bounds = { x: Math.min(...points.map(p=>p.x)), y: Math.min(...points.map(p=>p.y)) };
              bounds.width = Math.max(...points.map(p=>p.x)) - bounds.x;
              bounds.height = Math.max(...points.map(p=>p.y)) - bounds.y;
              
              importedAnnotations.push({
                id: generateUUID(),
                labelId: labelId,
                points: points,
                x: bounds.x,
                y: bounds.y,
                width: bounds.width,
                height: bounds.height
              });
            }
          }
        } else if (Array.isArray(importedData)) {
          // Detect if they accidentally imported the classes array into the objects panel
          if (importedData.length > 0 && (importedData[0].title || importedData[0].name) && !importedData[0].points && !importedData[0].labelId) {
            let count = 0;
            for (const lbl of importedData) {
              const name = lbl.title || lbl.name;
              if (name) {
                ensureLabel(name, lbl.color || null);
                count++;
              }
            }
            render();
            save();
            setStatus(`Imported ${count} classes.`);
            return;
          }
          // Legacy format
          importedAnnotations = importedData;
        } else {
          alert("Invalid objects file format. Expected COCO JSON or a JSON array.");
          return;
        }
        
        state.annotations = [...state.annotations, ...importedAnnotations];
        render();
        save();
        setStatus(`Imported ${importedAnnotations.length} objects.`);
      } catch (err) {
        console.error(err);
        alert("Failed to parse objects JSON.");
      }
    };
    reader.readAsText(file);
    importObjectsInput.value = ""; // reset
  });
}

if (exportObjectsBtn) {
  exportObjectsBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!state.annotations || state.annotations.length === 0) {
      alert("No objects to export.");
      return;
    }
    
    // Generate COCO format
    const coco = {
      images: [
        { id: 1, width: imageElement?.naturalWidth || 800, height: imageElement?.naturalHeight || 600, file_name: state.imageName || "image.jpg" }
      ],
      categories: state.labels.map((l, index) => ({
        id: index + 1,
        name: l.name,
        type: "polygon",
        title: l.name,
        value: l.name.replace(/[^a-zA-Z0-9]/g, ''),
        color: l.color,
        order: index + 1,
        useBBox: false,
        useRotation: false,
        defaultWidth: 0,
        defaultHeight: 0,
        defaultLength: 0,
        minWidth: 0,
        minHeight: 0,
        isAllowMinAtLeastOne: false,
        minLength: 0,
        maxWidth: 0,
        maxHeight: 0,
        isAllowMaxAtLeastOne: false,
        maxLength: 0,
        verticalRatio: null,
        horizontalRatio: null,
        maxAreaCount: null,
        minArea: null,
        maxInstanceCount: 0,
        vertex: 0,
        isOverlapFrameSelect: false,
        isOutsideAnnotationFrameSelect: false,
        isUniformSizeAcrossFrames: false,
        isFrameGapRestricted: false,
        lockRotationX: false,
        lockRotationY: false,
        lockRotationZ: false,
        attributes: [],
        keypoints: []
      })),
      annotations: []
    };
    
    // Map our labelId (uuid) to COCO category id (int)
    const labelIdToCatId = {};
    state.labels.forEach((l, index) => { labelIdToCatId[l.id] = index + 1; });
    
    state.annotations.forEach((ann, index) => {
      const catId = labelIdToCatId[ann.labelId] || 1;
      let segmentation = [];
      let bbox = [ann.x, ann.y, ann.width, ann.height];
      let area = ann.width * ann.height; // Rough estimate
      
      if (ann.points && ann.points.length > 0) {
        segmentation = [ ann.points.flatMap(p => [p.x, p.y]) ];
        // Calculate precise area of polygon using shoelace formula
        let polyArea = 0;
        for (let i = 0; i < ann.points.length; i++) {
          let j = (i + 1) % ann.points.length;
          polyArea += ann.points[i].x * ann.points[j].y;
          polyArea -= ann.points[j].x * ann.points[i].y;
        }
        area = Math.abs(polyArea / 2);
      }
      
      coco.annotations.push({
        id: index + 1,
        image_id: 1,
        category_id: catId,
        segmentation: segmentation,
        bbox: bbox,
        area: area,
        iscrowd: 0
      });
    });

    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(coco, null, 2));
    const dlAnchorElem = document.createElement('a');
    dlAnchorElem.setAttribute("href", dataStr);
    dlAnchorElem.setAttribute("download", "objects_export_coco.json");
    document.body.appendChild(dlAnchorElem);
    dlAnchorElem.click();
    document.body.removeChild(dlAnchorElem);
  });
}

labelStudioButton.addEventListener("click", sendToEndpoint);

labelStudioProxyInput.addEventListener("change", () => {
  localStorage.setItem(labelStudioStorageKey, labelStudioProxyInput.value.trim());
});


importJsonInput.addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  if (file) importData(file);
  importJsonInput.value = "";
});

importCsvInput.addEventListener("change", (event) => {
  const file = event.target.files?.[0];
  if (file) importCsvData(file);
  importCsvInput.value = "";
});

function finalizePolygon() {
  if (drag?.type !== "draw-polygon") return;
  const annotation = state.annotations.find((item) => item.id === drag.annotationId);
  drag = null;
  if (!annotation || (annotation.points || []).length < 3) {
    // Remove incomplete polygon
    if (annotation) {
      state.annotations = state.annotations.filter((item) => item.id !== annotation.id);
      state.selectedId = null;
    }
    render();
    save();
    return;
  }
  updateAnnotationBounds(annotation);
  render();
  save();
  setStatus("Annotation created");
}

function setZoom(newZoom, mouseX, mouseY) {
  if (!imageLoaded) return;
  const oldZoom = viewZoom;
  viewZoom = Math.max(0.01, Math.min(100, newZoom));
  
  const rect = canvas.getBoundingClientRect();
  const cx = mouseX !== undefined ? mouseX : rect.width / 2;
  const cy = mouseY !== undefined ? mouseY : rect.height / 2;
  
  const baseScale = Math.min(rect.width / imageElement.naturalWidth, rect.height / imageElement.naturalHeight);
  const oldScale = baseScale * oldZoom;
  const newScale = baseScale * viewZoom;
  
  const imgX = (cx - imageBox.x) / oldScale;
  const imgY = (cy - imageBox.y) / oldScale;
  
  const newWidth = imageElement.naturalWidth * newScale;
  const newHeight = imageElement.naturalHeight * newScale;
  
  viewPan.x = cx - (rect.width - newWidth) / 2 - imgX * newScale;
  viewPan.y = cy - (rect.height - newHeight) / 2 - imgY * newScale;
  
  drawAllLayers();
}

canvas.addEventListener("wheel", (event) => {
  if (!imageLoaded) return;
  event.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mouseX = event.clientX - rect.left;
  const mouseY = event.clientY - rect.top;
  const zoomFactor = event.deltaY < 0 ? 1.1 : 1 / 1.1;
  setZoom(viewZoom * zoomFactor, mouseX, mouseY);
}, { passive: false });

canvas.addEventListener("contextmenu", (event) => {
  if (state.selectedId) {
    event.preventDefault();
  }
});

canvas.addEventListener("pointerdown", (event) => {
  if (!imageLoaded) return;
  canvas.setPointerCapture(event.pointerId);
  
  if (event.button === 1 || (event.button === 0 && event.shiftKey && event.altKey)) {
    event.preventDefault();
    isPanning = true;
    panStart = { x: event.clientX, y: event.clientY, panX: viewPan.x, panY: viewPan.y };
    canvas.style.cursor = "grabbing";
    return;
  }
  
  const point = canvasPoint(event);

  // Left-click on a polygon edge to add a vertex
  if (state.selectedId && event.button === 0 && !event.altKey) {
    const selected = state.annotations.find(a => a.id === state.selectedId);
    if (selected && selected.points && selected.points.length >= 3) {
      // Prioritize point hit test so we don't accidentally split a line when clicking a point
      const ptIndex = hitTestPoint(point, selected);
      if (ptIndex === -1) {
        const lnIndex = hitTestLine(point, selected);
        if (lnIndex !== -1) {
          snapshot();
          // Insert new point exactly where clicked
          const img = imagePoint(point);
          const newPoint = { x: round(img.x), y: round(img.y) };
          selected.points.splice(lnIndex + 1, 0, newPoint);
          updateAnnotationBounds(selected);
          
          drag = {
            type: "move-point",
            annotationId: selected.id,
            pointIndex: lnIndex + 1
          };
          render();
          save();
          setStatus("Vertex added");
          return;
        }
      }
    }
  }
  if (state.selectedId && (event.altKey || event.button === 2)) {
    const selected = state.annotations.find(a => a.id === state.selectedId);
    if (selected && selected.points && selected.points.length > 3) {
      const ptIndex = hitTestPoint(point, selected);
      if (ptIndex !== -1) {
        snapshot();
        selected.points.splice(ptIndex, 1);
        updateAnnotationBounds(selected);
        render();
        save();
        return;
      }
      const lnIndex = hitTestLine(point, selected);
      if (lnIndex !== -1) {
        snapshot();
        const nextIndex = (lnIndex + 1) % selected.points.length;
        const toRemove = [lnIndex, nextIndex].sort((a,b)=>b-a);
        selected.points.splice(toRemove[0], 1);
        selected.points.splice(toRemove[1], 1);
        selectedLineIndex = -1;
        hoveredLineIndex = -1;
        updateAnnotationBounds(selected);
        render();
        save();
        return;
      }
    }
  }

  if (state.selectedId) {
    const selected = state.annotations.find(a => a.id === state.selectedId);
    if (selected && selected.points && selected.points.length >= 3) {
      const ptIndex = hitTestPoint(point, selected);
      if (ptIndex !== -1) {
        snapshot();
        drag = {
          type: "move-point",
          annotationId: selected.id,
          pointIndex: ptIndex
        };
        return;
      }
    }
  }

  // In draw mode, skip hit-testing – clicks should create shapes, not select existing ones
  if (state.mode !== "draw") {
    const hitId = hitTest(point);
    if (hitId) {
      if (event.shiftKey) {
        const hitAnnotation = state.annotations.find(a => a.id === hitId);
        const toSelect = hitAnnotation.groupId ? state.annotations.filter(a => a.groupId === hitAnnotation.groupId).map(a => a.id) : [hitId];
        if (state.selectedIds.has(hitId)) {
          toSelect.forEach(id => state.selectedIds.delete(id));
        } else {
          toSelect.forEach(id => state.selectedIds.add(id));
        }
        state._selectedId = state.selectedIds.size > 0 ? Array.from(state.selectedIds)[0] : null;
      } else {
        state.selectedIds.clear();
        const hitAnnotation = state.annotations.find(a => a.id === hitId);
        if (hitAnnotation && hitAnnotation.groupId) {
          state.annotations.forEach(a => {
            if (a.groupId === hitAnnotation.groupId) state.selectedIds.add(a.id);
          });
        } else {
          state.selectedIds.add(hitId);
        }
        state.selectedId = hitId;
      }
      selectedLineIndex = -1;
      hoveredLineIndex = -1;
      state.mode = "select";
      snapshot();
      drag = {
        type: "move",
        start: imagePoint(point),
        originals: state.annotations.filter(a => state.selectedIds.has(a.id)).map(a => JSON.parse(JSON.stringify(a)))
      };
      render();
      return;
    }
  }

  if (state.mode === "select") {
    state.selectedId = null;
    state.selectedIds.clear();
    selectedLineIndex = -1;
    hoveredLineIndex = -1;
    drag = null;
    render();
    return;
  }

  if (state.mode === "draw") {
    const pointInImage = imagePoint(point);
    
    if (state.shape === "comment") {
      pendingCommentPoint = pointInImage;
      render();
      
      const screenPoint = {
        x: imageBox.x + pendingCommentPoint.x * imageBox.scale,
        y: imageBox.y + pendingCommentPoint.y * imageBox.scale
      };
      
      commentOverlay.style.left = `${screenPoint.x + 15}px`;
      commentOverlay.style.top = `${screenPoint.y - 15}px`;
      commentOverlay.classList.remove("is-hidden");
      commentOverlayInput.value = "";
      commentOverlayInput.focus();
      return;
    }

    if (state.shape === "polygon") {
      if (drag?.type !== "draw-polygon") {
        // First point – create annotation immediately so it appears in the Objects panel
        snapshot();
        if (!state.activeLabelId) {
          const defaultLabel = ensureLabel("object");
          state.activeLabelId = defaultLabel.id;
        }
        const annotation = {
          id: generateUUID(),
          labelId: state.activeLabelId,
          points: [{ x: round(pointInImage.x), y: round(pointInImage.y) }]
        };
        updateAnnotationBounds(annotation);
        state.annotations.push(annotation);
        state.selectedId = annotation.id;
        drag = { type: "draw-polygon", annotationId: annotation.id };
      } else {
        // Subsequent points – add to the live annotation
        const annotation = state.annotations.find((item) => item.id === drag.annotationId);
        if (!annotation) { drag = null; render(); return; }
        const pts = annotation.points || [];
        const firstPoint = pts[0];
        // Close polygon when clicking near the first point
        if (firstPoint && pts.length >= 3) {
          const screenFirst = {
            x: imageBox.x + firstPoint.x * imageBox.scale,
            y: imageBox.y + firstPoint.y * imageBox.scale
          };
          if (Math.hypot(screenFirst.x - point.x, screenFirst.y - point.y) < closeThreshold) {
            finalizePolygon();
            return;
          }
        }
        const lastPoint = pts[pts.length - 1];
        if (!lastPoint || Math.hypot(lastPoint.x - pointInImage.x, lastPoint.y - pointInImage.y) > 1) {
          annotation.points.push({ x: round(pointInImage.x), y: round(pointInImage.y) });
          updateAnnotationBounds(annotation);
        }
      }
      render();
      save();
      return;
    } else {
      if (!state.activeLabelId) {
        const defaultLabel = ensureLabel("object");
        state.activeLabelId = defaultLabel.id;
      }
      drag = {
        type: "draw",
        draft: {
          id: "draft",
          labelId: state.activeLabelId,
          points: [
            { x: pointInImage.x, y: pointInImage.y },
            { x: pointInImage.x + 1, y: pointInImage.y },
            { x: pointInImage.x + 1, y: pointInImage.y + 1 },
            { x: pointInImage.x, y: pointInImage.y + 1 }
          ],
          x: pointInImage.x,
          y: pointInImage.y,
          width: 1,
          height: 1
        }
      };
    }

    draw();
  }
});

canvas.addEventListener("pointermove", (event) => {
  if (isPanning) {
    const dx = event.clientX - panStart.x;
    const dy = event.clientY - panStart.y;
    viewPan.x = panStart.panX + dx;
    viewPan.y = panStart.panY + dy;
    drawAllLayers();
    return;
  }
  const point = canvasPoint(event);
  updateCanvasCursor(point);

  // Detect line hover on selected polygon (even when no drag)
  if (state.selectedId && !drag) {
    const selected = state.annotations.find(a => a.id === state.selectedId);
    if (selected && selected.points && selected.points.length >= 3) {
      const ptIndex = hitTestPoint(point, selected);
      if (ptIndex !== -1) {
        if (hoveredLineIndex !== -1) {
          hoveredLineIndex = -1;
          draw();
        }
        if (hoveredPointIndex !== ptIndex) {
          hoveredPointIndex = ptIndex;
          draw();
        }
        canvas.style.cursor = "crosshair";
      } else {
        if (hoveredPointIndex !== -1) {
          hoveredPointIndex = -1;
          draw();
        }
        const lnIndex = hitTestLine(point, selected);
        if (lnIndex !== hoveredLineIndex) {
          hoveredLineIndex = lnIndex;
          draw();
        }
        if (lnIndex !== -1) {
          canvas.style.cursor = "pointer";
        }
      }
    } else if (hoveredLineIndex !== -1) {
      hoveredLineIndex = -1;
      draw();
    }
  }

  if (!drag) return;

  const end = imagePoint(point);
  if (drag.type === "draw-polygon") {
    drag.preview = end;
    drag.previewCanvas = point;
    draw();
  } else if (drag.type === "draw" && state.mode === "draw") {
    const start = drag.draft.points?.[0] || { x: end.x, y: end.y };
    const x1 = Math.min(start.x, end.x);
    const y1 = Math.min(start.y, end.y);
    const x2 = Math.max(start.x, end.x);
    const y2 = Math.max(start.y, end.y);
    drag.draft.points = [
      { x: x1, y: y1 },
      { x: x2, y: y1 },
      { x: x2, y: y2 },
      { x: x1, y: y2 }
    ];
    drag.draft.x = x1;
    drag.draft.y = y1;
    drag.draft.width = Math.max(1, x2 - x1);
    drag.draft.height = Math.max(1, y2 - y1);
    draw();
  }

  if (drag.type === "move") {
    drag.originals.forEach(original => {
      const updated = {
        ...original,
        points: (original.points || annotationPoints(original)).map((item) => ({
          x: round(clamp(item.x + (end.x - drag.start.x), 0, imageElement.naturalWidth)),
          y: round(clamp(item.y + (end.y - drag.start.y), 0, imageElement.naturalHeight))
        }))
      };
      updateAnnotationBounds(updated);
      replaceAnnotation(updated);
    });
    render();
  }

  if (drag.type === "move-point") {
    const annotation = state.annotations.find((item) => item.id === drag.annotationId);
    if (annotation) {
      annotation.points[drag.pointIndex] = {
        x: round(clamp(end.x, 0, imageElement.naturalWidth)),
        y: round(clamp(end.y, 0, imageElement.naturalHeight))
      };
      updateAnnotationBounds(annotation);
      render();
    }
  }
});

canvas.addEventListener("dblclick", (event) => {
  // Polygon finalizing via double-click has been removed as per user request
  
  if (state.selectedId) {
    const point = canvasPoint(event);
    const selected = state.annotations.find(a => a.id === state.selectedId);
    if (selected && selected.points && selected.points.length > 3) {
      const ptIndex = hitTestPoint(point, selected);
      if (ptIndex !== -1) {
        snapshot();
        selected.points.splice(ptIndex, 1);
        updateAnnotationBounds(selected);
        render();
        save();
        setStatus("Vertex removed");
        return;
      }
    }
  }
});

canvas.addEventListener("pointerup", (e) => {
  if (isPanning) {
    isPanning = false;
    canvas.style.cursor = "default";
    return;
  }
  if (drag?.type === "move-point") {
    drag = null;
    save();
    return;
  }

  if (drag?.type === "move") {
    let changed = false;
    drag.originals.forEach(original => {
      const updated = state.annotations.find(a => a.id === original.id);
      if (updated && annotationChanged(original, updated)) {
        changed = true;
      }
    });
    drag = null;
    if (changed) {
      save();
    } else {
      state.history.pop();
    }
    render();
    return;
  }

  if (drag?.draft && drag.type === "draw" && state.mode === "draw") {
    if (state.shape === "box") {
      const start = drag.draft.points?.[0] || { x: 0, y: 0 };
      const end = drag.draft.points?.[2] || start;
      const x1 = Math.min(start.x, end.x);
      const y1 = Math.min(start.y, end.y);
      const x2 = Math.max(start.x, end.x);
      const y2 = Math.max(start.y, end.y);
      drag.draft.points = [
        { x: x1, y: y1 },
        { x: x2, y: y1 },
        { x: x2, y: y2 },
        { x: x1, y: y2 }
      ];
      drag.draft.x = x1;
      drag.draft.y = y1;
      drag.draft.width = Math.max(1, x2 - x1);
      drag.draft.height = Math.max(1, y2 - y1);

      snapshot();
      const annotation = {
        id: generateUUID(),
        labelId: drag.draft.labelId,
        points: drag.draft.points.map((point) => ({ x: round(point.x), y: round(point.y) }))
      };
      updateAnnotationBounds(annotation);
      state.annotations.push(annotation);
      state.selectedId = annotation.id;
      drag = null;
      render();
      save();
      setStatus("Annotation created");
      return;
    } else if (state.shape === "magicWand") {
      const start = drag.draft.points?.[0] || { x: 0, y: 0 };
      const end = drag.draft.points?.[2] || start;
      const x1 = Math.min(start.x, end.x);
      const y1 = Math.min(start.y, end.y);
      const x2 = Math.max(start.x, end.x);
      const y2 = Math.max(start.y, end.y);
      
      drag = null;
      render();
      
      const isShift = e.shiftKey;
      const isAlt = e.altKey;
      
      if (Math.abs(x2 - x1) < 3 && Math.abs(y2 - y1) < 3) {
        performMagicWandSegmentation({ x: start.x, y: start.y }, null, isShift, isAlt);
      } else {
        performMagicWandSegmentation({ x: start.x, y: start.y }, [x1, y1, x2, y2], isShift, isAlt);
      }
      return;
    }
    draw();
  }
});

canvas.addEventListener("pointerleave", () => {
  if (isPanning) {
    isPanning = false;
  }
  if (!drag) canvas.style.cursor = "default";
  if (hoveredLineIndex !== -1) {
    hoveredLineIndex = -1;
    draw();
  }
});

canvas.addEventListener("pointercancel", () => {
  if (isPanning) {
    isPanning = false;
    canvas.style.cursor = "default";
    return;
  }
  if (drag?.type === "draw-polygon") {
    const annotation = state.annotations.find((item) => item.id === drag.annotationId);
    if (annotation && (annotation.points || []).length < 3) {
      state.annotations = state.annotations.filter((item) => item.id !== annotation.id);
    }
  }
  drag = null;
  render();
});

window.addEventListener("keydown", (event) => {
  const target = event.target;
  const isTyping = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
  if (isTyping) return;

  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
    event.preventDefault();
    undoButton.click();
    return;
  }

  if (event.key === "Delete" || event.key === "Backspace") {
    event.preventDefault();
    if (state.selectedId) {
      // If hovering over a vertex, delete just that vertex
      if (hoveredPointIndex !== -1) {
        const selected = state.annotations.find(a => a.id === state.selectedId);
        if (selected && selected.points && selected.points.length > 3) {
          snapshot();
          selected.points.splice(hoveredPointIndex, 1);
          hoveredPointIndex = -1;
          updateAnnotationBounds(selected);
          render();
          save();
          setStatus("Vertex deleted");
          return;
        }
      }
      // If a line segment is selected on a polygon, delete just that segment
      if (selectedLineIndex !== -1) {
        const selected = state.annotations.find(a => a.id === state.selectedId);
        if (selected && selected.points && selected.points.length > 3) {
          snapshot();
          const nextIndex = (selectedLineIndex + 1) % selected.points.length;
          const toRemove = [selectedLineIndex, nextIndex].sort((a,b)=>b-a);
          selected.points.splice(toRemove[0], 1);
          selected.points.splice(toRemove[1], 1);
          selectedLineIndex = -1;
          hoveredLineIndex = -1;
          updateAnnotationBounds(selected);
          render();
          save();
          setStatus("Line segment deleted");
          return;
        }
      }
    }
    deleteSelected();
    return;
  }

  if (event.key === "Escape") {
    // If drawing a polygon, cancel and remove the incomplete annotation
    if (drag?.type === "draw-polygon") {
      const annotation = state.annotations.find((item) => item.id === drag.annotationId);
      if (annotation) {
        state.annotations = state.annotations.filter((item) => item.id !== annotation.id);
      }
    }
    state.selectedId = null;
    state.selectedIds.clear();
    selectedLineIndex = -1;
    hoveredLineIndex = -1;
    drag = null;
    render();
    return;
  }

  if (event.key.toLowerCase() === "g") {
    groupSelectedAnnotations();
    return;
  }

  if (event.key.toLowerCase() === "d") {
    state.mode = "draw";
    render();
  }

  if (event.key.toLowerCase() === "s") {
    state.mode = "select";
    render();
  }
});

stageWrap.addEventListener("dragover", (event) => {
  event.preventDefault();
});

stageWrap.addEventListener("drop", (event) => {
  event.preventDefault();
  loadGallery(event.dataTransfer.files);
});

window.addEventListener("resize", resizeCanvas);

window.addEventListener("storage", (e) => {
  if (e.key === storageKey) {
    loadSaved();
    render();
  }
});
loadLabelStudioSettings();
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

// --- Session Timer Logic ---
const timerToggleBtn = document.getElementById("timerToggleBtn");
const sessionTimerDisplay = document.getElementById("sessionTimerDisplay");
const totalTimeLoggedDisplay = document.getElementById("totalTimeLogged");
const timerResetBtn = document.getElementById("timerResetBtn");
const timerStopBtn = document.getElementById("timerStopBtn");

let timerInterval = null;
let sessionSeconds = 0;
let taskSessionSeconds = 0;
let currentUserForTimer = localStorage.getItem('dataset_username') || 'Unknown';
let totalSeconds = 0;
let lastSyncedTotalSeconds = 0;
let isTimerRunning = false;

async function syncTaskTime(task) {
  if (task && task.id) {
    const timeDelta = taskSessionSeconds;
    taskSessionSeconds = 0;
    apiFetch('/api/tasks', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        id: task.id,
        time_spent_delta: timeDelta,
        status: task.status || 'In Progress',
        assignee: localStorage.getItem('dataset_username') || 'Unknown',
        annotations: JSON.stringify(task.annotations || []),
        updated_at: task.updated_at
      })
    })
    .then(async res => {
      if (res.status === 409) {
        const errorMsg = await res.json();
        alert(`Conflict: ${errorMsg.detail}`);
        task.id = null; // Prevent further autosaves for this task
        return;
      }
      if (res.ok) {
        const data = await res.json();
        if (data && data.updated_at) {
          task.updated_at = data.updated_at;
        }
      }
    })
    .catch(() => {});
  }
}

// Fetch initial time
(async () => {
  if (currentUserForTimer !== 'Unknown') {
    try {
      const res = await apiFetch('/api/team');
      if (res.ok) {
        const team = await res.json();
        const member = team.find(m => m.name === currentUserForTimer);
        if (member) {
          totalSeconds = member.time_logged || 0;
          lastSyncedTotalSeconds = totalSeconds;
          updateTimerDisplays();
        }
      }
    } catch (e) {}
  }
})();

const playSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
const pauseSvg = '<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>';

function formatTime(secondsToFormat) {
  const h = Math.floor(secondsToFormat / 3600).toString().padStart(2, '0');
  const m = Math.floor((secondsToFormat % 3600) / 60).toString().padStart(2, '0');
  const s = (secondsToFormat % 60).toString().padStart(2, '0');
  return `${h}:${m}:${s}`;
}

function updateTimerDisplays() {
  if (sessionTimerDisplay) sessionTimerDisplay.textContent = formatTime(sessionSeconds);
  if (totalTimeLoggedDisplay) totalTimeLoggedDisplay.textContent = formatTime(totalSeconds);
}

function syncTimeToServer() {
  if (currentUserForTimer !== 'Unknown') {
    const delta = totalSeconds - lastSyncedTotalSeconds;
    if (delta > 0) {
      apiFetch('/api/team/time', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ name: currentUserForTimer, time_logged: delta })
      }).catch(()=>{});
      lastSyncedTotalSeconds = totalSeconds;
    }
  }
}

function startTimer() {
  if (isTimerRunning) return;
  isTimerRunning = true;
  if (timerToggleBtn) {
    timerToggleBtn.innerHTML = pauseSvg;
    timerToggleBtn.title = "Pause Timer";
  }
  
  // Re-fetch username in case it changed
  currentUserForTimer = localStorage.getItem('dataset_username') || 'Unknown';

  timerInterval = setInterval(() => {
    sessionSeconds++;
    totalSeconds++;
    taskSessionSeconds++;
    
    if (sessionSeconds % 30 === 0) {
      syncTimeToServer();
    }
    
    updateTimerDisplays();
  }, 1000);
}

function pauseTimer() {
  if (!isTimerRunning) return;
  isTimerRunning = false;
  if (timerToggleBtn) {
    timerToggleBtn.innerHTML = playSvg;
    timerToggleBtn.title = "Start Timer";
  }
  clearInterval(timerInterval);
  syncTimeToServer(); // final sync on pause
}

if (timerToggleBtn) {
  timerToggleBtn.addEventListener("click", () => {
    if (isTimerRunning) {
      pauseTimer();
    } else {
      startTimer();
    }
  });
}

if (timerResetBtn) {
  timerResetBtn.addEventListener("click", () => {
    if (confirm("Reset the timer? This will clear your current session time.")) {
      pauseTimer();
      sessionSeconds = 0;
      updateTimerDisplays();
    }
  });
}

const sessionModal = document.getElementById("sessionModal");
const sessionModalTime = document.getElementById("sessionModalTime");
const sessionClose = document.getElementById("sessionClose");
const sessionOkBtn = document.getElementById("sessionOkBtn");

if (timerStopBtn) {
  timerStopBtn.addEventListener("click", () => {
    pauseTimer();
    if (sessionModalTime) sessionModalTime.textContent = formatTime(sessionSeconds);
    if (sessionModal) sessionModal.classList.add("is-active");
    updateTimerDisplays();
  });
}

function closeSessionModal() {
  if (sessionModal) sessionModal.classList.remove("is-active");
}

if (sessionClose) sessionClose.addEventListener("click", closeSessionModal);
if (sessionOkBtn) sessionOkBtn.addEventListener("click", closeSessionModal);

// Auto-start timer on canvas interaction
if (canvas) {
  canvas.addEventListener("pointerdown", () => {
    if (!isTimerRunning) {
      startTimer();
    }
  });
}

// Initialize displays
updateTimerDisplays();

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

// Sidebar Projects Logic
const createProjectSidebarForm = document.getElementById('createProjectSidebarForm');
const newProjectName = document.getElementById('newProjectName');
const projectsSidebarList = document.getElementById('projectsSidebarList');

let activeProjectId = null;

async function fetchSidebarProjects() {
  try {
    const username = localStorage.getItem('dataset_username') || '';
    const res = await apiFetch(`/api/projects?creator=${encodeURIComponent(username)}`);
    if (res.ok) {
      const projects = await res.json();
      renderSidebarProjects(projects);
    }
  } catch(e) {
    console.error("Failed to fetch projects", e);
  }
}


function renderSidebarProjects(projects) {
  projectsSidebarList.innerHTML = '';
  if (projects.length === 0) {
    projectsSidebarList.innerHTML = '<span style="color: var(--muted);">No projects yet.</span>';
    return;
  }
  
  // Show only up to 3 projects in the sidebar
  const visibleProjects = projects.slice(0, 3);
  
  visibleProjects.forEach(p => {
    const a = document.createElement('a');
    a.href = `project_details.html?id=${p.id}`;
    a.style.padding = '4px 8px';
    a.style.borderRadius = '4px';
    a.style.cursor = 'pointer';
    a.style.display = 'flex';
    a.style.justifyContent = 'space-between';
    a.style.alignItems = 'center';
    a.style.textDecoration = 'underline';
    
    const escapeHTML = (str) => String(str).replace(/[&<>'"]/g, match => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[match]));
    if (activeProjectId === p.id) {
      a.style.background = 'var(--accent)';
      a.style.color = '#fff';
      a.innerHTML = `<strong style="color: #fff; text-decoration: underline;">${escapeHTML(p.name)}</strong> <span style="font-size: 0.75rem;">${escapeHTML(p.status)}</span>`;
    } else {
      a.style.background = 'var(--panel-2)';
      a.innerHTML = `<strong style="color: #3b82f6; text-decoration: underline;">${escapeHTML(p.name)}</strong> <span style="font-size: 0.75rem;">${escapeHTML(p.status)}</span>`;
    }
    
    projectsSidebarList.appendChild(a);
  });
  
  // Add "Show All" button if there are more than 3 projects
  if (projects.length > 3) {
    const showAllBtn = document.createElement('a');
    showAllBtn.textContent = 'Show All';
    showAllBtn.style.cursor = 'pointer';
    showAllBtn.style.color = 'var(--muted)';
    showAllBtn.style.fontSize = '0.75rem';
    showAllBtn.style.textAlign = 'center';
    showAllBtn.style.display = 'block';
    showAllBtn.style.marginTop = '4px';
    showAllBtn.style.textDecoration = 'underline';
    
    showAllBtn.addEventListener('click', () => {
      openAllProjectsModal(projects);
    });
    
    projectsSidebarList.appendChild(showAllBtn);
  }
}

async function openAllProjectsModal(projects) {
  const modal = document.getElementById('allProjectsModal');
  const list = document.getElementById('allProjectsListModal');
  if (!modal || !list) return;
  
  let team = [];
  try {
    const teamRes = await apiFetch('/api/team');
    if (teamRes.ok) {
      const data = await teamRes.json();
      team = data.map(t => t.name);
    }
  } catch(e) {}
  
  const renderList = () => {
    list.innerHTML = '';
    projects.forEach(p => {
      const item = document.createElement('div');
      item.style.padding = '8px 12px';
      item.style.borderRadius = '6px';
      item.style.background = 'var(--panel-2)';
      item.style.display = 'flex';
      item.style.justifyContent = 'space-between';
      item.style.alignItems = 'center';
      item.style.border = '1px solid var(--line)';
      item.style.gap = '8px';
      
      const renderView = () => {
        const escapeHTML = (str) => String(str).replace(/[&<>'"]/g, match => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[match]));
        
        item.innerHTML = `
          <a href="project_details.html?id=${p.id}" style="text-decoration: none; display: flex; flex: 1; align-items: center; justify-content: space-between; min-width: 0; color: inherit; gap: 8px;">
            <strong style="color: #3b82f6; text-decoration: underline; font-size: 1rem; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; padding-right: 8px;">${escapeHTML(p.name)}</strong> 
            <div style="display: flex; gap: 8px; align-items: center;">
              <span style="font-size: 0.8rem; color: var(--muted);">${escapeHTML(p.assignee || 'Unassigned')}</span>
              <span class="status-badge" style="background: var(--bg); padding: 4px 8px; border-radius: 12px; font-size: 0.75rem; white-space: nowrap;">${escapeHTML(p.status)}</span>
            </div>
          </a>
          <div style="display: flex; gap: 8px; flex-shrink: 0;">
            <button type="button" class="edit-project-btn" style="padding: 6px; border-radius: 4px; display: inline-flex; align-items: center; justify-content: center; background: none; border: none; cursor: pointer; color: var(--ink);" title="Edit Project">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/></svg>
            </button>
            <button type="button" class="delete-project-btn" style="padding: 6px; border-radius: 4px; display: inline-flex; align-items: center; justify-content: center; background: none; border: none; cursor: pointer; color: #ff6b6b;" title="Delete Project">
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>
            </button>
          </div>
        `;
        
        item.querySelector('.edit-project-btn').addEventListener('click', () => {
          const assigneeOptions = team.map(m => `<option value="${escapeHTML(m)}" ${p.assignee === m ? 'selected' : ''}>${escapeHTML(m)}</option>`).join('');
          item.innerHTML = `
            <form class="edit-project-form" style="display: flex; flex-wrap: wrap; gap: 6px; width: 100%; align-items: center;" onsubmit="event.preventDefault();">
              <input type="text" class="edit-project-name" value="${escapeHTML(p.name)}" required style="flex: 1; min-width: 100px; padding: 4px; font-size: 0.85rem; border: 1px solid var(--line); border-radius: 4px; background: rgba(0,0,0,0.05); color: var(--ink);">
              <select class="edit-project-assignee" style="width: 100px; padding: 4px; border: 1px solid var(--line); border-radius: 4px; font-size: 0.85rem; background: rgba(0,0,0,0.05); color: var(--ink);">
                <option value="" ${!p.assignee ? 'selected' : ''}>Unassigned</option>
                ${assigneeOptions}
              </select>
              <select class="edit-project-status" style="width: 100px; padding: 4px; border: 1px solid var(--line); border-radius: 4px; font-size: 0.85rem; background: rgba(0,0,0,0.05); color: var(--ink);">
                <option value="Preparing" ${p.status === 'Preparing' ? 'selected' : ''}>Preparing</option>
                <option value="In Progress" ${p.status === 'In Progress' ? 'selected' : ''}>In Progress</option>
                <option value="Completed" ${p.status === 'Completed' ? 'selected' : ''}>Completed</option>
              </select>
              <button type="submit" class="primary" style="padding: 4px 8px; font-size: 0.75rem; border-radius: 4px;">Save</button>
              <button type="button" class="cancel-edit-btn" style="padding: 4px 8px; font-size: 0.75rem; background: var(--panel-2); border: 1px solid var(--line); border-radius: 4px; cursor: pointer;">Cancel</button>
            </form>
          `;
          
          const form = item.querySelector('.edit-project-form');
          const nameInput = item.querySelector('.edit-project-name');
          const statusInput = item.querySelector('.edit-project-status');
          const assigneeInput = item.querySelector('.edit-project-assignee');
          nameInput.focus();
          
          const finishEdit = async (save) => {
            if (save) {
              const newName = nameInput.value.trim();
              const newStatus = statusInput.value;
              const newAssignee = assigneeInput.value;
              if (newName) {
                try {
                  const res = await apiFetch('/api/projects/update', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ id: p.id, name: newName, status: newStatus, assignee: newAssignee })
                  });
                  if (res.ok) {
                    p.name = newName;
                    p.status = newStatus;
                    p.assignee = newAssignee;
                    fetchSidebarProjects();
                  } else {
                    alert('Failed to update project.');
                  }
                } catch(e) {
                  alert('Failed to update project.');
                }
              }
            }
            renderView();
          };
          
          form.addEventListener('submit', () => finishEdit(true));
          item.querySelector('.cancel-edit-btn').addEventListener('click', () => finishEdit(false));
        });
        
        item.querySelector('.delete-project-btn').addEventListener('click', async () => {
          if (confirm(`Delete project "${p.name}"? This action cannot be undone.`)) {
            try {
              const res = await apiFetch(`/api/projects/${p.id}`, { method: 'DELETE' });
              if (res.ok) {
                projects = projects.filter(proj => proj.id !== p.id);
                fetchSidebarProjects();
                renderList();
              } else {
                alert('Failed to delete project.');
              }
            } catch(e) {
              alert('Failed to delete project.');
            }
          }
        });
      };
      
      renderView();
      list.appendChild(item);
    });
  };
  
  renderList();
  modal.classList.add('is-active');
}

const allProjectsCloseBtn = document.getElementById('allProjectsClose');
if (allProjectsCloseBtn) {
  allProjectsCloseBtn.addEventListener('click', () => {
    document.getElementById('allProjectsModal').classList.remove('is-active');
  });
}


createProjectSidebarForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const name = newProjectName.value.trim();
  const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)+/g, '');
  const username = localStorage.getItem('dataset_username') || 'Unknown';
  
  try {
    const res = await apiFetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, slug, creator: username })
    });
    
    if (res.ok) {
      newProjectName.value = '';
      fetchSidebarProjects();
    } else {
      alert("Failed to create project");
    }
  } catch(e) {
    console.error(e);
  }
});

async function fetchLabels() {
  try {
    const res = await apiFetch('/api/labels');
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
        assignee: t.assignee
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
      }
    }
  } catch(e) {
    console.error(e);
  }
}

function initPanelDragAndDrop() {
  const container = document.getElementById('sidebarPanels');
  if (!container) return;

  // 1. Restore layout
  const savedLayout = localStorage.getItem('panelLayout');
  if (savedLayout) {
    try {
      const order = JSON.parse(savedLayout);
      order.forEach(id => {
        const panel = document.getElementById(id);
        if (panel) container.appendChild(panel);
      });
    } catch(e) {}
  }

  // 2. Setup dragging
  const panels = container.querySelectorAll('.panel');
  let draggedElement = null;

  panels.forEach(panel => {
    const handle = panel.querySelector('.drag-handle');
    if (handle) {
      handle.addEventListener('mousedown', () => panel.setAttribute('draggable', 'true'));
      handle.addEventListener('mouseup', () => panel.setAttribute('draggable', 'false'));
      handle.addEventListener('mouseleave', () => panel.setAttribute('draggable', 'false'));
    }

    panel.addEventListener('dragstart', (e) => {
      draggedElement = panel;
      e.dataTransfer.effectAllowed = 'move';
      // Firefox requires some data to be set
      e.dataTransfer.setData('text/plain', panel.id);
      setTimeout(() => panel.classList.add('is-dragging'), 0);
    });

    panel.addEventListener('dragend', () => {
      panel.classList.remove('is-dragging');
      panel.removeAttribute('draggable');
      draggedElement = null;
      savePanelLayout();
    });
  });

  container.addEventListener('dragover', e => {
    e.preventDefault();
    if (!draggedElement) return;
    const afterElement = getDragAfterElement(container, e.clientY);
    if (afterElement == null) {
      container.appendChild(draggedElement);
    } else {
      container.insertBefore(draggedElement, afterElement);
    }
  });

  function getDragAfterElement(container, y) {
    const draggableElements = [...container.querySelectorAll('.panel:not(.is-dragging)')];
    return draggableElements.reduce((closest, child) => {
      const box = child.getBoundingClientRect();
      const offset = y - box.top - box.height / 2;
      if (offset < 0 && offset > closest.offset) {
        return { offset: offset, element: child };
      } else {
        return closest;
      }
    }, { offset: Number.NEGATIVE_INFINITY }).element;
  }

  function savePanelLayout() {
    const currentOrder = Array.from(container.children)
      .filter(child => child.classList.contains('panel'))
      .map(child => child.id);
    localStorage.setItem('panelLayout', JSON.stringify(currentOrder));
  }
}

document.addEventListener('DOMContentLoaded', () => {
  initPanelDragAndDrop();
  window.addEventListener('beforeunload', () => {
    if (typeof state !== 'undefined' && state && state.galleryIndex >= 0) {
      syncToBackend();
    }
  });
  fetchSidebarProjects();
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
          const timeDelta = taskSessionSeconds;
          taskSessionSeconds = 0;
          const username = localStorage.getItem('dataset_username') || 'Unknown';
          const res = await apiFetch('/api/tasks', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              id: currentTask.id,
              status: 'Completed',
              time_spent_delta: timeDelta,
              assignee: username,
              annotations: JSON.stringify(state.annotations)
            })
          });
        
        if (res.ok) {
          const tcModal = document.getElementById('taskCompletedModal');
          if (tcModal) tcModal.classList.add('is-active');
        } else {
          alert('Failed to mark task as completed.');
        }
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

const canvas = document.querySelector("#annotationCanvas");
const ctx = canvas.getContext("2d");

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
const autoDetectButton = document.querySelector("#autoDetectButton");
const undoButton = document.querySelector("#undoButton");
const deleteButton = document.querySelector("#deleteButton");
const clearButton = document.querySelector("#clearButton");
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

const storageKey = "image-annotation-mvp-v1";
const labelStudioStorageKey = "image-annotation-label-studio-settings";
const handleSize = 9;
const closeThreshold = 15;
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
  selectedId: null,
  activeLabelId: null,
  mode: "draw",
  shape: "box",
  history: []
};

let imageElement = new Image();
let imageLoaded = false;
let imageBox = { x: 0, y: 0, width: 0, height: 0, scale: 1 };
let drag = null;
let hoverHandle = null;
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

function colorForName(name) {
  let hash = 0;
  for (let index = 0; index < name.length; index += 1) {
    hash = (hash * 31 + name.charCodeAt(index)) | 0;
  }
  return labelPalette[Math.abs(hash) % labelPalette.length];
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
    id: crypto.randomUUID(),
    name,
    color: customColor || colorForName(name)
  };
  state.labels.push(label);
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
  const timeoutId = setTimeout(() => controller.abort(), 10000);

  try {
    const response = await fetch(`${window.location.origin}/api/detect`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        image: state.image?.src || imageElement.src,
        selection
      })
    });
    clearTimeout(timeoutId);
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error || `Detection failed (${response.status})`);
    }

    const predictions = payload.predictions || [];
    snapshot();

    if (!predictions.length) {
      if (replace) {
        if (selected) {
          state.annotations = state.annotations.filter((item) => item.id === selected.id || item.source !== "auto-detect");
          state.selectedId = selected.id;
        } else {
          state.annotations = [];
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
        state.annotations = detected;
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
      id: crypto.randomUUID(),
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

function save() {
  const payload = {
    labels: state.labels,
    annotations: state.annotations,
    image: state.image
  };
  localStorage.setItem(storageKey, JSON.stringify(payload));
  setStatus("Saved");
}

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
    if (payload.image?.src) {
      state.image = payload.image;
      loadImageFromSource(payload.image.src, payload.image.name || "Restored image");
    }
  } catch {
    localStorage.removeItem(storageKey);
  }
}

function resizeCanvas() {
  const rect = stageWrap.getBoundingClientRect();
  const ratio = window.devicePixelRatio || 1;
  canvas.width = Math.floor(rect.width * ratio);
  canvas.height = Math.floor(rect.height * ratio);
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  if (pendingCommentPoint) {
    pendingCommentPoint = null;
    commentOverlay.classList.add("is-hidden");
  }
  draw();
}

function computeImageBox() {
  if (!imageLoaded) {
    imageBox = { x: 0, y: 0, width: 0, height: 0, scale: 1 };
    return;
  }

  const rect = canvas.getBoundingClientRect();
  const scale = Math.min(rect.width / imageElement.naturalWidth, rect.height / imageElement.naturalHeight);
  const width = imageElement.naturalWidth * scale;
  const height = imageElement.naturalHeight * scale;
  imageBox = {
    x: (rect.width - width) / 2,
    y: (rect.height - height) / 2,
    width,
    height,
    scale
  };
}

function draw() {
  const rect = canvas.getBoundingClientRect();
  ctx.clearRect(0, 0, rect.width, rect.height);
  computeImageBox();

  if (!imageLoaded) return;

  ctx.drawImage(imageElement, imageBox.x, imageBox.y, imageBox.width, imageBox.height);

  state.annotations.forEach((annotation) => drawAnnotation(annotation, annotation.id === state.selectedId));

  if (drag?.draft) {
    drawAnnotation(drag.draft, true);
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
    if (pts.length >= 3) {
      const first = pts[0];
      const screenX = imageBox.x + first.x * imageBox.scale;
      const screenY = imageBox.y + first.y * imageBox.scale;
      ctx.save();
      ctx.beginPath();
      ctx.arc(screenX, screenY, closeThreshold / 2, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(15, 139, 141, 0.35)";
      ctx.strokeStyle = "#0f8b8d";
      ctx.lineWidth = 2;
      ctx.fill();
      ctx.stroke();
      ctx.restore();
    }
    // Draw preview line from last point to cursor
    if (pts.length >= 1 && drag.preview) {
      const last = pts[pts.length - 1];
      const sx = imageBox.x + last.x * imageBox.scale;
      const sy = imageBox.y + last.y * imageBox.scale;
      const ex = imageBox.x + drag.preview.x * imageBox.scale;
      const ey = imageBox.y + drag.preview.y * imageBox.scale;
      ctx.save();
      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = "#0f8b8d";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(ex, ey);
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

function drawAnnotation(annotation, selected = false) {
  if (annotation.type === "comment") {
    const screenPoint = {
      x: imageBox.x + annotation.x * imageBox.scale,
      y: imageBox.y + annotation.y * imageBox.scale
    };
    ctx.save();
    ctx.fillStyle = selected ? "#f4a261" : "#e85d75";
    ctx.beginPath();
    ctx.arc(screenPoint.x, screenPoint.y, 8, 0, Math.PI * 2);
    ctx.fill();
    ctx.strokeStyle = "#ffffff";
    ctx.lineWidth = 2;
    ctx.stroke();

    const text = `${annotation.author || 'User'}: ${annotation.text}`;
    ctx.font = "600 12px Inter, system-ui, sans-serif";
    const tw = ctx.measureText(text).width + 12;
    ctx.fillStyle = "rgba(0,0,0,0.75)";
    ctx.beginPath();
    ctx.roundRect(screenPoint.x + 12, screenPoint.y - 12, tw, 24, 4);
    ctx.fill();
    ctx.fillStyle = "#ffffff";
    ctx.fillText(text, screenPoint.x + 18, screenPoint.y + 4);
    ctx.restore();
    return;
  }

  const label = labelById(annotation.labelId);
  const points = annotationPoints(annotation);
  const screenPoints = points.map((point) => ({
    x: imageBox.x + point.x * imageBox.scale,
    y: imageBox.y + point.y * imageBox.scale
  }));

  ctx.save();
  ctx.lineWidth = selected ? 3 : 2;
  ctx.strokeStyle = label.color;
  ctx.fillStyle = hexToRgba(label.color, selected ? 0.2 : 0.12);

  if (!screenPoints.length) {
    ctx.restore();
    return;
  }

  ctx.beginPath();
  screenPoints.forEach((point, index) => {
    if (index === 0) {
      ctx.moveTo(point.x, point.y);
    } else {
      ctx.lineTo(point.x, point.y);
    }
  });
  if (screenPoints.length >= 3) {
    ctx.closePath();
    ctx.fill();
  }
  ctx.stroke();

  const firstPoint = screenPoints[0];
  const tag = labelDisplayName(label);
  const bounds = screenPoints.reduce((accumulator, point) => ({
    minX: Math.min(accumulator.minX, point.x),
    minY: Math.min(accumulator.minY, point.y),
    maxX: Math.max(accumulator.maxX, point.x),
    maxY: Math.max(accumulator.maxY, point.y)
  }), { minX: Infinity, minY: Infinity, maxX: -Infinity, maxY: -Infinity });
  const tagWidth = Math.min(ctx.measureText(tag).width + 18, Math.max(bounds.maxX - bounds.minX, 54));
  const tagY = Math.max(4, bounds.minY - 24);
  ctx.font = "700 12px Inter, system-ui, sans-serif";
  ctx.fillStyle = label.color;
  ctx.fillRect(bounds.minX, tagY, tagWidth, 22);
  ctx.fillStyle = "#ffffff";
  ctx.fillText(tag, bounds.minX + 8, tagY + 15, tagWidth - 14);

  if (selected) {
    drawVertexHandles(screenPoints, label.color);
  }
  ctx.restore();
}

function drawVertexHandles(points, color) {
  const half = handleSize / 2;
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  points.forEach((point) => {
    ctx.beginPath();
    ctx.arc(point.x, point.y, half, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
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
    if (img.x >= ax && img.x <= ax + aw && img.y >= ay && img.y <= ay + ah) return annotation.id;

    // Fallback to polygon hit test for complex shapes
    const polygon = annotationPoints(annotation);
    if (pointInPolygon(img, polygon)) return annotation.id;
  }
  return null;
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

  if (state.mode === "select" && hitTest(point)) {
    canvas.style.cursor = "move";
    return;
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
    item.innerHTML = `
      <span class="swatch" style="background:${label.color}"></span>
      <strong></strong>
      <span class="delete-class-btn" title="Delete class" style="cursor: pointer; color: var(--muted); font-weight: bold;">×</span>
    `;
    item.querySelector("strong").textContent = labelDisplayName(label);

    // Click on the item itself sets it as active
    item.addEventListener("click", (e) => {
      if (e.target.classList.contains("delete-class-btn")) return;
      state.activeLabelId = label.id;
      render();
    });

    // Click on delete button
    item.querySelector(".delete-class-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      if (confirm(`Delete class "${labelDisplayName(label)}" and all its annotations?`)) {
        deleteClass(label.id);
      }
    });

    classesList.appendChild(item);
  });
}

function renderAnnotations() {
  annotationCount.textContent = String(state.annotations.length);
  annotationList.innerHTML = "";

  if (!state.annotations.length) {
    const empty = document.createElement("p");
    empty.className = "chip-count";
    empty.textContent = "No annotations yet";
    annotationList.appendChild(empty);
  }

  state.annotations.forEach((annotation, index) => {
    const label = annotation.type === "comment" ? { name: "Comment", color: "#e85d75" } : labelById(annotation.labelId);
    const bounds = annotationPoints(annotation);
    const item = document.createElement("button");
    item.type = "button";
    item.className = `annotation-item${annotation.id === state.selectedId ? " is-active" : ""}`;
    item.innerHTML = `
      <span class="swatch" style="background:${label.color}"></span>
      <strong></strong>
      <span></span>
    `;
    item.querySelector("strong").textContent = annotation.type === "comment" ? `💬 ${annotation.text || "Comment"}` : `${index + 1}. ${labelDisplayName(label)}`;
    item.querySelector("span:last-child").textContent = annotation.type === "comment" ? "" : `${bounds.length} pts`;
    item.addEventListener("click", () => {
      state.selectedId = annotation.id;
      state.mode = "select";
      render();
      draw();
    });
    annotationList.appendChild(item);
  });

  const selected = state.annotations.find((item) => item.id === state.selectedId);
  if (selected) {
    if (selected.type === "comment") {
      selectedInfo.textContent = `Comment by ${selected.author || "User"}`;
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
  deleteButton.disabled = !state.selectedId;
  clearButton.disabled = state.annotations.length === 0;
  const noData = !imageLoaded && state.annotations.length === 0;
  exportMenuButton.disabled = noData;
  labelStudioButton.disabled = labelStudioBusy || noData;
  emptyState.classList.toggle("is-hidden", imageLoaded);
}

if (logoutBtnApp) {
  logoutBtnApp.addEventListener("click", () => {
    localStorage.removeItem("dataset_username");
    localStorage.removeItem("image-annotation-mvp-v1");
    window.location.href = "index.html";
  });
}

function render() {
  renderClasses();
  renderAnnotations();
  renderControls();
  draw();
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

    item.annotations.forEach(ann => {
      if (ann.type === "comment") return; // skip comments in COCO export
      const label = labelById(ann.labelId);
      const category_id = labelToCategoryId[label.name] || 1;
      const points = annotationPoints(ann);
      const segmentation = [points.flatMap(p => [round(p.x), round(p.y)])];
      
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      points.forEach(p => {
        minX = Math.min(minX, p.x);
        minY = Math.min(minY, p.y);
        maxX = Math.max(maxX, p.x);
        maxY = Math.max(maxY, p.y);
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
        iscrowd: 0
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
    const response = await fetch(url, {
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
    const payload = buildExportTasks();

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
          id: label.id || crypto.randomUUID(),
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
            id: item.id || crypto.randomUUID(),
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
            id: crypto.randomUUID(),
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
    state.gallery[state.galleryIndex].annotations = [...state.annotations];
    syncTaskTime(state.gallery[state.galleryIndex]);
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

commentOverlayInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    const text = commentOverlayInput.value;
    if (text && text.trim() !== "" && pendingCommentPoint) {
      snapshot();
      const annotation = {
        id: crypto.randomUUID(),
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
    } else {
      // If empty, treat as cancel
      pendingCommentPoint = null;
      commentOverlay.classList.add("is-hidden");
      render();
    }
  } else if (e.key === "Escape") {
    e.preventDefault();
    pendingCommentPoint = null;
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
  if (!state.selectedId) return;
  snapshot();
  // If deleting the polygon being drawn, clean up drag state
  if (drag?.type === "draw-polygon" && drag.annotationId === state.selectedId) {
    drag = null;
  }
  state.annotations = state.annotations.filter((item) => item.id !== state.selectedId);
  state.selectedId = null;
  render();
  save();
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
document.addEventListener("click", (e) => {
  if (!exportDropdown.contains(e.target)) {
    exportDropdown.classList.remove("show");
  }
  if (!importDropdown.contains(e.target)) {
    importDropdown.classList.remove("show");
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

canvas.addEventListener("pointerdown", (event) => {
  if (!imageLoaded) return;
  canvas.setPointerCapture(event.pointerId);
  const point = canvasPoint(event);

  // In draw mode, skip hit-testing – clicks should create shapes, not select existing ones
  if (state.mode !== "draw") {
    const hitId = hitTest(point);
    if (hitId) {
      state.selectedId = hitId;
      state.mode = "select";
      const annotation = state.annotations.find((item) => item.id === hitId);
      snapshot();
      drag = {
        type: "move",
        start: imagePoint(point),
        original: { ...annotation }
      };
      render();
      return;
    }
  }

  if (state.mode === "select") {
    state.selectedId = null;
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
          id: crypto.randomUUID(),
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
          const screenClick = {
            x: imageBox.x + pointInImage.x * imageBox.scale,
            y: imageBox.y + pointInImage.y * imageBox.scale
          };
          if (Math.hypot(screenFirst.x - screenClick.x, screenFirst.y - screenClick.y) < closeThreshold) {
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
  const point = canvasPoint(event);
  updateCanvasCursor(point);
  if (!drag) return;

  const end = imagePoint(point);
  if (drag.type === "draw-polygon") {
    drag.preview = end;
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
    const updated = {
      ...drag.original,
      points: (drag.original.points || annotationPoints(drag.original)).map((item) => ({
        x: round(clamp(item.x + (end.x - drag.start.x), 0, imageElement.naturalWidth)),
        y: round(clamp(item.y + (end.y - drag.start.y), 0, imageElement.naturalHeight))
      }))
    };
    updateAnnotationBounds(updated);
    replaceAnnotation(updated);
    render();
  }
});

canvas.addEventListener("dblclick", () => {
  if (state.shape === "polygon") {
    finalizePolygon();
  }
});

canvas.addEventListener("pointerup", () => {
  if (drag?.type === "move") {
    const updated = selectedAnnotation();
    const original = drag.original;
    drag = null;
    if (updated && annotationChanged(original, updated)) {
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
        id: crypto.randomUUID(),
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
    }
    draw();
  }
});

canvas.addEventListener("pointerleave", () => {
  if (!drag) canvas.style.cursor = "default";
});

canvas.addEventListener("pointercancel", () => {
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
    drag = null;
    render();
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

if (openSettingsBtn) {
  openSettingsBtn.addEventListener("click", () => {
    settingsUsernameInput.value = localStorage.getItem("dataset_username") || "";
    settingsModal.classList.add("is-active");
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
let isTimerRunning = false;

async function syncTaskTime(task) {
  if (task && task.id) {
    const timeDelta = taskSessionSeconds;
    taskSessionSeconds = 0;
    fetch('/api/tasks', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        id: task.id,
        time_spent_delta: timeDelta,
        status: task.status || 'In Progress',
        assignee: localStorage.getItem('dataset_username') || 'Unknown',
        annotations: JSON.stringify(task.annotations || [])
      })
    }).catch(() => {});
  }
}

// Fetch initial time
(async () => {
  if (currentUserForTimer !== 'Unknown') {
    try {
      const res = await fetch('/api/team');
      if (res.ok) {
        const team = await res.json();
        const member = team.find(m => m.name === currentUserForTimer);
        if (member) {
          totalSeconds = member.time_logged || 0;
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
    fetch('/api/team/time', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ name: currentUserForTimer, time_logged: totalSeconds })
    }).catch(()=>{});
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
    
    if (sessionSeconds % 5 === 0) {
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
      const res = await fetch('/api/team');
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
    const res = await fetch(`/api/projects?creator=${encodeURIComponent(username)}`);
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
    
    if (activeProjectId === p.id) {
      a.style.background = 'var(--accent)';
      a.style.color = '#fff';
      a.innerHTML = `<strong style="color: #fff; text-decoration: underline;">${p.name}</strong> <span style="font-size: 0.75rem;">${p.status}</span>`;
    } else {
      a.style.background = 'var(--panel-2)';
      a.innerHTML = `<strong style="color: #3b82f6; text-decoration: underline;">${p.name}</strong> <span style="font-size: 0.75rem;">${p.status}</span>`;
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

function openAllProjectsModal(projects) {
  const modal = document.getElementById('allProjectsModal');
  const list = document.getElementById('allProjectsListModal');
  if (!modal || !list) return;
  
  list.innerHTML = '';
  projects.forEach(p => {
    const a = document.createElement('a');
    a.href = `project_details.html?id=${p.id}`;
    a.style.padding = '8px 12px';
    a.style.borderRadius = '6px';
    a.style.background = 'var(--panel-2)';
    a.style.display = 'flex';
    a.style.justifyContent = 'space-between';
    a.style.alignItems = 'center';
    a.style.textDecoration = 'none';
    a.style.color = 'inherit';
    a.style.border = '1px solid var(--line)';
    
    a.innerHTML = `<strong style="color: #3b82f6; text-decoration: underline; font-size: 1rem;">${p.name}</strong> 
                   <span class="status-badge" style="background: var(--bg); padding: 4px 8px; border-radius: 12px; font-size: 0.75rem;">${p.status}</span>`;
    
    list.appendChild(a);
  });
  
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
    const res = await fetch('/api/projects', {
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

document.addEventListener('DOMContentLoaded', () => {
  fetchSidebarProjects();
});

// Workspace Project Support
const urlParams = new URLSearchParams(window.location.search);
const projectId = urlParams.get('projectId');

async function loadWorkspaceTasks() {
  if (!projectId) return;
  try {
    const res = await fetch(`/api/tasks?projectId=${projectId}`);
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
        // Start from index 1 as requested, fallback to 0 if only 1 image
        const startIndex = state.gallery.length > 1 ? 1 : 0;
        switchImage(startIndex);
      } else {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        updateGalleryUI();
      }
    }
  } catch(e) {
    console.error(e);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  if (projectId) {
    loadWorkspaceTasks();
  }

});

// Complete Task Logic
document.addEventListener('DOMContentLoaded', () => {
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
          const res = await fetch('/api/tasks', {
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

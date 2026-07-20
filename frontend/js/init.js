import { generateUUID, clamp, round, normalizeClassName, formatTime } from "./utils.js?v=1";
import { apiFetch, pollJob } from "./api.js?v=1";
import {
  state, storageKey, labelStudioStorageKey, handleSize,
  colorForName, labelByName, labelById, labelDisplayName, snapshot, resetWorkspaceForNewImage,
  selectedAnnotation
} from "./state.js?v=1";
import { view } from "./canvas/view.js?v=1";
import { timerState } from "./timer-state.js?v=1";
import { commentOverlayRefs } from "./comment-overlay.js?v=1";
import {
  canvas, ctx, imageCanvas, imageCtx, staticCanvas, staticCtx, stageWrap,
  emptyState, drawMode, selectMode, boxMode, polygonMode, commentMode, magicWandMode,
  autoDetectButton, undoButton, deleteButton, clearButton, exportMenuButton,
  labelStudioButton, labelStudioProxyInput
} from "./dom.js?v=1";
import { drawAllLayers } from "./canvas/draw.js?v=1";
import { exportJsonData } from "./export/coco.js?v=1";
import { exportCsvData } from "./export/csv.js?v=1";
import {
  setStatus, ensureLabel,
  syncToBackend, save, loadSaved,
  render, sendToEndpoint, importData, importCsvData
} from "./components/workspace.js?v=1";
import { autoDetectObjects, autoTagObjects } from "./ai/detect.js?v=1";
import { syncTaskTime, syncTimeToServer } from "./components/timer.js?v=1";
import { finalizePolygon, deleteSelected } from "./canvas/interactions.js?v=1";

if (!localStorage.getItem('logged_in')) {
  window.location.href = '/';
}

const imageInput = document.querySelector("#imageInput");
const imageName = document.querySelector("#imageName");
const imageSize = document.querySelector("#imageSize");
const addClassButton = document.querySelector("#addClassButton");
const newClassForm = document.querySelector("#newClassForm");
const newClassName = document.querySelector("#newClassName");
const newClassColor = document.querySelector("#newClassColor");
const autoTagButton = document.querySelector("#autoTagButton");
const aiSettingsMenuButton = document.querySelector("#aiSettingsMenuButton");
const aiSettingsDropdownContainer = document.querySelector("#aiSettingsDropdownContainer");
const importMenuButton = document.querySelector("#importMenuButton");
const importDropdown = document.querySelector("#importDropdown").parentElement;
const importJsonButton = document.querySelector("#importJsonButton");
const importCsvButton = document.querySelector("#importCsvButton");
const importJsonInput = document.querySelector("#importJsonInput");
const importCsvInput = document.querySelector("#importCsvInput");
const exportDropdown = document.querySelector("#exportDropdown").parentElement;
const exportJsonButton = document.querySelector("#exportJsonButton");
const exportCsvButton = document.querySelector("#exportCsvButton");
const labelStudioProjectInput = document.querySelector("#labelStudioProjectInput");
const labelStudioTaskInput = document.querySelector("#labelStudioTaskInput");
const labelStudioFromInput = document.querySelector("#labelStudioFromInput");
const labelStudioToInput = document.querySelector("#labelStudioToInput");
const prevImageButton = document.querySelector("#prevImageButton");
const nextImageButton = document.querySelector("#nextImageButton");
const galleryPosition = document.querySelector("#galleryPosition");
const clearGalleryButton = document.querySelector("#clearGalleryButton");
const logoutBtnApp = document.querySelector("#logoutBtnApp");

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
    imageName.textContent = name;
    imageSize.textContent = `${view.imageElement.naturalWidth} x ${view.imageElement.naturalHeight}`;
    state.image = { src, name, width: view.imageElement.naturalWidth, height: view.imageElement.naturalHeight };
    if (state.galleryIndex >= 0 && state.gallery[state.galleryIndex]) {
      state.gallery[state.galleryIndex].width = view.imageElement.naturalWidth;
      state.gallery[state.galleryIndex].height = view.imageElement.naturalHeight;
    }
    resizeCanvas();
    render();
    if (autoDetect) {
      await autoDetectObjects({ replace: true });
    } else {
      save();
    }
  };
  view.imageElement.src = src;
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
    clearGalleryButton.disabled = total === 0 && !view.imageLoaded;
  }
}

prevImageButton.addEventListener("click", () => switchImage(state.galleryIndex - 1));
nextImageButton.addEventListener("click", () => switchImage(state.galleryIndex + 1));

if (clearGalleryButton) {
  clearGalleryButton.addEventListener("click", () => {
    state.gallery.forEach(item => URL.revokeObjectURL(item.url));
    state.gallery = [];
    state.galleryIndex = -1;
    view.imageLoaded = false;
    view.imageElement = new Image();
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
  const previous = state.history.pop();
  if (!previous) return;
  const restored = JSON.parse(previous);
  state.labels = restored.labels;
  state.annotations = restored.annotations;
  state.selectedId = restored.selectedId;
  // Clear polygon draw state if the annotation was undone
  if (view.drag?.type === "draw-polygon") {
    const exists = state.annotations.some((item) => item.id === view.drag.annotationId);
    if (!exists) view.drag = null;
  }
  render();
  save();
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
          const catIdToLabelId = {};
          for (const cat of importedData.categories) {
            const name = cat.title || cat.name;
            if (name) {
              const existing = ensureLabel(name, cat.color);
              catIdToLabelId[cat.id] = existing.id;
            }
          }

          for (const ann of importedData.annotations) {
            const labelId = catIdToLabelId[ann.category_id];
            if (!labelId) continue;

            let points = [];
            if (ann.segmentation && ann.segmentation.length > 0 && ann.segmentation[0].length > 0) {
              const seg = ann.segmentation[0];
              for (let i = 0; i < seg.length; i += 2) {
                points.push({ x: seg[i], y: seg[i + 1] });
              }
            } else if (ann.bbox && ann.bbox.length === 4) {
              const [x, y, w, h] = ann.bbox;
              points = [
                { x: x, y: y }, { x: x + w, y: y }, { x: x + w, y: y + h }, { x: x, y: y + h }
              ];
            }

            if (points.length > 0) {
              const bounds = { x: Math.min(...points.map(p => p.x)), y: Math.min(...points.map(p => p.y)) };
              bounds.width = Math.max(...points.map(p => p.x)) - bounds.x;
              bounds.height = Math.max(...points.map(p => p.y)) - bounds.y;

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
          // Legacy format: Validate each annotation
          for (const ann of importedData) {
             if (ann.points && ann.labelId) {
                // Ensure the label actually exists, otherwise assign a default or skip
                let label = labelById(ann.labelId);
                if (!label) {
                   // Try to recover by creating a generic label or use active label
                   if (state.activeLabelId && labelById(state.activeLabelId)) {
                      ann.labelId = state.activeLabelId;
                   } else {
                      continue; // Skip invalid annotation
                   }
                }
                if (!ann.id) ann.id = generateUUID();
                importedAnnotations.push(ann);
             }
          }
        } else {
          alert("Invalid objects file format. Expected COCO JSON or a JSON array.");
          return;
        }

        if (importedAnnotations.length > 0) {
          state.annotations = [...state.annotations, ...importedAnnotations];
          render();
          save();
          setStatus(`Imported ${importedAnnotations.length} objects.`);
        } else {
          alert("No valid objects found to import.");
        }
      } catch (err) {
        console.error(err);
        alert("Failed to parse objects JSON.");
      } finally {
        importObjectsInput.value = ""; // reset
      }
    };
    reader.readAsText(file);
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
        { id: 1, width: view.imageElement?.naturalWidth || 800, height: view.imageElement?.naturalHeight || 600, file_name: state.imageName || "image.jpg" }
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
        segmentation = [ann.points.flatMap(p => [p.x, p.y])];
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

const exportImageBtn = document.getElementById("exportImageBtn");
if (exportImageBtn) {
  exportImageBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    if (!view.imageLoaded || !view.imageElement) {
      alert("No image loaded to export.");
      return;
    }
    
    const exportCvs = document.createElement("canvas");
    exportCvs.width = view.imageElement.naturalWidth;
    exportCvs.height = view.imageElement.naturalHeight;
    const exportCtx = exportCvs.getContext("2d");
    
    exportCtx.drawImage(view.imageElement, 0, 0);
    
    state.annotations.forEach(ann => {
      const color = labelById(ann.labelId)?.color || "#0f8b8d";
      exportCtx.strokeStyle = color;
      exportCtx.lineWidth = Math.max(2, Math.floor(Math.min(exportCvs.width, exportCvs.height) * 0.002));
      exportCtx.fillStyle = color + "40"; // ~25% opacity
      
      const isPolygon = ann.type === "polygon" || (ann.points && ann.points.length !== 4);
      
      exportCtx.beginPath();
      if (isPolygon && ann.points && ann.points.length > 0) {
        ann.points.forEach((pt, i) => {
          if (i === 0) exportCtx.moveTo(pt.x, pt.y);
          else exportCtx.lineTo(pt.x, pt.y);
        });
      } else {
        exportCtx.rect(ann.x, ann.y, ann.width, ann.height);
      }
      exportCtx.closePath();
      exportCtx.fill();
      exportCtx.stroke();
    });
    
    const dataUrl = exportCvs.toDataURL("image/jpeg", 0.95);
    const a = document.createElement("a");
    a.href = dataUrl;
    const originalName = state.imageName || "export";
    a.download = originalName.replace(/\.[^/.]+$/, "") + "_annotated.jpg";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
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
  } catch (e) {
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
  } catch (e) { }

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
                    headers: { 'Content-Type': 'application/json' },
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
                } catch (e) {
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
            } catch (e) {
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
  } catch (e) {
    console.error(e);
  }
});

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
  } catch (e) {
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
    } catch (e) { }
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
          const timeDelta = timerState.taskSessionSeconds;
          timerState.taskSessionSeconds = 0;
          const username = localStorage.getItem('dataset_username') || 'Unknown';
          const res = await apiFetch('/api/tasks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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

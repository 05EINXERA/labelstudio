import { generateUUID, round } from "../utils.js?v=1";
import { apiFetch, pollJob } from "../api.js?v=1";
import { state, colorForName, labelByName, snapshot, selectedAnnotation } from "../state.js?v=1";
import { updateAnnotationBounds } from "../canvas/geometry.js?v=1";
import { view } from "../canvas/view.js?v=1";
import { detectState } from "./detect-state.js?v=1";
import { getImageSrcForAPI } from "./shared.js?v=1";
import { autoDetectButton } from "../dom.js?v=1";
import {
  setStatus, ensureLabel, save, render
} from "../components/workspace.js?v=1";

export function setDetectionBusy(isBusy) {
  detectState.detectionBusy = isBusy;
  autoDetectButton.disabled = isBusy || !view.imageLoaded;
  const labelSpan = autoDetectButton.querySelector(".btn-label");
  if (labelSpan) {
    labelSpan.textContent = isBusy ? "Detecting..." : "Detect";
  }
}

export function predictionsToAnnotations(predictions) {
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
      // "bbox", not "box": this is the vocabulary the exporters read
      // (formats/common.py annotation_type_of). Nothing renders off this
      // string — draw.js keys on type === "polygon" or the point count — so
      // the old value was write-only and never reached an export correctly.
      type: prediction.points ? "polygon" : "bbox",
      detectedClass: prediction.class
    };
  });
}

export async function autoDetectObjects({ replace = true } = {}) {
  if (!view.imageLoaded || detectState.detectionBusy) return 0;

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

export async function autoTagObjects() {
  if (!view.imageLoaded || detectState.detectionBusy) return;

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

export function showAutoTagModal(tags) {
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

export async function performMagicWandSegmentation(point, bbox = null, isShift = false, isAlt = false) {
  if (!view.imageLoaded || detectState.detectionBusy) return;

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
        // SAM returns a traced mask contour — always a polygon, never a box.
        type: "polygon",
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

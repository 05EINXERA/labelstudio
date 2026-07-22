import { generateUUID, clamp, round } from "../utils.js?v=1";
import { state, snapshot, isAnnotationHidden } from "../state.js?v=1";
import { annotationPoints, updateAnnotationBounds, pointInPolygon } from "./geometry.js?v=1";
import { view } from "./view.js?v=1";
import { draw, drawAllLayers } from "./draw.js?v=1";
import { canvas, undoButton } from "../dom.js?v=1";
import { commentOverlayRefs } from "../comment-overlay.js?v=1";
import { setStatus, save, render, ensureLabel } from "../components/workspace.js?v=1";
import { performMagicWandSegmentation } from "../ai/detect.js?v=1";

export function canvasPoint(event) {
  const rect = canvas.getBoundingClientRect();
  return {
    x: event.clientX - rect.left,
    y: event.clientY - rect.top
  };
}

export function imagePoint(point) {
  return {
    x: clamp((point.x - view.imageBox.x) / view.imageBox.scale, 0, view.imageElement.naturalWidth),
    y: clamp((point.y - view.imageBox.y) / view.imageBox.scale, 0, view.imageElement.naturalHeight)
  };
}

export function hitTest(point) {
  const img = imagePoint(point);
  for (let index = state.annotations.length - 1; index >= 0; index -= 1) {
    const annotation = state.annotations[index];
    // Hidden annotations are not on screen, so they must not be selectable:
    // clicking empty space should not pick up something invisible.
    if (isAnnotationHidden(annotation)) continue;
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

export function hitTestPoint(point, annotation) {
  if (!annotation || !annotation.points) return -1;
  const img = imagePoint(point);
  const threshold = 6 / view.imageBox.scale;
  for (let i = 0; i < annotation.points.length; i++) {
    const pt = annotation.points[i];
    if (Math.hypot(pt.x - img.x, pt.y - img.y) < threshold) {
      return i;
    }
  }
  return -1;
}

export function hitTestLine(point, annotation) {
  if (!annotation || !annotation.points || annotation.points.length < 3) return -1;
  const img = imagePoint(point);
  const threshold = 6 / view.imageBox.scale;
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

export function replaceAnnotation(updated) {
  state.annotations = state.annotations.map((item) => (
    item.id === updated.id ? updated : item
  ));
}

export function annotationChanged(before, after) {
  const beforePoints = annotationPoints(before);
  const afterPoints = annotationPoints(after);
  if (beforePoints.length !== afterPoints.length) return true;
  return beforePoints.some((point, index) => point.x !== afterPoints[index].x || point.y !== afterPoints[index].y);
}

export function updateCanvasCursor(point) {
  if (!view.imageLoaded) {
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

export function finalizePolygon() {
  if (view.drag?.type !== "draw-polygon") return;
  const annotation = state.annotations.find((item) => item.id === view.drag.annotationId);
  view.drag = null;
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
  state.needsLabelSelection = true;
  render();
  save();
  setStatus("Please select a class name for the next polygon");
}

export function undoLastPoint() {
  if (view.drag?.type === "draw-polygon") {
    const annotation = state.annotations.find((item) => item.id === view.drag.annotationId);
    if (annotation && annotation.points && annotation.points.length > 1) {
      if (!view.drag.undonePoints) view.drag.undonePoints = [];
      const popped = annotation.points.pop();
      view.drag.undonePoints.push(popped);
      updateAnnotationBounds(annotation);
      render();
      save();
      return true;
    }
  }
  return false;
}

export function redoLastPoint() {
  if (view.drag?.type === "draw-polygon" && view.drag.undonePoints?.length > 0) {
    const annotation = state.annotations.find((item) => item.id === view.drag.annotationId);
    if (annotation && annotation.points) {
      const restored = view.drag.undonePoints.pop();
      annotation.points.push(restored);
      updateAnnotationBounds(annotation);
      render();
      save();
      return true;
    }
  }
  return false;
}

export function undoAction() {
  if (undoLastPoint()) {
    return;
  }
  const previous = state.history.pop();
  if (!previous) return;
  
  state.redoHistory.push(JSON.stringify({
    labels: state.labels,
    annotations: state.annotations,
    selectedId: state.selectedId
  }));

  const restored = JSON.parse(previous);
  state.labels = restored.labels;
  state.annotations = restored.annotations;
  state.selectedId = restored.selectedId;
  
  if (view.drag?.type === "draw-polygon") {
    const exists = state.annotations.some((item) => item.id === view.drag.annotationId);
    if (!exists) view.drag = null;
  }
  render();
  save();
}

export function redoAction() {
  if (redoLastPoint()) {
    return;
  }
  const next = state.redoHistory.pop();
  if (!next) return;

  state.history.push(JSON.stringify({
    labels: state.labels,
    annotations: state.annotations,
    selectedId: state.selectedId
  }));

  const restored = JSON.parse(next);
  state.labels = restored.labels;
  state.annotations = restored.annotations;
  state.selectedId = restored.selectedId;

  if (view.drag?.type === "draw-polygon") {
    const exists = state.annotations.some((item) => item.id === view.drag.annotationId);
    if (!exists) view.drag = null;
  }
  render();
  save();
}

// The zoom readout subscribes here rather than being imported directly: the
// component already imports setZoom, so a direct import would be circular.
let onZoomChange = null;
export function setZoomChangeHandler(fn) {
  onZoomChange = fn;
}

export function setZoom(newZoom, mouseX, mouseY) {
  if (!view.imageLoaded) return;
  const oldZoom = view.viewZoom;
  view.viewZoom = Math.max(0.25, Math.min(100, newZoom));

  const rect = canvas.getBoundingClientRect();
  const cx = mouseX !== undefined ? mouseX : rect.width / 2;
  const cy = mouseY !== undefined ? mouseY : rect.height / 2;

  // Must match computeImageBox's contain-fit, or zoom-at-cursor drifts.
  const baseScale = Math.min(
    rect.width / view.imageElement.naturalWidth,
    rect.height / view.imageElement.naturalHeight
  );
  const oldScale = baseScale * oldZoom;
  const newScale = baseScale * view.viewZoom;

  const imgX = (cx - view.imageBox.x) / oldScale;
  const imgY = (cy - view.imageBox.y) / oldScale;

  const newWidth = view.imageElement.naturalWidth * newScale;
  const newHeight = view.imageElement.naturalHeight * newScale;

  view.viewPan.x = cx - (rect.width - newWidth) / 2 - imgX * newScale;
  view.viewPan.y = cy - (rect.height - newHeight) / 2 - imgY * newScale;

  drawAllLayers();
  // Notified here, not from the buttons, so wheel zoom updates the readout too.
  if (onZoomChange) onZoomChange();
}

export function deleteSelected() {
  if (state.selectedIds.size === 0) return;
  snapshot();
  // If deleting the polygon being drawn, clean up view.drag state
  if (view.drag?.type === "draw-polygon" && state.selectedIds.has(view.drag.annotationId)) {
    view.drag = null;
  }
  // Drop visibility state for the ids going away, so the set does not grow
  // unboundedly across a session.
  state.selectedIds.forEach((id) => state.hiddenAnnotationIds.delete(id));
  state.annotations = state.annotations.filter((item) => !state.selectedIds.has(item.id));
  state.selectedIds.clear();
  state.selectedId = null;
  view.selectedLineIndex = -1;
  view.hoveredLineIndex = -1;
  view.hoveredPointIndex = -1;
  render();
  save();
}

const groupButton = document.querySelector("#groupButton");
if (groupButton) {
  groupButton.addEventListener("click", () => {
    groupSelectedAnnotations();
  });
}

export function groupSelectedAnnotations() {
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

canvas.addEventListener("wheel", (event) => {
  if (!view.imageLoaded) return;
  event.preventDefault();
  const rect = canvas.getBoundingClientRect();
  const mouseX = event.clientX - rect.left;
  const mouseY = event.clientY - rect.top;
  const zoomFactor = event.deltaY < 0 ? 1.1 : 1 / 1.1;
  setZoom(view.viewZoom * zoomFactor, mouseX, mouseY);
}, { passive: false });

canvas.addEventListener("contextmenu", (event) => {
  event.preventDefault();
});

canvas.addEventListener("pointerdown", (event) => {
  if (!view.imageLoaded) return;
  canvas.setPointerCapture(event.pointerId);

  if (event.button === 2 || (event.button === 0 && event.shiftKey && event.altKey)) {
    event.preventDefault();
    view.isPanning = true;
    view.panStart = { x: event.clientX, y: event.clientY, panX: view.viewPan.x, panY: view.viewPan.y };
    canvas.style.cursor = "grabbing";
    return;
  }

  const point = canvasPoint(event);

  // Left-click on a polygon edge to add a vertex.
  // Skipped while drawing: hitTestLine treats the shape as already closed, so it
  // reports a phantom edge from the last vertex back to the first. Splitting it
  // would start a move-point view.drag and silently end the in-progress polygon.
  if (state.selectedId && event.button === 0 && !event.altKey && view.drag?.type !== "draw-polygon") {
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

          view.drag = {
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
  // Vertex/edge deletion — also skipped while drawing, so an Alt+click cannot
  // remove points from the polygon currently being placed.
  if (state.selectedId && (event.altKey || event.button === 2) && view.drag?.type !== "draw-polygon") {
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
        const toRemove = [lnIndex, nextIndex].sort((a, b) => b - a);
        selected.points.splice(toRemove[0], 1);
        selected.points.splice(toRemove[1], 1);
        view.selectedLineIndex = -1;
        view.hoveredLineIndex = -1;
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
        // While drawing, only the first vertex is meaningful — it closes the shape.
        // Any other vertex hit must fall through to the drawing code below, or the
        // move-point view.drag would overwrite view.drag.type and silently end the polygon.
        if (view.drag?.type === "draw-polygon") {
          if (ptIndex === 0) {
            finalizePolygon();
            return;
          }
        } else {
          snapshot();
          view.drag = {
            type: "move-point",
            annotationId: selected.id,
            pointIndex: ptIndex
          };
          return;
        }
      }
    }
  }

  // In draw mode, skip hit-testing – clicks should create shapes, not select existing ones.
  // Also skipped while a polygon is in progress: the polygon being drawn is itself a hit
  // target, so selecting it would replace view.drag with a "move" and end the shape. state.mode
  // is not sufficient here — line 3286 can leave it as "select" before drawing ever starts.
  if (state.mode !== "draw" && view.drag?.type !== "draw-polygon") {
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
      view.selectedLineIndex = -1;
      view.hoveredLineIndex = -1;
      state.mode = "select";
      snapshot();
      view.drag = {
        type: "move",
        start: imagePoint(point),
        originals: state.annotations.filter(a => state.selectedIds.has(a.id)).map(a => JSON.parse(JSON.stringify(a)))
      };
      render();
      return;
    }
  }

  // Clicking empty space clears the selection — but never while a polygon is being
  // drawn, or this would discard view.drag (and the in-progress shape) on every click.
  if (state.mode === "select" && view.drag?.type !== "draw-polygon") {
    state.selectedId = null;
    state.selectedIds.clear();
    view.selectedLineIndex = -1;
    view.hoveredLineIndex = -1;
    view.drag = null;
    render();
    return;
  }

  // An in-progress polygon keeps receiving points even if state.mode drifted to
  // "select", so the shape can always be completed once started.
  if (state.mode === "draw" || view.drag?.type === "draw-polygon") {
    const pointInImage = imagePoint(point);

    if (state.shape === "comment") {
      view.pendingCommentPoint = pointInImage;
      render();

      const screenPoint = {
        x: view.imageBox.x + view.pendingCommentPoint.x * view.imageBox.scale,
        y: view.imageBox.y + view.pendingCommentPoint.y * view.imageBox.scale
      };

      commentOverlayRefs.commentOverlay.style.left = `${screenPoint.x + 15}px`;
      commentOverlayRefs.commentOverlay.style.top = `${screenPoint.y - 15}px`;
      commentOverlayRefs.commentOverlay.classList.remove("is-hidden");
      commentOverlayRefs.commentOverlayInput.value = "";
      commentOverlayRefs.commentOverlayInput.focus();
      return;
    }

    if (state.shape === "polygon") {
      if (view.drag?.type !== "draw-polygon") {
        if (state.needsLabelSelection) {
          setStatus("Please select a class name first");
          return;
        }
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
        view.drag = { type: "draw-polygon", annotationId: annotation.id };
      } else {
        // Subsequent points – add to the live annotation
        const annotation = state.annotations.find((item) => item.id === view.drag.annotationId);
        if (!annotation) { view.drag = null; render(); return; }
        const pts = annotation.points || [];

        // Closure is now handled by the hitTestPoint logic above

        const lastPoint = pts[pts.length - 1];
        if (!lastPoint || Math.hypot(lastPoint.x - pointInImage.x, lastPoint.y - pointInImage.y) > 1) {
          annotation.points.push({ x: round(pointInImage.x), y: round(pointInImage.y) });
          updateAnnotationBounds(annotation);
          if (view.drag.undonePoints) view.drag.undonePoints = [];
        }
      }
      render();
      save();
      return;
    } else {
      if (state.needsLabelSelection && state.shape !== "magicWand") {
        setStatus("Please select a class name first");
        return;
      }
      if (!state.activeLabelId) {
        const defaultLabel = ensureLabel("object");
        state.activeLabelId = defaultLabel.id;
      }
      view.drag = {
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
  if (view.isPanning) {
    const dx = event.clientX - view.panStart.x;
    const dy = event.clientY - view.panStart.y;
    view.viewPan.x = view.panStart.panX + dx;
    view.viewPan.y = view.panStart.panY + dy;
    drawAllLayers();
    return;
  }
  const point = canvasPoint(event);
  updateCanvasCursor(point);

  // Detect line hover on selected polygon (even when no view.drag)
  if (state.selectedId && !view.drag) {
    const selected = state.annotations.find(a => a.id === state.selectedId);
    if (selected && selected.points && selected.points.length >= 3) {
      const ptIndex = hitTestPoint(point, selected);
      if (ptIndex !== -1) {
        if (view.hoveredLineIndex !== -1) {
          view.hoveredLineIndex = -1;
          draw();
        }
        if (view.hoveredPointIndex !== ptIndex) {
          view.hoveredPointIndex = ptIndex;
          draw();
        }
        canvas.style.cursor = "crosshair";
      } else {
        if (view.hoveredPointIndex !== -1) {
          view.hoveredPointIndex = -1;
          draw();
        }
        const lnIndex = hitTestLine(point, selected);
        if (lnIndex !== view.hoveredLineIndex) {
          view.hoveredLineIndex = lnIndex;
          draw();
        }
        if (lnIndex !== -1) {
          canvas.style.cursor = "pointer";
        }
      }
    } else if (view.hoveredLineIndex !== -1) {
      view.hoveredLineIndex = -1;
      draw();
    }
  }

  if (!view.drag) return;

  const end = imagePoint(point);
  if (view.drag.type === "draw-polygon") {
    view.drag.preview = end;
    view.drag.previewCanvas = point;
    
    if (event.buttons === 1) {
      const annotation = state.annotations.find((item) => item.id === view.drag.annotationId);
      if (annotation) {
        const pts = annotation.points || [];
        const lastPoint = pts[pts.length - 1];
        const threshold = 10 / view.imageBox.scale;
        if (lastPoint && Math.hypot(lastPoint.x - end.x, lastPoint.y - end.y) > threshold) {
          annotation.points.push({ x: round(end.x), y: round(end.y) });
          updateAnnotationBounds(annotation);
          view.drag.needsSave = true;
          if (view.drag.undonePoints) view.drag.undonePoints = [];
        }
      }
    }
    
    draw();
  } else if (view.drag.type === "draw" && state.mode === "draw") {
    const start = view.drag.draft.points?.[0] || { x: end.x, y: end.y };
    const x1 = Math.min(start.x, end.x);
    const y1 = Math.min(start.y, end.y);
    const x2 = Math.max(start.x, end.x);
    const y2 = Math.max(start.y, end.y);
    view.drag.draft.points = [
      { x: x1, y: y1 },
      { x: x2, y: y1 },
      { x: x2, y: y2 },
      { x: x1, y: y2 }
    ];
    view.drag.draft.x = x1;
    view.drag.draft.y = y1;
    view.drag.draft.width = Math.max(1, x2 - x1);
    view.drag.draft.height = Math.max(1, y2 - y1);
    draw();
  }

  if (view.drag.type === "move") {
    view.drag.originals.forEach(original => {
      const updated = {
        ...original,
        points: (original.points || annotationPoints(original)).map((item) => ({
          x: round(clamp(item.x + (end.x - view.drag.start.x), 0, view.imageElement.naturalWidth)),
          y: round(clamp(item.y + (end.y - view.drag.start.y), 0, view.imageElement.naturalHeight))
        }))
      };
      updateAnnotationBounds(updated);
      replaceAnnotation(updated);
    });
    render();
  }

  if (view.drag.type === "move-point") {
    const annotation = state.annotations.find((item) => item.id === view.drag.annotationId);
    if (annotation) {
      annotation.points[view.drag.pointIndex] = {
        x: round(clamp(end.x, 0, view.imageElement.naturalWidth)),
        y: round(clamp(end.y, 0, view.imageElement.naturalHeight))
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
  if (view.isPanning) {
    view.isPanning = false;
    canvas.style.cursor = "default";
    return;
  }
  if (view.drag?.type === "move-point") {
    view.drag = null;
    save();
    return;
  }

  if (view.drag?.type === "draw-polygon" && view.drag.needsSave) {
    view.drag.needsSave = false;
    save();
  }

  if (view.drag?.type === "move") {
    let changed = false;
    view.drag.originals.forEach(original => {
      const updated = state.annotations.find(a => a.id === original.id);
      if (updated && annotationChanged(original, updated)) {
        changed = true;
      }
    });
    view.drag = null;
    if (changed) {
      save();
    } else {
      state.history.pop();
    }
    render();
    return;
  }

  if (view.drag?.draft && view.drag.type === "draw" && state.mode === "draw") {
    if (state.shape === "box") {
      const start = view.drag.draft.points?.[0] || { x: 0, y: 0 };
      const end = view.drag.draft.points?.[2] || start;
      const x1 = Math.min(start.x, end.x);
      const y1 = Math.min(start.y, end.y);
      const x2 = Math.max(start.x, end.x);
      const y2 = Math.max(start.y, end.y);
      view.drag.draft.points = [
        { x: x1, y: y1 },
        { x: x2, y: y1 },
        { x: x2, y: y2 },
        { x: x1, y: y2 }
      ];
      view.drag.draft.x = x1;
      view.drag.draft.y = y1;
      view.drag.draft.width = Math.max(1, x2 - x1);
      view.drag.draft.height = Math.max(1, y2 - y1);

      snapshot();
      const annotation = {
        id: generateUUID(),
        labelId: view.drag.draft.labelId,
        points: view.drag.draft.points.map((point) => ({ x: round(point.x), y: round(point.y) }))
      };
      updateAnnotationBounds(annotation);
      state.annotations.push(annotation);
      state.selectedId = annotation.id;
      view.drag = null;
      render();
      save();
      return;
    } else if (state.shape === "magicWand") {
      const start = view.drag.draft.points?.[0] || { x: 0, y: 0 };
      const end = view.drag.draft.points?.[2] || start;
      const x1 = Math.min(start.x, end.x);
      const y1 = Math.min(start.y, end.y);
      const x2 = Math.max(start.x, end.x);
      const y2 = Math.max(start.y, end.y);

      view.drag = null;
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
  if (view.isPanning) {
    view.isPanning = false;
  }
  if (!view.drag) canvas.style.cursor = "default";
  if (view.hoveredLineIndex !== -1) {
    view.hoveredLineIndex = -1;
    draw();
  }
});

canvas.addEventListener("pointercancel", () => {
  if (view.isPanning) {
    view.isPanning = false;
    canvas.style.cursor = "default";
    return;
  }
  if (view.drag?.type === "draw-polygon") {
    const annotation = state.annotations.find((item) => item.id === view.drag.annotationId);
    if (annotation && (annotation.points || []).length < 3) {
      state.annotations = state.annotations.filter((item) => item.id !== annotation.id);
    }
  }
  view.drag = null;
  render();
});

window.addEventListener("keydown", (event) => {
  const target = event.target;
  const isTyping = target instanceof HTMLInputElement || target instanceof HTMLTextAreaElement;
  if (isTyping) return;

  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "z") {
    event.preventDefault();
    if (event.shiftKey) {
      redoAction();
    } else {
      undoAction();
    }
    return;
  }

  if (event.key === "Delete" || event.key === "Backspace") {
    event.preventDefault();
    if (state.selectedId) {
      // If hovering over a vertex, delete just that vertex
      if (view.hoveredPointIndex !== -1) {
        const selected = state.annotations.find(a => a.id === state.selectedId);
        if (selected && selected.points && selected.points.length > 3) {
          snapshot();
          selected.points.splice(view.hoveredPointIndex, 1);
          view.hoveredPointIndex = -1;
          updateAnnotationBounds(selected);
          render();
          save();
          setStatus("Vertex deleted");
          return;
        }
      }
      // If a line segment is selected on a polygon, delete just that segment
      if (view.selectedLineIndex !== -1) {
        const selected = state.annotations.find(a => a.id === state.selectedId);
        if (selected && selected.points && selected.points.length > 3) {
          snapshot();
          const nextIndex = (view.selectedLineIndex + 1) % selected.points.length;
          const toRemove = [view.selectedLineIndex, nextIndex].sort((a, b) => b - a);
          selected.points.splice(toRemove[0], 1);
          selected.points.splice(toRemove[1], 1);
          view.selectedLineIndex = -1;
          view.hoveredLineIndex = -1;
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
  if (event.key === "Enter") {
    if (view.drag?.type === "draw-polygon") {
      event.preventDefault();
      finalizePolygon();
    }
    return;
  }

  if (event.key === "Escape") {
    // If drawing a polygon, cancel and remove the incomplete annotation
    if (view.drag?.type === "draw-polygon") {
      const annotation = state.annotations.find((item) => item.id === view.drag.annotationId);
      if (annotation) {
        state.annotations = state.annotations.filter((item) => item.id !== annotation.id);
      }
    }
    state.selectedId = null;
    state.selectedIds.clear();
    view.selectedLineIndex = -1;
    view.hoveredLineIndex = -1;
    view.drag = null;
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

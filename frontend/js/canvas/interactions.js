import { generateUUID, clamp, round } from "../utils.js?v=3";
import { state, snapshot, isAnnotationHidden, labelDisplayName } from "../state.js?v=4";
import {
  annotationPoints,
  updateAnnotationBounds,
  pointInPolygon,
  isPointInsideOtherGroupPolygons,
  addPolygonPointResolvingIntersections,
  appendEvenlySpacedPoints,
  resolvePolygonClosingIntersections,
  resolveClosedPolygonIntersections,
  splitClosedPolygonAtIntersections,
  polygonsTouch,
  unionPolygons,
  smoothUnionCusps
} from "./geometry.js?v=9";
import { view } from "./view.js?v=3";
import { draw, drawAllLayers } from "./draw.js?v=6";
import { canvas, undoButton } from "../dom.js?v=2";
import { commentOverlayRefs } from "../comment-overlay.js?v=1";
import { setStatus, save, render, activateLabel, HOTKEY_LABEL_LIMIT } from "../components/workspace.js?v=12";
import { performMagicWandSegmentation } from "../ai/detect.js?v=2";
import { applyAutoSmooth } from "../fft-controls.js?v=1";
import { annotationSettings, vertexGrabScreenRadius } from "../feature-flags.js?v=10";

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
  // Same curve as the drawn handle in draw.js (currently a constant on-screen
  // size), capped in image space so a click when zoomed out cannot swallow
  // every nearby vertex, then divided by scale to convert the on-screen pixel
  // radius into image space, where the comparison happens.
  const screenRadius = vertexGrabScreenRadius(
    annotationSettings.vertexGrabRadius, view.viewZoom, view.imageBox.scale);
  const threshold = screenRadius / view.imageBox.scale;
  const groupAnns = annotation.groupId ? state.annotations.filter(a => a.groupId === annotation.groupId) : null;
  for (let i = 0; i < annotation.points.length; i++) {
    const pt = annotation.points[i];
    if (groupAnns && isPointInsideOtherGroupPolygons(pt, annotation, groupAnns)) continue;
    if (Math.hypot(pt.x - img.x, pt.y - img.y) < threshold) {
      return i;
    }
  }
  return -1;
}

export function hitTestLine(point, annotation) {
  if (!annotation || !annotation.points || annotation.points.length < 3) return -1;
  const img = imagePoint(point);
  // Screen pixels -> image space; see hitTestPoint above.
  const threshold = annotationSettings.edgeGrabRadius / view.imageBox.scale;
  const pts = annotation.points;
  const groupAnns = annotation.groupId ? state.annotations.filter(a => a.groupId === annotation.groupId) : null;
  for (let i = 0; i < pts.length; i++) {
    const p1 = pts[i];
    const p2 = pts[(i + 1) % pts.length];

    const l2 = (p1.x - p2.x) ** 2 + (p1.y - p2.y) ** 2;
    if (l2 === 0) continue;

    let t = ((img.x - p1.x) * (p2.x - p1.x) + (img.y - p1.y) * (p2.y - p1.y)) / l2;
    t = Math.max(0, Math.min(1, t));

    const projX = p1.x + t * (p2.x - p1.x);
    const projY = p1.y + t * (p2.y - p1.y);

    if (groupAnns && isPointInsideOtherGroupPolygons({x: projX, y: projY}, annotation, groupAnns)) continue;

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

export function setCanvasCursor(cursor) {
  if (canvas && canvas.style.cursor !== cursor) {
    canvas.style.cursor = cursor;
  }
}

// `shiftSelect` mirrors the pointerdown gate: Shift (without Alt, which is the
// pan gesture) turns a draw-mode click into a selection, so the cursor has to
// say "pointer" over a shape rather than promise a crosshair that will not draw.
export function updateCanvasCursor(point, shiftSelect = false) {
  if (!view.imageLoaded) {
    setCanvasCursor("default");
    return;
  }

  if (view.drag) {
    if (view.drag.type === "move-shape") {
      setCanvasCursor("grabbing");
      return;
    }
    if (view.drag.type === "draw-polygon" || view.drag.type === "draw" || view.drag.type === "move-point") {
      setCanvasCursor("crosshair");
      return;
    }
  }

  if (state.mode === "select") {
    if (state.selectedId) {
      const selected = state.annotations.find((a) => a.id === state.selectedId);
      if (selected && hitTestPoint(point, selected) !== -1) {
        setCanvasCursor("crosshair");
        return;
      }
      if (selected && selected.points && selected.points.length >= 3 && hitTestLine(point, selected) !== -1) {
        setCanvasCursor("pointer");
        return;
      }
    }
    const hoverId = hitTest(point);
    if (hoverId) {
      // With Move Objects unlocked, hovering the interior of an already-selected
      // shape promises a drag, so show "move". Otherwise a finished annotation
      // can only be selected, and "pointer" is the honest cursor.
      if (state.moveObjectsUnlocked && state.selectedIds.has(hoverId)) {
        setCanvasCursor("move");
      } else {
        setCanvasCursor("pointer");
      }
      return;
    }
    setCanvasCursor("default");
    return;
  }

  if (state.mode === "draw") {
    if (state.needsLabelSelection || shiftSelect) {
      const hoverId = hitTest(point);
      setCanvasCursor(hoverId ? "pointer" : "default");
      return;
    }
    setCanvasCursor("crosshair");
    return;
  }

  setCanvasCursor("default");
}

// Drop the selection left over from a completed shape. Called on the first canvas
// click after finalizing, so the vertex handles disappear and a subsequent class
// click applies to the next annotation instead of re-labelling the finished one.
function clearSelectionAfterFinalize() {
  if (!state.selectedId && state.selectedIds.size === 0) return;
  state.selectedId = null;
  state.selectedIds.clear();
  view.selectedLineIndex = -1;
  view.hoveredLineIndex = -1;
  render();
}

// --- Sticky-class hover selection -------------------------------------------
// In sticky class mode a finished polygon is not dropped straight back into a
// pure drawing state. It stays "hover-armed": the pointer inside its boundary
// selects it (select mode, handles shown) so it can be corrected immediately,
// and the pointer leaving the boundary puts the canvas back in draw mode so the
// next click begins the next polygon in the same class. That keeps the
// one-click-per-shape flow sticky class exists for while making the common
// "I need to nudge that last vertex" fix free of a mode switch.

/** Start hover-arming `annotationId` (sticky class only). */
function armStickyHover(annotationId) {
  view.stickyHoverId = annotationId;
  // The pointer has not moved since the closing click, so it is still inside the
  // shape: enter the selected state immediately rather than waiting for the
  // first pointermove, which may never come if the annotator reaches for a key.
  view.stickyHoverInside = true;
  state.mode = "select";
  state.selectedIds.clear();
  state.selectedIds.add(annotationId);
  state.selectedId = annotationId;
}

/** Drop any hover-arming, leaving selection and mode untouched. */
export function clearStickyHover() {
  view.stickyHoverId = null;
  view.stickyHoverInside = false;
}

/**
 * Re-evaluate the hover-armed polygon against the cursor. Called from
 * pointermove before anything else reads state.mode, so the click that follows
 * a move sees the mode the cursor position implies.
 * Returns true when the selection or mode changed (caller re-renders).
 */
function updateStickyHover(point) {
  if (!view.stickyHoverId) return false;
  // Any in-progress drag owns the interaction: a vertex being dragged out of the
  // shape must not deselect it mid-gesture, and a new polygon already disarmed.
  if (view.drag) return false;
  if (!state.stickyClass) {
    // The toggle can be flipped between shapes; honour it without stranding the
    // canvas in select mode.
    clearStickyHover();
    return false;
  }
  const annotation = state.annotations.find((item) => item.id === view.stickyHoverId);
  if (!annotation) {
    // Deleted (or undone) while armed.
    clearStickyHover();
    return false;
  }
  // hitTest returns the topmost annotation, which may be a different shape
  // overlapping this one; only this polygon's own boundary arms the selection.
  const inside = pointInPolygon(imagePoint(point), annotationPoints(annotation));
  if (inside === view.stickyHoverInside) return false;
  view.stickyHoverInside = inside;
  if (inside) {
    state.mode = "select";
    state.selectedIds.clear();
    state.selectedIds.add(annotation.id);
    state.selectedId = annotation.id;
  } else {
    state.selectedId = null;
    state.selectedIds.clear();
    view.selectedLineIndex = -1;
    view.hoveredLineIndex = -1;
    view.hoveredPointIndex = -1;
    // Back to drawing the same class. The label gate is only re-armed when no
    // class is actually armed, mirroring the justFinalized block in pointerdown.
    if (!state.needsLabelSelection && state.activeLabelId) {
      state.mode = "draw";
    }
  }
  return true;
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
  // Clean up any closing edge self-intersections while preserving primary shape
  if (annotation.points && annotation.points.length >= 4) {
    const cleaned = resolvePolygonClosingIntersections(annotation.points);
    if (cleaned && cleaned.length >= 3) {
      annotation.points = cleaned;
    }
  }
  // Auto-smooth: apply FFT low-pass filter when the toggle is enabled.
  // Called before updateAnnotationBounds so the bounds reflect the smoothed points.
  applyAutoSmooth(annotation);
  updateAnnotationBounds(annotation);
  // Sticky class: the finished polygon keeps its label and the same class stays
  // armed, so the next polygon can start on the very next click without a trip
  // to the class panel. Without it, re-arm the gate as before.
  state.needsLabelSelection = !state.stickyClass;
  state.justFinalized = true;
  // Arm post-finalize vertex trimming: the next Ctrl+Z shaves this polygon's
  // last vertex rather than deleting the shape outright (undoLastFinalizedPoint).
  view.lastFinalizedPolygonId = annotation.id;
  // Closing a polygon drops into select mode with the finished shape selected,
  // so its vertices and label are immediately editable instead of the canvas
  // still being armed to draw. The next single click restores draw mode (see
  // the justFinalized block in pointerdown) without placing a vertex.
  //
  // Sticky class is deliberately exempt: there the whole point is that the next
  // polygon starts on the very next click, and bouncing through select mode
  // would cost an extra click per shape.
  if (!state.stickyClass) {
    state.mode = "select";
    state.selectedIds.clear();
    state.selectedIds.add(annotation.id);
    state.selectedId = annotation.id;
  } else {
    // Hover-arm the finished polygon: while the pointer stays inside it, it is
    // selected and editable (vertex drag, edge split, move); the moment the
    // pointer crosses its boundary the selection is released and the next click
    // starts a new polygon in the same class. See armStickyHover/updateStickyHover.
    armStickyHover(annotation.id);
  }
  render();
  save();
  setStatus(state.stickyClass ? "Polygon saved — keep drawing" : "Polygon saved — click to draw again");
}

// Point-level undo/redo for an in-progress (not yet finalized) polygon.
// Deliberately kept OUT of the state.history/redoHistory snapshot stack:
// snapshotting every single point-click there would flood the 50-entry
// history with one entry per vertex, so drawing a long freehand/click
// polygon would blow undo history for everything else in the session and
// (worse) collapse into "one Ctrl+Z removes dozens of points" once older
// entries got shifted out. Instead, in-progress points are tracked on the
// ephemeral view.drag.undonePoints array, one pop/push per point, and only
// the *finalized* polygon (or its deletion) becomes a real history entry.
//
// undonePoints must survive exactly as long as the polygon it belongs to.
// It is intentionally NOT cleared by undoAction/redoAction's history-restore
// path so that undoing past the first vertex (which falls through to
// deleting the whole in-progress annotation via history) can still be
// redone point-by-point afterwards — see redoAction below.
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

// Post-finalize vertex trimming. Closing a polygon used to mean the very next
// Ctrl+Z threw the whole shape away, which is the wrong reading of "undo" right
// after drawing: the annotator almost always means "that last vertex was
// wrong", not "scrap the polygon". So while view.lastFinalizedPolygonId still
// points at the polygon that was just closed, Ctrl+Z pops its last vertex.
//
// Unlike undoLastPoint (in-progress drawing, deliberately history-free because
// it runs once per click and would flood the 50-entry stack), each trim here
// DOES take a real snapshot: they are a handful of deliberate presses, and
// routing them through history is what lets Ctrl+Shift+Z put the vertex back.
//
// Trimming stops at 3 points — below that it is no longer a polygon — so the
// press that would take it to 2 falls through to ordinary history undo and
// removes the shape, which is the behaviour the annotator is now asking for.
function undoLastFinalizedPoint() {
  if (!view.lastFinalizedPolygonId) return false;
  if (view.drag?.type === "draw-polygon") return false;
  const annotation = state.annotations.find((item) => item.id === view.lastFinalizedPolygonId);
  if (!annotation || annotation.type !== "polygon") {
    view.lastFinalizedPolygonId = null;
    return false;
  }
  if (!annotation.points || annotation.points.length <= 3) return false;

  snapshot();
  annotation.points.pop();
  updateAnnotationBounds(annotation);
  // A trimmed vertex can invalidate a hovered/selected vertex or edge index.
  view.hoveredPointIndex = -1;
  view.hoveredLineIndex = -1;
  view.selectedLineIndex = -1;
  render();
  save();
  setStatus(`Vertex removed — ${annotation.points.length} points`);
  return true;
}

export function undoAction() {
  if (undoLastPoint()) {
    return;
  }
  if (undoLastFinalizedPoint()) {
    return;
  }
  // Any undo that isn't a vertex trim means we've left the just-closed polygon
  // behind; don't let a later press resume trimming it.
  view.lastFinalizedPolygonId = null;
  // Falling through here means either there's no in-progress polygon, or its
  // last remaining vertex is about to be undone away (undoLastPoint requires
  // length > 1, so a 1-point polygon reaches this branch). That vertex, and
  // any points still parked in view.drag.undonePoints from earlier point-undos,
  // need to travel together into the same history entry so a later redo can
  // bring the whole in-progress polygon back in one step instead of losing
  // everything past the first point. Stash them on the annotation itself
  // (a plain data field, harmless to serialize) before snapshotting.
  const pendingDrag = view.drag?.type === "draw-polygon" ? view.drag : null;
  let carryAnnotation = null;
  if (pendingDrag?.undonePoints?.length > 0) {
    carryAnnotation = state.annotations.find((item) => item.id === pendingDrag.annotationId);
  }
  if (carryAnnotation) {
    carryAnnotation.__pendingUndonePoints = pendingDrag.undonePoints.slice();
  }

  const previous = state.history.pop();
  if (!previous) {
    if (carryAnnotation) delete carryAnnotation.__pendingUndonePoints;
    return;
  }

  state.redoHistory.push(JSON.stringify({
    annotations: state.annotations,
    selectedId: state.selectedId
  }));

  if (carryAnnotation) delete carryAnnotation.__pendingUndonePoints;

  const restored = JSON.parse(previous);
  if (restored.annotations) {
    state.annotations = restored.annotations;
  }
  if (restored.selectedId !== undefined) {
    state.selectedId = restored.selectedId;
  }

  if (view.drag?.type === "draw-polygon") {
    const exists = state.annotations.some((item) => item.id === view.drag.annotationId);
    if (!exists) {
      view.drag = null;
    } else {
      // The polygon this drag refers to still exists after the restore
      // (we undid something else, not the polygon's own creation) — keep
      // whatever point-undo buffer it already had.
    }
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
    annotations: state.annotations,
    selectedId: state.selectedId
  }));

  const restored = JSON.parse(next);
  if (restored.annotations) {
    state.annotations = restored.annotations;
  }
  if (restored.selectedId !== undefined) {
    state.selectedId = restored.selectedId;
  }

  if (view.drag?.type === "draw-polygon") {
    const exists = state.annotations.some((item) => item.id === view.drag.annotationId);
    if (!exists) view.drag = null;
  }

  // If the redone state brought back an in-progress polygon that still had
  // points buffered from an earlier point-level undo (stashed on
  // __pendingUndonePoints by undoAction just before it snapshotted), restore
  // that buffer so redo can keep walking forward point-by-point instead of
  // stopping dead after this one step. This can also be the step that brings
  // the polygon itself back into existence (its creation was undone), in
  // which case view.drag was nulled out and needs to be re-pointed at it.
  const revivedAnnotation = state.annotations.find(
    (item) => item.__pendingUndonePoints?.length > 0
  );
  if (revivedAnnotation) {
    if (view.drag?.type !== "draw-polygon" || view.drag.annotationId !== revivedAnnotation.id) {
      view.drag = { type: "draw-polygon", annotationId: revivedAnnotation.id };
    }
    view.drag.undonePoints = revivedAnnotation.__pendingUndonePoints;
    delete revivedAnnotation.__pendingUndonePoints;
    state.selectedId = revivedAnnotation.id;
  }

  render();
  save();
}

// Ratio of one zoom notch, for both the wheel and the +/- buttons.
//
// Lives here, next to setZoom, and is imported by the zoom control rather than
// being written out in each place: the two were previously separate copies of
// 1.1, which is exactly the kind of pair that drifts apart and leaves the
// buttons zooming at a different rate than the wheel.
//
// 1.1 is a ~10% change per notch. This was briefly halved to 1.05 to make
// fine positioning easier, and that was reverted: at 1.05 it takes roughly 28
// notches to get from fit to 4x instead of 15, and annotators experience that
// as the zoom being slow rather than as being precise. Getting to the region
// of interest is the common action and it has to stay quick; fine adjustment
// is the rare one. Do not lower this again without a way to keep the coarse
// approach fast -- a modifier key for fine steps, or acceleration on repeat.
export const ZOOM_STEP = 1.1;

// The zoom readout subscribes here rather than being imported directly: the
// component already imports setZoom, so a direct import would be circular.
let onZoomChange = null;
export function setZoomChangeHandler(fn) {
  onZoomChange = fn;
}

export function setZoom(newZoom, mouseX, mouseY) {
  if (!view.imageLoaded) return;
  const oldZoom = view.viewZoom;
  view.viewZoom = Math.max(0.1, Math.min(500, newZoom));

  const rect = canvas.getBoundingClientRect();
  const cx = mouseX !== undefined ? mouseX : rect.width / 2;
  const cy = mouseY !== undefined ? mouseY : rect.height / 2;

  // view.baseScale is written by computeImageBox on every draw. It matches
  // the contain-fit formula exactly so zoom-at-cursor never drifts.
  const baseScale = view.baseScale;
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
  // A delete is a new edit; the next Ctrl+Z must undo it, not resume trimming
  // vertices off the polygon that was closed before it.
  view.lastFinalizedPolygonId = null;
  // If deleting the polygon being drawn, clean up view.drag state
  if (view.drag?.type === "draw-polygon" && state.selectedIds.has(view.drag.annotationId)) {
    view.drag = null;
  }
  // Drop visibility state for the ids going away, so the set does not grow
  // unboundedly across a session.
  state.selectedIds.forEach((id) => state.hiddenAnnotationIds.delete(id));
  // Deleting the hover-armed polygon ends the arming and hands the canvas back
  // to drawing, rather than leaving select mode with nothing selected.
  if (view.stickyHoverId && state.selectedIds.has(view.stickyHoverId)) {
    clearStickyHover();
    if (state.stickyClass && state.activeLabelId) state.mode = "draw";
  }
  state.annotations = state.annotations.filter((item) => !state.selectedIds.has(item.id));
  state.selectedIds.clear();
  state.selectedId = null;
  // The pending shape may be the one just deleted; there is nothing left to
  // release or to label.
  state.justFinalized = false;
  state.needsLabelSelection = false;
  view.selectedLineIndex = -1;
  view.hoveredLineIndex = -1;
  view.hoveredPointIndex = -1;
  render();
  save();
}

// --- Z-order (stacking) --------------------------------------------------
//
// Annotations paint in state.annotations array order (later = on top), and
// hitTest() walks the array backwards, so the topmost-painted shape wins a
// click. Reordering the array is therefore the whole feature: it changes both
// what is drawn on top and what is selectable first, and — because the array
// is persisted verbatim by save() — it survives reloads with no schema change.
//
// The selected annotations move as a single contiguous block, preserving
// their relative order, so a multi-select or a group never gets interleaved
// with the shapes it passes.

// Split state.annotations into the selected block (relative order kept) and
// the unselected remainder. Returns null when there is nothing to move.
function partitionBySelection() {
  if (state.selectedIds.size === 0) return null;
  const selected = [];
  const others = [];
  state.annotations.forEach((a) => {
    if (state.selectedIds.has(a.id)) selected.push(a);
    else others.push(a);
  });
  if (selected.length === 0) return null;
  return { selected, others };
}

// Commit a new array order and persist it, mirroring every other mutating
// action in this file (snapshot already taken by the caller).
function applyReorder(next) {
  state.annotations = next;
  render();
  save();
}

export function sendToBack() {
  const parts = partitionBySelection();
  if (!parts) return;
  // Already at the very back: the selected block occupies indices 0..n-1.
  const alreadyBack = state.annotations
    .slice(0, parts.selected.length)
    .every((a) => state.selectedIds.has(a.id));
  if (alreadyBack) return;
  snapshot();
  applyReorder([...parts.selected, ...parts.others]);
  setStatus("Sent back");
}

export function bringToFront() {
  const parts = partitionBySelection();
  if (!parts) return;
  const n = parts.selected.length;
  const alreadyFront = state.annotations
    .slice(state.annotations.length - n)
    .every((a) => state.selectedIds.has(a.id));
  if (alreadyFront) return;
  snapshot();
  applyReorder([...parts.others, ...parts.selected]);
  setStatus("Brought forward");
}

// Move the selected block one step towards index 0 (visually backwards): drop
// it just before the nearest unselected neighbour above it. No-op when the
// block is already at the back.
export function sendBackward() {
  const parts = partitionBySelection();
  if (!parts) return;
  // Index in the full array of the first selected annotation.
  const firstSel = state.annotations.findIndex((a) => state.selectedIds.has(a.id));
  if (firstSel <= 0) return; // already at the back
  // Insert the block one position earlier among the unselected remainder.
  const insertAt = firstSel - 1;
  snapshot();
  const next = [...parts.others];
  next.splice(insertAt, 0, ...parts.selected);
  applyReorder(next);
  setStatus("Sent backward");
}

// Move the selected block one step towards the end (visually forwards): drop
// it just after the nearest unselected neighbour below it. No-op when the
// block is already at the front.
export function bringForward() {
  const parts = partitionBySelection();
  if (!parts) return;
  let lastSel = -1;
  for (let i = state.annotations.length - 1; i >= 0; i -= 1) {
    if (state.selectedIds.has(state.annotations[i].id)) { lastSel = i; break; }
  }
  if (lastSel === -1 || lastSel >= state.annotations.length - 1) return; // already at front
  // Count how many unselected shapes precede the block; the block should land
  // one unselected neighbour further forward than where it starts.
  let othersBefore = 0;
  for (let i = 0; i < state.annotations.length; i += 1) {
    if (state.selectedIds.has(state.annotations[i].id)) break;
    othersBefore += 1;
  }
  const insertAt = othersBefore + 1;
  snapshot();
  const next = [...parts.others];
  next.splice(insertAt, 0, ...parts.selected);
  applyReorder(next);
  setStatus("Brought forward");
}

const groupButton = document.querySelector("#groupButton");
if (groupButton) {
  groupButton.addEventListener("click", () => {
    groupSelectedAnnotations();
  });
}

/**
 * The pre-merge shapes recorded on a merged polygon, or null if it is not a
 * merge survivor. Like `mergedFromGroup`, the field round-trips through the
 * server's `extra` blob, so after a reload it arrives nested rather than as a
 * top-level property — both spellings are accepted.
 */
function mergedPartsOf(annotation) {
  const parts = annotation?.mergedParts || annotation?.extra?.mergedParts;
  return Array.isArray(parts) && parts.length > 1 ? parts : null;
}

/**
 * A deep-enough copy of an annotation to restore it later: points are cloned so
 * a later edit of the merged shape cannot reach back and mutate the record.
 */
function snapshotAnnotation(annotation) {
  const copy = { ...annotation, points: annotationPoints(annotation) };
  // Not carried into the record: these describe the merged survivor, not the
  // part, and keeping them would make a restored part claim to be a merge.
  delete copy.mergedFromGroup;
  delete copy.mergedParts;
  delete copy.groupId;
  if (copy.extra) {
    copy.extra = { ...copy.extra };
    delete copy.extra.mergedFromGroup;
    delete copy.extra.mergedParts;
  }
  return copy;
}

export function groupSelectedAnnotations() {
  if (state.selectedIds.size <= 1) return;

  snapshot();

  const selectedList = state.annotations.filter(a => state.selectedIds.has(a.id) && a.type !== "comment");
  if (selectedList.length <= 1) {
    state.history.pop();
    return;
  }

  // Captured up front: the base annotation may itself be absorbed by a merge.
  const baseLabelId = selectedList[0].labelId;

  // Shapes that touch each other are merged into a single polygon: the union
  // outline replaces them and the vertices buried in the overlap are discarded,
  // so the result is one real object rather than several overlapping ones.
  const clusters = clusterTouchingAnnotations(selectedList);
  const merged = [];
  const consumed = new Set();
  let refusedMerge = false;

  for (const cluster of clusters) {
    if (cluster.length < 2) continue;
    const outline = unionPolygons(cluster.map(a => annotationPoints(a)));
    if (!outline || outline.length < 3) {
      // The shapes touch but their union is not a single simple region (they
      // meet only at a point, or enclose a hole). Merging would distort them,
      // so they are left as they are and only grouped.
      refusedMerge = true;
      continue;
    }

    // Round the cusps left where the shapes crossed. Only sharp turns between
    // short segments (a traced curve) are affected; corners drawn as straight
    // edges — a merged box's 90° corner — are left exactly as they are.
    const smoothed = smoothUnionCusps(outline);

    const survivor = cluster[0];
    // Captured before the survivor is overwritten: Ungroup restores these to
    // undo the merge, so they are the shapes exactly as they were drawn. Nested
    // merges flatten — a member that was itself merged contributes its own
    // parts, so one Ungroup always returns to the original shapes rather than
    // to an intermediate union.
    const preMergeParts = cluster.flatMap(a => mergedPartsOf(a) || [snapshotAnnotation(a)]);

    survivor.type = "polygon";
    survivor.points = smoothed.map(p => ({ x: round(p.x), y: round(p.y) }));
    survivor.labelId = baseLabelId;
    // Rendering hint only: the union's cusps are stroked with round joins so the
    // merged shape reads as smoothly as the old grouped rendering did. The
    // points above are the exact union and are not softened.
    survivor.mergedFromGroup = true;
    survivor.mergedParts = preMergeParts;
    if (survivor.extra) delete survivor.extra.mergedParts;
    delete survivor.groupId;
    updateAnnotationBounds(survivor);

    cluster.slice(1).forEach(a => consumed.add(a.id));
    merged.push(survivor);
  }

  if (consumed.size) {
    state.annotations = state.annotations.filter(a => !consumed.has(a.id));
    consumed.forEach(id => state.selectedIds.delete(id));
  }

  // Anything left over (shapes that never touched) keeps the visual group so the
  // existing grouped-selection behaviour is preserved.
  const remaining = state.annotations.filter(a => state.selectedIds.has(a.id) && a.type !== "comment");
  const stillSeparate = remaining.filter(a => !merged.includes(a));

  if (remaining.length > 1 && stillSeparate.length) {
    const groupId = generateUUID();
    remaining.forEach(a => {
      a.groupId = groupId;
      a.labelId = baseLabelId;
    });
  } else {
    remaining.forEach(a => { a.labelId = baseLabelId; });
  }

  if (!consumed.size && remaining.length <= 1) {
    state.history.pop();
    return;
  }

  render();
  save();

  // A refusal leaves the shapes separate and merely grouped, which looks very
  // like a completed merge — the outline is painted as one blob either way. Say
  // so explicitly, including when only part of the selection merged, so the
  // annotator is never left believing shapes were combined when they were not.
  if (refusedMerge) {
    setStatus(consumed.size
      ? "Partly merged — some shapes could not be combined into one outline"
      : "Grouped only — these shapes could not be combined into one outline");
  } else {
    setStatus(consumed.size ? "Merged" : "Grouped");
  }
}

/**
 * Partitions annotations into clusters of shapes that touch or overlap, directly
 * or transitively (A touches B, B touches C -> one cluster).
 */
function clusterTouchingAnnotations(annotations) {
  const shapes = annotations.map(a => ({ annotation: a, points: annotationPoints(a) }));
  const parent = shapes.map((_, i) => i);

  const find = (i) => {
    while (parent[i] !== i) {
      parent[i] = parent[parent[i]];
      i = parent[i];
    }
    return i;
  };

  for (let i = 0; i < shapes.length; i++) {
    for (let j = i + 1; j < shapes.length; j++) {
      if (shapes[i].points.length < 3 || shapes[j].points.length < 3) continue;
      if (!polygonsTouch(shapes[i].points, shapes[j].points)) continue;
      const rootI = find(i);
      const rootJ = find(j);
      if (rootI !== rootJ) parent[rootJ] = rootI;
    }
  }

  const groups = new Map();
  shapes.forEach((shape, i) => {
    const root = find(i);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root).push(shape.annotation);
  });

  return [...groups.values()];
}

/**
 * Reverses the last Group on the selection. Two things can need undoing, and a
 * selection may contain both:
 *
 *  - a merged polygon, which is replaced by the shapes it was built from;
 *  - a plain visual group (shapes that never touched, so were only linked),
 *    which is unlinked.
 *
 * Returns true if anything changed. The caller owns the undo snapshot.
 */
export function ungroupSelectedAnnotations() {
  let restoredParts = 0;
  let unlinked = false;

  // Indexed walk over a copy: restoring splices new annotations in beside the
  // survivor, and the selection is rebuilt to hold the restored shapes.
  const targets = state.annotations.filter(a => state.selectedIds.has(a.id));

  targets.forEach(survivor => {
    const parts = mergedPartsOf(survivor);
    if (!parts) {
      if (survivor.groupId) {
        delete survivor.groupId;
        unlinked = true;
      }
      return;
    }

    // The merged shape may have been dragged since it was created. Moves
    // translate every point by one delta, so replaying that delta onto the
    // stored parts puts them back under the outline the annotator is looking
    // at instead of at the original coordinates.
    const origin = mergedPartsOrigin(parts);
    const dx = round((Number(survivor.x) || 0) - origin.x);
    const dy = round((Number(survivor.y) || 0) - origin.y);

    const restored = parts.map((part, index) => {
      const shape = {
        ...part,
        // The survivor keeps its own id so anything else referring to it (the
        // annotation list, the draft) still resolves; the rest are new objects.
        id: index === 0 ? survivor.id : generateUUID(),
        points: (part.points || []).map(p => ({ x: round(p.x + dx), y: round(p.y + dy) }))
      };
      updateAnnotationBounds(shape);
      return shape;
    });

    const at = state.annotations.indexOf(survivor);
    state.annotations.splice(at, 1, ...restored);

    state.selectedIds.delete(survivor.id);
    restored.forEach(shape => state.selectedIds.add(shape.id));
    restoredParts += restored.length;
  });

  if (restoredParts) {
    // The restored shapes are independent objects, so the single-selection
    // pointer must not keep naming the survivor alone.
    state.selectedId = null;
    setStatus(`Merge undone — ${restoredParts} shapes restored`);
  } else if (unlinked) {
    setStatus("Ungrouped");
  }

  return restoredParts > 0 || unlinked;
}

/**
 * Top-left corner of the stored parts, in the coordinates they were recorded
 * in. Compared against the merged survivor's current corner to recover how far
 * the merged shape has been moved since.
 */
function mergedPartsOrigin(parts) {
  let x = Infinity;
  let y = Infinity;
  parts.forEach(part => {
    (part.points || []).forEach(p => {
      if (Number(p.x) < x) x = Number(p.x);
      if (Number(p.y) < y) y = Number(p.y);
    });
  });
  return { x: Number.isFinite(x) ? x : 0, y: Number.isFinite(y) ? y : 0 };
}

const ungroupButton = document.querySelector("#ungroupButton");
if (ungroupButton) {
  ungroupButton.addEventListener("click", () => {
    snapshot();
    if (ungroupSelectedAnnotations()) {
      render();
      save();
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
  const zoomFactor = event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
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
    setCanvasCursor("grabbing");
    return;
  }

  const point = canvasPoint(event);

  // Any click that isn't a pan means the annotator has moved on from the polygon
  // they just closed — starting the next shape, or editing something. Post-
  // finalize vertex trimming is a keyboard-only gesture (Ctrl+Z straight after
  // closing), so disarming it here keeps a later Ctrl+Z undoing whatever this
  // click does rather than quietly shaving a vertex off an older shape.
  view.lastFinalizedPolygonId = null;

  // First click after finalizing a shape. The shape is still selected so a class
  // click can label it; this click releases that selection, so picking a class for
  // the next shape cannot re-label the finished one. Consumed immediately — every
  // later click must fall through to the normal editing blocks below.
  // With sticky class there is no pending class pick to protect, so this click
  // must not be swallowed — it is the first vertex/corner of the next shape.
  // Just drop the finished shape's selection and fall through.
  // Click while the hover-armed polygon is under the cursor: this is an edit of
  // that polygon, not the start of the next one. Leave the selection and select
  // mode in place and fall through to the vertex/edge/move blocks below.
  if (view.stickyHoverId && view.stickyHoverInside) {
    state.justFinalized = false;
  } else if (state.justFinalized && state.stickyClass && state.mode === "draw") {
    // Pointer is outside the armed polygon (or nothing is armed): the arming is
    // spent and this click is the first vertex of the next shape.
    clearStickyHover();
    state.justFinalized = false;
    // ...unless Shift is held, which makes this a multi-select click. The shape
    // just finished is the one an annotator most often wants as the first member
    // of the group, so dropping its selection here would defeat the gesture
    // before it started. Shift is additive everywhere else; keep it additive here.
    if (!(event.shiftKey && !event.altKey)) clearSelectionAfterFinalize();
  } else if (state.justFinalized) {
    clearStickyHover();
    state.justFinalized = false;
    const hitId = hitTest(point);
    // Clicking the finished shape itself keeps it selected; anything else releases
    // it. Either way we fall through, so this click behaves like a Select-mode
    // click: vertex add/delete, point drag and move all work from here on.
    if (!hitId || !state.selectedIds.has(hitId)) {
      clearSelectionAfterFinalize();
      if (!hitId) {
        // Empty canvas: this click re-arms drawing rather than doing nothing.
        // finalizePolygon parked the canvas in select mode so the finished shape
        // stayed editable; one click on empty space says "done editing, draw the
        // next one". Deliberately places no vertex — the click spends itself on
        // the mode switch, and the polygon starts on the following click.
        //
        // Gated on the label gate: with no class armed, entering draw mode would
        // only produce "Select class" on the next click, so staying in select
        // mode is the more honest state.
        if (!state.needsLabelSelection && state.activeLabelId) {
          state.mode = "draw";
          setStatus("Draw mode");
        }
        render();
        return;
      }
    }
  }

  // Left-click on a polygon edge to add a vertex (select mode only).
  // Skipped while drawing: hitTestLine treats the shape as already closed, so it
  // reports a phantom edge from the last vertex back to the first. Splitting it
  // would start a move-point view.drag and silently end the in-progress polygon.
  if (state.mode === "select" && state.selectedId && event.button === 0 && !event.altKey && view.drag?.type !== "draw-polygon") {
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
  // Vertex/edge deletion (select mode only) — also skipped while drawing, so an Alt+click cannot
  // remove points from the polygon currently being placed.
  if (state.mode === "select" && state.selectedId && (event.altKey || event.button === 2) && view.drag?.type !== "draw-polygon") {
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
          // Requires 4 points, not the 3 the outer guard allows. At 3, the
          // click landing in vertex 0's grab radius means the shape is a
          // triangle whose free end is back at its own start — a degenerate
          // sliver, not something an annotator set out to draw. finalizePolygon
          // would accept it (its own floor is 3), so the floor has to be here.
          // Below 4, fall through and let the click place a point instead.
          if (ptIndex === 0 && selected.points.length >= 4) {
            finalizePolygon();
            return;
          }
        } else if (state.mode === "select") {
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
  // target, so selecting it would clear view.drag and end the shape. state.mode
  // is not sufficient here — line 3286 can leave it as "select" before drawing ever starts.
  // The inert post-finalize state is included even though state.mode is still
  // "draw": no new shape can be started until a class is picked, so a click on an
  // existing annotation should select it rather than do nothing.
  //
  // Shift+click is included for the same reason. Sticky class keeps the canvas
  // in "draw" indefinitely — that is the whole point of it — so without this the
  // multi-select gesture below is unreachable for exactly the annotators who
  // draw the most shapes, and a Shift+click silently drops another vertex
  // instead. Shift is unambiguous here: plain clicks still draw, and the
  // Shift+Alt pan gesture was already consumed at the top of this handler.
  const shiftSelect = event.shiftKey && !event.altKey;
  if ((state.mode !== "draw" || state.needsLabelSelection || shiftSelect) && view.drag?.type !== "draw-polygon") {
    const hitId = hitTest(point);
    if (hitId) {
      // Move Objects unlocked: clicking an already-selected annotation (without
      // Shift, i.e. not a multi-select gesture) grabs the whole selection to
      // drag it. Vertex/edge hits were already handled above, so this only
      // fires on the shape's interior. Locked, or clicking an unselected shape,
      // falls through to plain selection below.
      if (state.moveObjectsUnlocked && !event.shiftKey && state.selectedIds.has(hitId)) {
        const moving = state.annotations.filter((a) => state.selectedIds.has(a.id));
        // snapshot() is deferred to the first move (see pointermove) so a plain
        // click on a selected shape doesn't push a no-op undo entry.
        view.drag = {
          type: "move-shape",
          moved: false,
          start: imagePoint(point),
          // Deep-copied pre-drag geometry, keyed by id. pointermove rewrites
          // each shape's points from these plus the cursor delta, so repeated
          // moves never compound rounding. draw.js already renders any shape
          // listed in drag.originals on the interactive layer.
          originals: moving.map((a) => ({
            id: a.id,
            points: (a.points || []).map((p) => ({ x: p.x, y: p.y })),
            x: a.x,
            y: a.y
          }))
        };
        setCanvasCursor("grabbing");
        return;
      }
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
      // Clicking a finished annotation selects it and nothing more. Arming a
      // whole-shape move here meant any slight pointer travel during the click
      // dragged the entire annotation off its object, which then had to be
      // re-aligned by hand. Reshaping stays available through the vertex and
      // edge handles ("move-point"), which are deliberate targets.
      // No snapshot(): selecting is not an undoable edit.
      render();
      return;
    }
  }

  // Clicking empty space clears the selection (unless Shift is held).
  // Shift in draw mode lands here too (shiftSelect above), so a Shift+drag over
  // blank canvas rubber-bands a group the same way it does in select mode
  // instead of starting a shape. The `!event.shiftKey` guard below then never
  // fires on that path, which is correct: Shift is the additive gesture and
  // must not wipe what is already selected.
  if ((state.mode === "select" || shiftSelect) && view.drag?.type !== "draw-polygon") {
    if (!event.shiftKey) {
      state.selectedId = null;
      state.selectedIds.clear();
    }
    state.justFinalized = false;
    view.selectedLineIndex = -1;
    view.hoveredLineIndex = -1;
    view.drag = {
      type: "marquee",
      startX: point.x,
      startY: point.y,
      currentX: point.x,
      currentY: point.y,
      isShift: event.shiftKey
    };
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
          setStatus("Select class");
          return;
        }
        if (!state.activeLabelId) {
          setStatus("Select class");
          return;
        }
        // First point – create annotation immediately so it appears in the Objects panel
        snapshot();
        const annotation = {
          id: generateUUID(),
          // Recorded so exports can tell a box from a polygon. Without it both
          // shapes are just points plus a bound, and every box leaves as a
          // polygon (see formats/common.py annotation_type_of).
          type: "polygon",
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
        if (!lastPoint || Math.hypot(lastPoint.x - pointInImage.x, lastPoint.y - pointInImage.y) >= 1e-3) {
          annotation.points = addPolygonPointResolvingIntersections(pts, pointInImage);
          updateAnnotationBounds(annotation);
          // A genuinely new vertex invalidates whatever was parked by prior
          // point-level undos — there is no longer a consistent "redo" path
          // back to points that came after a different vertex.
          if (view.drag.undonePoints) view.drag.undonePoints = [];
        }
      }
      render();
      save();
      return;
    } else {
      if (state.needsLabelSelection && state.shape !== "magicWand") {
        setStatus("Select class");
        return;
      }
      if (!state.activeLabelId) {
        setStatus("Select class");
        return;
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

// How far, in degrees, the freehand stroke must swing away from the direction
// it was last travelling before the turn counts as a corner rather than the
// ordinary wander of a hand tracing a curve. A traced curve holds its heading
// to within a few degrees between points; a deliberate corner swings far
// wider. Set well above that noise so a shaky hand on a smooth arc does not
// litter the outline with points.
const FREEHAND_CORNER_ANGLE_DEGREES = 30;

// Minimum travel, in SCREEN pixels, before a corner may be committed. Without
// it the direction test fires continuously while the cursor jitters in place
// at the moment of the turn -- the heading of a near-zero-length step is
// meaningless -- and stacks a cluster of points on the corner.
//
// This floor is not a free parameter: it is the corner path's share of the
// same separation invariant the spacing gate enforces. A corner bypasses
// `freehandPointSpacing` by design (that is the whole point -- the spacing
// gate is what loses corners), so if the floor were merely "enough travel to
// have a meaningful heading" the corner branch would happily commit a vertex
// a couple of pixels from its neighbour, and a second one a couple of pixels
// after that once the fresh short segment made the incoming heading noisy.
// The annotator sees two handles stacked on the edge and cannot grab either
// reliably. So the floor tracks `vertexGrabRadius`: a corner vertex may land
// closer than the ordinary spacing, but never closer than the distance at
// which the grab test can still tell it apart from the point before it.
//
// It is TWICE the grab radius, not once. Each of the two vertices carries its
// own grab area, so the centres must be more than radius+radius apart for the
// areas not to intersect -- the same doubling `freehandPointSpacing` already
// encodes at 2x5=10. Using the bare radius leaves them overlapping by half,
// which is what still fused handles at traced corners after the first attempt
// at this fix.
//
// NOTE that at the current settings this makes the corner rule UNREACHABLE:
// 2 * vertexGrabRadius (10) equals freehandPointSpacing (10), so the spacing
// gate always opens first and `isFreehandCorner` never decides anything. That
// is deliberate and was chosen over sharper corners -- fused handles are
// unworkable, a corner rounded by at most one spacing step is cosmetic. The
// rule is kept rather than deleted because it becomes live again the moment
// the two settings separate (a smaller grab radius, or a wider spacing), and
// it is the piece that keeps corners sharp when there is room for it.
// A test pins the >= relationship so the overlap cannot silently return.
function freehandCornerMinTravel() {
  // Already in SCREEN pixels, and already floored at `minGrabScreenRadius`.
  return 2 * vertexGrabScreenRadius(
    annotationSettings.vertexGrabRadius, view.viewZoom, view.imageBox.scale);
}

/**
 * True when the freehand stroke has just turned a corner and the vertex must be
 * committed now, regardless of the spacing gate.
 *
 * The spacing gate alone loses corners, and does so precisely because of how a
 * hand draws one: approaching a corner the annotator slows and pivots, so the
 * cursor stays inside the spacing radius throughout the turn and no point is
 * laid down. By the time one is, the cursor has already travelled a full
 * spacing distance down the NEW heading, and the outline cuts the corner off --
 * the recorded vertex sits pulled toward the adjacent side.
 *
 * So the corner is detected by direction rather than distance: compare the
 * heading of the step about to be taken against the heading the stroke arrived
 * on, and commit as soon as they diverge. Distance still governs everything
 * else, which keeps smooth runs sparse.
 */
function isFreehandCorner(pts, end) {
  if (!Array.isArray(pts) || pts.length < 2) return false;
  const last = pts[pts.length - 1];
  const prev = pts[pts.length - 2];

  // The heading the stroke arrived on, and the one it is about to take.
  const inX = last.x - prev.x;
  const inY = last.y - prev.y;
  const outX = end.x - last.x;
  const outY = end.y - last.y;

  const inMag = Math.hypot(inX, inY);
  const outMag = Math.hypot(outX, outY);
  if (inMag === 0) return false;
  // Below the floor the outgoing heading is noise, not a turn -- and a vertex
  // committed there would overlap the one before it.
  if (outMag < freehandCornerMinTravel() / view.imageBox.scale) return false;

  const cos = Math.max(-1, Math.min(1,
    (inX * outX + inY * outY) / (inMag * outMag)));
  const turnDegrees = (Math.acos(cos) * 180) / Math.PI;
  return turnDegrees > FREEHAND_CORNER_ANGLE_DEGREES;
}

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
  // Runs before updateCanvasCursor so the cursor reflects the mode this move
  // just produced (crosshair outside the hover-armed polygon, pointer inside).
  const stickyChanged = updateStickyHover(point);
  updateCanvasCursor(point, event.shiftKey && !event.altKey);
  if (stickyChanged) render();

  // Detect line & point hover on selected polygon (select mode only, even when no view.drag)
  if (state.mode === "select" && state.selectedId && !view.drag) {
    const selected = state.annotations.find(a => a.id === state.selectedId);
    if (selected && selected.points && selected.points.length >= 3) {
      const ptIndex = hitTestPoint(point, selected);
      if (ptIndex !== -1) {
        let changed = false;
        if (view.hoveredLineIndex !== -1) {
          view.hoveredLineIndex = -1;
          changed = true;
        }
        if (view.hoveredPointIndex !== ptIndex) {
          view.hoveredPointIndex = ptIndex;
          changed = true;
        }
        if (changed) draw();
      } else {
        let changed = false;
        if (view.hoveredPointIndex !== -1) {
          view.hoveredPointIndex = -1;
          changed = true;
        }
        const lnIndex = hitTestLine(point, selected);
        if (lnIndex !== view.hoveredLineIndex) {
          view.hoveredLineIndex = lnIndex;
          changed = true;
        }
        if (changed) draw();
      }
    } else {
      let changed = false;
      if (view.hoveredPointIndex !== -1) {
        view.hoveredPointIndex = -1;
        changed = true;
      }
      if (view.hoveredLineIndex !== -1) {
        view.hoveredLineIndex = -1;
        changed = true;
      }
      if (changed) draw();
    }
  } else {
    let changed = false;
    if (view.hoveredPointIndex !== -1) {
      view.hoveredPointIndex = -1;
      changed = true;
    }
    if (view.hoveredLineIndex !== -1) {
      view.hoveredLineIndex = -1;
      changed = true;
    }
    if (changed) draw();
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
        // Screen pixels -> image space, so tracing at high zoom lays down
        // proportionally finer detail without retuning the setting.
        const threshold = annotationSettings.freehandPointSpacing / view.imageBox.scale;
        const travelled = lastPoint
          ? Math.hypot(lastPoint.x - end.x, lastPoint.y - end.y)
          : 0;
        // A corner is allowed to commit inside the spacing threshold -- that
        // is the whole point of the corner rule, since the spacing gate is
        // what rounds corners off. But it may not commit so close that the
        // new handle lands inside its neighbour's grab area: the annotator
        // sees two fused dots and cannot reliably grab either. So the corner
        // path carries its own, smaller, separation floor measured from the
        // last COMMITTED point -- which is the distance `isFreehandCorner`
        // cannot check, because its outgoing step is the pending mouse
        // sample, not the gap that will actually be left behind.
        const cornerFloor = freehandCornerMinTravel() / view.imageBox.scale;
        const cornerOpen = travelled > cornerFloor && isFreehandCorner(pts, end);
        if (lastPoint && (travelled > threshold || cornerOpen)) {
          // A corner commits AT the cursor, deliberately: the whole purpose of
          // the corner rule is to put a vertex on the turn itself, and
          // snapping it back to the spacing grid would round off the corner
          // the rule exists to preserve. Everything else is subdivided at
          // exact intervals so spacing does not depend on how fast the
          // annotator was moving when the event happened to fire.
          annotation.points = cornerOpen
            ? addPolygonPointResolvingIntersections(pts, end)
            : appendEvenlySpacedPoints(pts, end, threshold);
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
  } else if (view.drag.type === "marquee") {
    view.drag.currentX = point.x;
    view.drag.currentY = point.y;
    draw();
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

  if (view.drag.type === "move-shape") {
    const naturalW = view.imageElement.naturalWidth;
    const naturalH = view.imageElement.naturalHeight;
    let dx = end.x - view.drag.start.x;
    let dy = end.y - view.drag.start.y;

    // Take the undo snapshot once, on the first movement that actually shifts
    // the shape — a click that never moves leaves history untouched.
    if (!view.drag.moved) {
      if (Math.abs(dx) < 0.5 && Math.abs(dy) < 0.5) return;
      snapshot();
      view.drag.moved = true;
    }

    // Clamp the delta so no shape in the moved set leaves the image on any
    // edge — the whole group stops together rather than deforming when one
    // member hits a wall.
    view.drag.originals.forEach((orig) => {
      const pts = orig.points.length ? orig.points : [{ x: orig.x || 0, y: orig.y || 0 }];
      pts.forEach((p) => {
        dx = clamp(dx, -p.x, naturalW - p.x);
        dy = clamp(dy, -p.y, naturalH - p.y);
      });
    });

    view.drag.originals.forEach((orig) => {
      const annotation = state.annotations.find((a) => a.id === orig.id);
      if (!annotation) return;
      if (annotation.points) {
        annotation.points = orig.points.map((p) => ({ x: round(p.x + dx), y: round(p.y + dy) }));
      }
      if (typeof orig.x === "number") annotation.x = round(orig.x + dx);
      if (typeof orig.y === "number") annotation.y = round(orig.y + dy);
      updateAnnotationBounds(annotation);
    });
    render();
  }
});

// Screen-pixel radius within which the second click of a double-click is
// treated as landing on the same spot as the first. Converted to image
// coordinates at use, so it stays a constant on-screen distance whatever the
// zoom — at 8x zoom two clicks 6px apart on screen are 0.75px apart in the
// image, and a fixed image-space threshold would stop recognising them.
// Kept small on purpose: an annotator tracing fine detail may legitimately
// place vertices close together, and eating one of those would be worse than
// the doubled vertex this removes.
const DBLCLICK_DUPLICATE_RADIUS_PX = 6;

canvas.addEventListener("dblclick", (event) => {
  // Double-click closes an in-progress polygon.
  //
  // This was previously removed ("as per user request"), then asked for again;
  // the likely reason it was dropped the first time is the vertex it leaves
  // behind. The browser fires mousedown/mouseup/click for BOTH clicks before
  // dblclick, so by the time we get here the pointerdown handler has already
  // added two vertices a few pixels apart — the second is an artifact of the
  // gesture, not something the annotator meant to place. So drop it before
  // finalizing, otherwise every double-closed polygon carries a doubled vertex
  // at the end.
  //
  // Deliberately scoped to an in-progress polygon in draw mode, so the
  // select-mode vertex deletion below is untouched.
  if (view.drag?.type === "draw-polygon") {
    const annotation = state.annotations.find((item) => item.id === view.drag.annotationId);
    const pts = annotation?.points || [];

    // The duplicate is only droppable if doing so still leaves a real polygon.
    // Below that, finalizePolygon discards the shape anyway, and dropping a
    // point first would only change which incomplete shape gets discarded.
    if (pts.length >= 4) {
      const last = pts[pts.length - 1];
      const prev = pts[pts.length - 2];
      const threshold = DBLCLICK_DUPLICATE_RADIUS_PX / (view.imageBox?.scale || 1);
      if (last && prev && Math.hypot(last.x - prev.x, last.y - prev.y) <= threshold) {
        pts.pop();
        // Matches the invalidation the point-adding paths do: the parked
        // point-undo stack no longer describes a reachable state once the
        // vertex sequence changes underneath it.
        if (view.drag.undonePoints) view.drag.undonePoints = [];
      }
    }

    event.preventDefault();
    finalizePolygon();
    return;
  }

  if (state.mode === "select") {
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

    // Double-clicking inside a shape selects it. pointerdown has usually done
    // this already (hitTest does a real interior pointInPolygon test), but not
    // always: with Move Objects unlocked, double-clicking an already-selected
    // shape takes the "move-shape" branch instead, and a Shift-held second
    // click toggles the shape back off. Re-asserting the selection here makes
    // the gesture mean one thing regardless of how it was reached.
    //
    // Ordered after the vertex check on purpose: a double-click landing on a
    // vertex is a delete, and every vertex is also inside the shape, so
    // selecting first would swallow that gesture.
    const hitId = hitTest(point);
    if (hitId) {
      const hitAnnotation = state.annotations.find(a => a.id === hitId);
      state.selectedIds.clear();
      // Group-aware, matching the pointerdown selection path: a shape that
      // belongs to a group is never selected alone.
      if (hitAnnotation && hitAnnotation.groupId) {
        state.annotations.forEach(a => {
          if (a.groupId === hitAnnotation.groupId) state.selectedIds.add(a.id);
        });
      } else {
        state.selectedIds.add(hitId);
      }
      state.selectedId = hitId;
      view.selectedLineIndex = -1;
      view.hoveredLineIndex = -1;
      // Cancels any move armed by this gesture's pointerdown, so the shape
      // cannot drift while the second click is being delivered.
      view.drag = null;
      setCanvasCursor("default");
      // No snapshot() and no save(): selecting is not an undoable edit.
      render();
    }
  }
});

canvas.addEventListener("pointerup", (e) => {
  if (view.isPanning) {
    view.isPanning = false;
    setCanvasCursor("default");
    return;
  }
  if (view.drag?.type === "move-point") {
    const annotation = state.annotations.find((item) => item.id === view.drag.annotationId);
    const pointIdx = view.drag.pointIndex;
    view.drag = null;
    if (annotation && annotation.points && annotation.points.length >= 4) {
      const oldLen = annotation.points.length;
      // A drag that pinches the outline in two used to keep only the larger
      // half and throw the rest away. Split it into fully independent polygons
      // instead: same class, but each part is its own object that selects,
      // moves and deletes on its own.
      const parts = splitClosedPolygonAtIntersections(annotation.points);

      if (parts.length > 1) {
        annotation.points = parts[0].map((p) => ({ x: round(p.x), y: round(p.y) }));
        annotation.type = "polygon";
        // A split breaks the shape apart, so it also leaves whatever group the
        // original belonged to — otherwise the new pieces would stay welded to
        // each other and to that group's other shapes.
        delete annotation.groupId;
        // Likewise the merged-union rendering hint: the pieces are plain
        // polygons now, not the outline of a merged group. The flag also
        // round-trips nested under `extra`, so clear both spellings.
        delete annotation.mergedFromGroup;
        if (annotation.extra) delete annotation.extra.mergedFromGroup;
        // The recorded pre-merge shapes describe an outline that no longer
        // exists, so restoring them would resurrect geometry the split just
        // discarded. Drop them with the flag.
        delete annotation.mergedParts;
        if (annotation.extra) delete annotation.extra.mergedParts;
        updateAnnotationBounds(annotation);

        const insertAt = state.annotations.indexOf(annotation) + 1;
        const siblings = parts.slice(1).map((part) => {
          const sibling = {
            id: generateUUID(),
            type: "polygon",
            labelId: annotation.labelId,
            points: part.map((p) => ({ x: round(p.x), y: round(p.y) }))
          };
          updateAnnotationBounds(sibling);
          return sibling;
        });
        state.annotations.splice(insertAt, 0, ...siblings);

        // Select only the part still under the cursor. With no groupId the
        // cascade in state.js leaves the other pieces alone, which is what
        // makes them read as separate objects.
        state.selectedIds.clear();
        state.selectedId = annotation.id;
        setStatus(`Shape split into ${parts.length} polygons`);
      } else {
        annotation.points = resolveClosedPolygonIntersections(annotation.points, pointIdx);
        updateAnnotationBounds(annotation);
        if (annotation.points.length !== oldLen) {
          setStatus("Intersected sides removed");
        }
      }
      render();
    }
    save();
    return;
  }

  if (view.drag?.type === "move-shape") {
    const moved = view.drag.moved;
    view.drag = null;
    setCanvasCursor("default");
    render();
    if (moved) save();
    return;
  }

  if (view.drag?.type === "marquee") {
    const dx = view.drag.currentX - view.drag.startX;
    const dy = view.drag.currentY - view.drag.startY;
    if (Math.abs(dx) > 2 || Math.abs(dy) > 2) {
      const minX = Math.min(view.drag.startX, view.drag.currentX);
      const minY = Math.min(view.drag.startY, view.drag.currentY);
      const maxX = Math.max(view.drag.startX, view.drag.currentX);
      const maxY = Math.max(view.drag.startY, view.drag.currentY);
      
      const startPt = imagePoint({ x: minX, y: minY });
      const endPt = imagePoint({ x: maxX, y: maxY });
      
      const hitIds = [];
      state.annotations.forEach(ann => {
        if (isAnnotationHidden(ann)) return;
        const ax1 = ann.x;
        const ay1 = ann.y;
        const ax2 = ann.x + ann.width;
        const ay2 = ann.y + ann.height;
        
        // Intersect bounding boxes
        if (!(ax2 < startPt.x || ax1 > endPt.x || ay2 < startPt.y || ay1 > endPt.y)) {
          hitIds.push(ann.id);
        }
      });
      
      if (hitIds.length > 0) {
        if (!view.drag.isShift) {
          state.selectedIds.clear();
        }
        hitIds.forEach(id => state.selectedIds.add(id));
        state._selectedId = hitIds[0];
      }
    } else if (!view.drag.isShift) {
      // Just a click without shift should clear selection
      state.selectedId = null;
      state.selectedIds.clear();
    }
    view.drag = null;
    render();
    return;
  }

  if (view.drag?.type === "draw-polygon" && view.drag.needsSave) {
    view.drag.needsSave = false;
    save();
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
        // A real box, not a 4-vertex polygon that happens to be rectangular.
        // YOLO and COCO can represent the two differently.
        type: "bbox",
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
  if (!view.drag) setCanvasCursor("default");
  if (view.hoveredLineIndex !== -1) {
    view.hoveredLineIndex = -1;
    draw();
  }
});

canvas.addEventListener("pointercancel", () => {
  if (view.isPanning) {
    view.isPanning = false;
    setCanvasCursor("default");
    return;
  }
  if (view.drag?.type === "draw-polygon") {
    const annotation = state.annotations.find((item) => item.id === view.drag.annotationId);
    if (annotation && (annotation.points || []).length < 3) {
      state.annotations = state.annotations.filter((item) => item.id !== annotation.id);
    }
  }
  // A cancelled shape move keeps wherever it currently sits — the snapshot taken
  // on the first move still lets Ctrl+Z put it back. Only persist if it actually
  // moved (a snapshot was taken).
  const wasMoved = view.drag?.type === "move-shape" && view.drag.moved;
  view.drag = null;
  setCanvasCursor("default");
  render();
  if (wasMoved) save();
});

window.addEventListener("keydown", (event) => {
  const target = event.target;
  const isTyping = target instanceof HTMLInputElement
    || target instanceof HTMLTextAreaElement
    || target?.isContentEditable;
  if (isTyping) return;

  // Digit hotkeys pick a class by its position in the sidebar: 1-9 then 0 for
  // the tenth, Shift for classes 11-20. Read from event.code rather than
  // event.key because Shift+3 arrives as "#" on a US layout and as something
  // else again on other layouts, whereas the physical key is stable.
  if (!event.ctrlKey && !event.metaKey && !event.altKey) {
    const digitMatch = /^(?:Digit|Numpad)(\d)$/.exec(event.code);
    if (digitMatch) {
      // Numpad digits carry no shifted meaning, so only the main row extends
      // into the 11-20 range.
      const shifted = event.shiftKey && event.code.startsWith("Digit");
      if (!event.shiftKey || shifted) {
        event.preventDefault();
        const digit = Number(digitMatch[1]);
        // 0 is the tenth slot, not the zeroth.
        const index = (digit === 0 ? 9 : digit - 1) + (shifted ? 10 : 0);
        if (index < HOTKEY_LABEL_LIMIT && index < state.labels.length) {
          const label = state.labels[index];
          // A digit keystroke is unambiguous intent, so it still retags the
          // selection — unlike a sidebar click, which is easy to hit by
          // accident (see relabelSelection in workspace.js).
          activateLabel(label, { relabel: true });
          setStatus(`Class ${index + 1}: ${labelDisplayName(label)}`);
        } else {
          setStatus(`No class ${index + 1}`);
        }
        return;
      }
    }
  }

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
    // Covers all three branches below (vertex, edge, whole shape): each is a new
    // edit, so the next Ctrl+Z belongs to it rather than to post-finalize trimming.
    view.lastFinalizedPolygonId = null;
    if (state.mode === "select" && state.selectedId) {
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
    state.justFinalized = false;
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

  // Z-order shortcuts. Only meaningful with a selection, and only in select
  // mode so they don't collide with drawing. Shift = jump to the extreme
  // (back/front); no modifier = one step (backward/forward).
  if (event.key.toLowerCase() === "b" && state.selectedIds.size > 0) {
    event.preventDefault();
    if (event.shiftKey) sendToBack();
    else sendBackward();
    return;
  }
  if (event.key.toLowerCase() === "f" && state.selectedIds.size > 0) {
    event.preventDefault();
    if (event.shiftKey) bringToFront();
    else bringForward();
    return;
  }

  // H toggles the selection's visibility; U reveals. A hidden annotation can no
  // longer be clicked on the canvas (isAnnotationHidden gates the hit-test too),
  // but it stays *selected*, so a second H press finds it again and brings it
  // back. U remains because a click elsewhere drops that selection, and then the
  // only ways back are U (with nothing selected it reveals everything hidden) or
  // the sidebar eye button.
  if (event.key.toLowerCase() === "h" && state.selectedIds.size > 0) {
    event.preventDefault();
    // Mixed selections resolve to "hide": pressing H again then reveals all of
    // them, so the pair of presses is a predictable round trip.
    const anyVisible = [...state.selectedIds].some((id) => !state.hiddenAnnotationIds.has(id));
    const count = state.selectedIds.size;
    if (anyVisible) {
      state.selectedIds.forEach((id) => state.hiddenAnnotationIds.add(id));
      setStatus(count > 1
        ? `${count} objects hidden (H or U to reveal)`
        : "Object hidden (H or U to reveal)");
    } else {
      state.selectedIds.forEach((id) => state.hiddenAnnotationIds.delete(id));
      setStatus(count > 1 ? `${count} objects revealed` : "Object revealed");
    }
    render();
    return;
  }
  if (event.key.toLowerCase() === "u") {
    event.preventDefault();
    if (state.selectedIds.size > 0) {
      state.selectedIds.forEach((id) => state.hiddenAnnotationIds.delete(id));
      setStatus("Object revealed");
    } else if (state.hiddenAnnotationIds.size > 0) {
      const count = state.hiddenAnnotationIds.size;
      state.hiddenAnnotationIds.clear();
      setStatus(`${count} hidden object${count > 1 ? "s" : ""} revealed`);
    } else {
      setStatus("Nothing hidden");
    }
    render();
    return;
  }

  if (event.key.toLowerCase() === "d") {
    if (!state.activeLabelId) {
      setStatus("Pick class first");
      render();
    } else {
      state.mode = "draw";
      state.selectedId = null;
      state.selectedIds.clear();
      render();
    }
  }

  if (event.key.toLowerCase() === "s") {
    state.mode = "select";
    render();
  }
});

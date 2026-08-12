import { generateUUID, clamp, round } from "../utils.js?v=1";
import { state, snapshot, isAnnotationHidden } from "../state.js?v=7";
import { annotationPoints, updateAnnotationBounds, pointInPolygon } from "./geometry.js?v=1";
import { untangleRing } from "./untangle.js?v=1";
import { view } from "./view.js?v=1";
import { draw, drawAllLayers } from "./draw.js?v=1";
import { canvas, undoButton } from "../dom.js?v=2";
import { commentOverlayRefs } from "../comment-overlay.js?v=1";
import { setStatus, save, render } from "../components/workspace.js?v=10";
import { performMagicWandSegmentation } from "../ai/detect.js?v=1";
import { applyAutoSmooth } from "../fft-controls.js?v=1";
import { annotationSettings } from "../feature-flags.js?v=1";

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
  // Divided by scale to convert the on-screen pixel radius into image space,
  // where the comparison happens — so the grab area stays a constant physical
  // size on screen instead of shrinking as the annotator zooms in.
  const threshold = annotationSettings.vertexGrabRadius / view.imageBox.scale;
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
  // Screen pixels -> image space; see hitTestPoint above.
  const threshold = annotationSettings.edgeGrabRadius / view.imageBox.scale;
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

// Resolve a polygon that an edit just made cross itself: the outside loop is
// dropped and the crossing becomes a vertex. See untangle.js for the algorithm.
//
// The `type === "polygon"` guard is the containment boundary for this whole
// feature. Boxes go through several of the same handlers (updateAnnotationBounds
// is shared by every shape), and a bbox must never be reshaped by this — its
// four corners are load-bearing for `_is_axis_aligned_rect` in
// formats/common.py, which is what keeps a box exporting as a box. Comments
// carry no points at all.
//
// Returns true when the ring was actually rewritten, so callers can report it.
// Takes no snapshot of its own: every call site either already snapshotted for
// the edit it is part of, or is a click on an in-progress polygon that
// deliberately stays out of state.history (see undoLastPoint).
//
// `closed` (default true) must be false for a polygon that is still being
// drawn by hand: `annotation.points` is an open polyline there (the segment
// back to points[0] is only the dashed preview to the cursor, drawn by
// draw.js — not a committed edge), and treating it as a closed ring would
// fabricate a crossing edge that does not exist yet. See untangle.js.
function untangleIfPolygon(annotation, anchor, closed = true) {
  if (!annotation || annotation.type !== "polygon") return false;
  const pts = annotation.points;
  if (!Array.isArray(pts) || pts.length < 4) return false;

  const result = untangleRing(pts, anchor, closed);
  if (!result.changed) return false;

  annotation.points = result.points;

  if (closed) {
    // Save the original first vertex *before* untangling so we can restore it
    // to index 0 afterwards.  A closed ring is rotationally invariant, so any
    // rotation produces the same polygon — but the canvas draws the closing-
    // vertex handle at points[0], and the annotator expects it to stay where
    // they first clicked.  See .devnotes/bugs/polygon-untangle-startpoint-drift.md.
    //
    // Not needed for an open polyline: untangleRing never rotates or wraps
    // there, it only trims the tail, so points[0] is untouched already.
    const originalFirst = pts[0];
    const EPS = 1e-9;
    const idx = annotation.points.findIndex(
      (p) => Math.abs(p.x - originalFirst.x) < EPS && Math.abs(p.y - originalFirst.y) < EPS
    );
    if (idx > 0) {
      annotation.points = [
        ...annotation.points.slice(idx),
        ...annotation.points.slice(0, idx),
      ];
    }
  }

  return true;
}

// Untangle after a vertex or edge removal. Deleting points stitches the two
// neighbours together with a new edge, and that new edge can cut straight
// across the far side of the shape — it is the easiest way to produce a
// crossing without dragging anything. There is no anchor here: the vertex the
// annotator interacted with is the one that just went away.
//
// Also clears the hover/selection indices, which refer to positions in the ring
// that the removal (and any untangle) has just shifted.
function afterPointRemoval(annotation) {
  const untangled = untangleIfPolygon(annotation);
  view.selectedLineIndex = -1;
  view.hoveredLineIndex = -1;
  view.hoveredPointIndex = -1;
  updateAnnotationBounds(annotation);
  return untangled;
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
    const hoverId = hitTest(point);
    if (hoverId) {
      // With Move Objects unlocked, hovering the interior of an already-selected
      // shape promises a drag, so show "move". Otherwise a finished annotation
      // can only be selected, and "pointer" is the honest cursor.
      if (state.moveObjectsUnlocked && state.selectedIds.has(hoverId)) {
        canvas.style.cursor = "move";
      } else {
        canvas.style.cursor = "pointer";
      }
      return;
    }
  }

  canvas.style.cursor = state.mode === "draw" ? "crosshair" : "default";
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
  // Resolve any self-crossing before smoothing. Freehand tracing is sampled on
  // pointermove, so a stroke that loops back over itself lays down a crossing
  // mid-drag; untangling there would collapse the ring under the moving cursor,
  // which then continues drawing into a re-indexed array. Deferring to finalize
  // keeps the stroke stable and resolves it once, at the moment the shape is
  // committed. No anchor: by this point no single vertex is "the one just
  // placed", so area alone decides.
  //
  // Must run before applyAutoSmooth — the FFT low-pass treats points as one
  // ordered contour, and running it over a tangled ring smears the two loops
  // into each other rather than filtering either.
  const untangled = untangleIfPolygon(annotation);
  // Auto-smooth: apply FFT low-pass filter when the toggle is enabled.
  // Called before updateAnnotationBounds so the bounds reflect the smoothed points.
  applyAutoSmooth(annotation);
  updateAnnotationBounds(annotation);
  state.needsLabelSelection = true;
  state.justFinalized = true;
  render();
  save();
  setStatus(untangled ? "Overlap removed — select class for next" : "Select class for next");
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

export function undoAction() {
  if (undoLastPoint()) {
    return;
  }
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
  // state.labels is untouched: classes are project-level, not undoable (state.js).
  state.annotations = restored.annotations;
  state.selectedId = restored.selectedId;

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
  // state.labels is untouched: classes are project-level, not undoable (state.js).
  state.annotations = restored.annotations;
  state.selectedId = restored.selectedId;

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
  setStatus("Grouped");
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
      setStatus("Ungrouped");
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

  // First click after finalizing a shape. The shape is still selected so a class
  // click can label it; this click releases that selection, so picking a class for
  // the next shape cannot re-label the finished one. Consumed immediately — every
  // later click must fall through to the normal editing blocks below.
  if (state.justFinalized) {
    state.justFinalized = false;
    const hitId = hitTest(point);
    // Clicking the finished shape itself keeps it selected; anything else releases
    // it. Either way we fall through, so this click behaves like a Select-mode
    // click: vertex add/delete, point drag and move all work from here on.
    if (!hitId || !state.selectedIds.has(hitId)) {
      clearSelectionAfterFinalize();
      if (!hitId) return;
    }
  }

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
        if (afterPointRemoval(selected)) setStatus("Overlap removed");
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
        if (afterPointRemoval(selected)) setStatus("Overlap removed");
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
  // target, so selecting it would clear view.drag and end the shape. state.mode
  // is not sufficient here — line 3286 can leave it as "select" before drawing ever starts.
  // The inert post-finalize state is included even though state.mode is still
  // "draw": no new shape can be started until a class is picked, so a click on an
  // existing annotation should select it rather than do nothing.
  if ((state.mode !== "draw" || state.needsLabelSelection) && view.drag?.type !== "draw-polygon") {
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
        canvas.style.cursor = "grabbing";
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

  // Clicking empty space clears the selection — but never while a polygon is being
  // drawn, or this would discard view.drag (and the in-progress shape) on every click.
  if (state.mode === "select" && view.drag?.type !== "draw-polygon") {
    state.selectedId = null;
    state.selectedIds.clear();
    state.justFinalized = false;
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

        // A click landing back on an already-placed vertex (typically the
        // second half of a double-click) is a delete gesture, not a request
        // to place a new point there. Left alone, it would push a
        // near-duplicate point and hand the mid-draw untangle below a
        // fabricated edge back to that vertex — a real crossing by
        // coincidence, not by intent — which then cuts away whatever the
        // annotator had already drawn. points[0] is excluded: clicking it
        // closes the shape via the hitTestPoint block above and never
        // reaches here.
        const existingIndex = pts.length > 1
          ? hitTestPoint(point, { points: pts.slice(1) })
          : -1;
        if (existingIndex !== -1) {
          annotation.points.splice(existingIndex + 1, 1);
          updateAnnotationBounds(annotation);
          if (view.drag.undonePoints) view.drag.undonePoints = [];
          render();
          save();
          return;
        }

        const lastPoint = pts[pts.length - 1];
        if (!lastPoint || Math.hypot(lastPoint.x - pointInImage.x, lastPoint.y - pointInImage.y) > 1) {
          const placed = { x: round(pointInImage.x), y: round(pointInImage.y) };
          annotation.points.push(placed);
          // Each click is a discrete committed action, so a crossing it creates
          // is resolved immediately rather than at finalize — the annotator sees
          // the shape they will keep while they are still placing points.
          // `closed: false` — the polygon isn't finished yet, so this is an
          // open polyline, not a ring; the dashed line back to the start point
          // is only a preview (see draw.js), not a real edge to test against.
          // The crossing point itself becomes the new last point, so `placed`
          // can be dropped here if it landed inside the loop just cut away.
          if (untangleIfPolygon(annotation, placed, false)) {
            setStatus("Overlap removed");
          }
          updateAnnotationBounds(annotation);
          // A genuinely new vertex invalidates whatever was parked by prior
          // point-level undos — there is no longer a consistent "redo" path
          // back to points that came after a different vertex. This holds all
          // the more once an untangle has re-indexed the ring.
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
        // Screen pixels -> image space, so tracing at high zoom lays down
        // proportionally finer detail without retuning the setting.
        const threshold = annotationSettings.freehandPointSpacing / view.imageBox.scale;
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

canvas.addEventListener("dblclick", (event) => {
  // Polygon finalizing via double-click has been removed as per user request

  // While a polygon is still being drawn, vertex deletion is already handled
  // per-click in the pointerdown "draw-polygon" branch above (closed: false,
  // no fabricated edge back to points[0]). This handler runs afterPointRemoval
  // with closed: true — correct for a finished ring, but on an open polyline
  // it treats the dashed preview line back to the start point as a real edge,
  // reintroducing the exact phantom-closing-edge bug the mid-draw path exists
  // to avoid. Both pointerdown events that make up the double-click already
  // reached that branch by the time this fires, so there is nothing left
  // for this handler to do mid-draw — skip it outright.
  if (view.drag?.type === "draw-polygon") return;

  if (state.selectedId) {
    const point = canvasPoint(event);
    const selected = state.annotations.find(a => a.id === state.selectedId);
    if (selected && selected.points && selected.points.length > 3) {
      const ptIndex = hitTestPoint(point, selected);
      if (ptIndex !== -1) {
        snapshot();
        selected.points.splice(ptIndex, 1);
        const untangled = afterPointRemoval(selected);
        render();
        save();
        setStatus(untangled ? "Vertex removed — overlap removed" : "Vertex removed");
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
    // Untangle on release, not on every pointermove. Rewriting the ring
    // mid-drag would invalidate view.drag.pointIndex the instant it happened,
    // leaving the annotator dragging a vertex that no longer exists at that
    // index — they would silently start reshaping some other part of the
    // polygon. Deferring to release keeps pointIndex valid for the whole
    // gesture; the crossing stays visible while dragging and resolves when
    // the vertex is dropped.
    const annotation = state.annotations.find((item) => item.id === view.drag.annotationId);
    // The dragged vertex is the anchor, so a symmetric split keeps the half
    // the annotator was actually working in.
    const anchor = annotation?.points?.[view.drag.pointIndex];
    const untangled = untangleIfPolygon(annotation, anchor);
    if (untangled) {
      updateAnnotationBounds(annotation);
      // The ring was re-indexed, so any hover/selection referring to the old
      // indices now points at unrelated geometry.
      view.hoveredPointIndex = -1;
      view.hoveredLineIndex = -1;
      view.selectedLineIndex = -1;
      setStatus("Overlap removed");
    }
    view.drag = null;
    // snapshot() was already taken at pointerdown, so one Ctrl+Z undoes the
    // move and the untangle together — they are one edit from the user's side.
    if (untangled) render();
    save();
    return;
  }

  if (view.drag?.type === "move-shape") {
    const moved = view.drag.moved;
    view.drag = null;
    canvas.style.cursor = "default";
    render();
    if (moved) save();
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
  // A cancelled shape move keeps wherever it currently sits — the snapshot taken
  // on the first move still lets Ctrl+Z put it back. Only persist if it actually
  // moved (a snapshot was taken).
  const wasMoved = view.drag?.type === "move-shape" && view.drag.moved;
  view.drag = null;
  canvas.style.cursor = "default";
  render();
  if (wasMoved) save();
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
          const untangled = afterPointRemoval(selected);
          render();
          save();
          setStatus(untangled ? "Vertex deleted — overlap removed" : "Vertex deleted");
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
          const untangled = afterPointRemoval(selected);
          render();
          save();
          setStatus(untangled ? "Line segment deleted — overlap removed" : "Line segment deleted");
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

  if (event.key.toLowerCase() === "d") {
    if (!state.activeLabelId) {
      setStatus("Pick class first");
      render();
    } else {
      state.mode = "draw";
      render();
    }
  }

  if (event.key.toLowerCase() === "s") {
    state.mode = "select";
    render();
  }
});

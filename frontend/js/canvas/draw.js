import { canvas, ctx, backgroundImage, staticCanvas, staticCtx } from "../dom.js?v=1";
import { state, labelById, isAnnotationHidden } from "../state.js?v=1";
import { annotationSettings, annotationOpacity } from "../feature-flags.js?v=1";
import { view } from "./view.js?v=1";
import { annotationPoints, hexToRgba, isPointInsideOtherGroupPolygons } from "./geometry.js?v=1";

let compositeFillCanvas = null;
let compositeFillCtx = null;
let compositeStrokeCanvas = null;
let compositeStrokeCtx = null;

function getCompositeContexts(width, height) {
  if (!compositeFillCanvas) {
    compositeFillCanvas = document.createElement("canvas");
    compositeFillCtx = compositeFillCanvas.getContext("2d");
    compositeStrokeCanvas = document.createElement("canvas");
    compositeStrokeCtx = compositeStrokeCanvas.getContext("2d");
  }
  if (compositeFillCanvas.width !== width || compositeFillCanvas.height !== height) {
    compositeFillCanvas.width = width;
    compositeFillCanvas.height = height;
    compositeStrokeCanvas.width = width;
    compositeStrokeCanvas.height = height;
  }
  return { fillCtx: compositeFillCtx, strokeCtx: compositeStrokeCtx };
}

function isAnnotationVisible(annotation, canvasWidth, canvasHeight) {
  const ax = Number(annotation.x) || 0;
  const ay = Number(annotation.y) || 0;
  const aw = Math.max(0, Number(annotation.width) || 0);
  const ah = Math.max(0, Number(annotation.height) || 0);

  const screenX = view.imageBox.x + ax * view.imageBox.scale;
  const screenY = view.imageBox.y + ay * view.imageBox.scale;
  const screenW = aw * view.imageBox.scale;
  const screenH = ah * view.imageBox.scale;

  const pad = 30; // Padding for strokes and selection handles
  return !(
    screenX + screenW + pad < 0 ||
    screenY + screenH + pad < 0 ||
    screenX - pad > canvasWidth ||
    screenY - pad > canvasHeight
  );
}

function drawGroupUnion(groupAnns, selected, targetCtx) {
  if (groupAnns.length === 0) return;
  const label = labelById(groupAnns[0].labelId);
  const { fillCtx, strokeCtx } = getCompositeContexts(targetCtx.canvas.width, targetCtx.canvas.height);

  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  groupAnns.forEach((ann) => {
    if (ann.type === "comment") return;
    const points = annotationPoints(ann);
    if (!points || points.length < 3) return;
    points.forEach((p) => {
      const sx = view.imageBox.x + p.x * view.imageBox.scale;
      const sy = view.imageBox.y + p.y * view.imageBox.scale;
      if (sx < minX) minX = sx;
      if (sy < minY) minY = sy;
      if (sx > maxX) maxX = sx;
      if (sy > maxY) maxY = sy;
    });
  });

  if (minX === Infinity) return;
  
  // Add padding for stroke width and general safety
  const pad = (selected ? 3 : 2) * 2 + 5;
  const bx = Math.max(0, Math.floor(minX - pad));
  const by = Math.max(0, Math.floor(minY - pad));
  const bw = Math.min(targetCtx.canvas.width - bx, Math.ceil(maxX - minX + pad * 2));
  const bh = Math.min(targetCtx.canvas.height - by, Math.ceil(maxY - minY + pad * 2));

  if (bw <= 0 || bh <= 0) return;

  // Clear ONLY the bounding box region
  fillCtx.clearRect(bx, by, bw, bh);
  strokeCtx.clearRect(bx, by, bw, bh);

  // Fill Canvas: draw opaque union
  fillCtx.fillStyle = label.color;
  groupAnns.forEach((ann) => {
    if (ann.type === "comment") return;
    const points = annotationPoints(ann);
    if (!points || points.length < 3) return;
    const screenPoints = points.map((p) => ({
      x: view.imageBox.x + p.x * view.imageBox.scale,
      y: view.imageBox.y + p.y * view.imageBox.scale
    }));
    fillCtx.beginPath();
    screenPoints.forEach((pt, i) => {
      if (i === 0) fillCtx.moveTo(pt.x, pt.y);
      else fillCtx.lineTo(pt.x, pt.y);
    });
    fillCtx.closePath();
    fillCtx.fill();
  });

  // Stroke Canvas: draw thick strokes
  strokeCtx.strokeStyle = label.color;
  strokeCtx.lineWidth = (selected ? 3 : 2) * 2;
  strokeCtx.lineJoin = "round";
  
  groupAnns.forEach((ann) => {
    if (ann.type === "comment") return;
    const points = annotationPoints(ann);
    if (!points || points.length < 3) return;
    const screenPoints = points.map((p) => ({
      x: view.imageBox.x + p.x * view.imageBox.scale,
      y: view.imageBox.y + p.y * view.imageBox.scale
    }));
    strokeCtx.beginPath();
    screenPoints.forEach((pt, i) => {
      if (i === 0) strokeCtx.moveTo(pt.x, pt.y);
      else strokeCtx.lineTo(pt.x, pt.y);
    });
    strokeCtx.closePath();
    strokeCtx.stroke();
  });

  // Erase inner strokes using the fill mask
  strokeCtx.globalCompositeOperation = "destination-out";
  strokeCtx.drawImage(fillCtx.canvas, bx, by, bw, bh, bx, by, bw, bh);
  strokeCtx.globalCompositeOperation = "source-over";

  // Composite them to the target canvas
  targetCtx.save();
  // We use normal or selected opacity (groups don't include drafts usually)
  const fillAlpha = selected ? annotationOpacity.selected : annotationOpacity.normal;
  targetCtx.globalAlpha = fillAlpha;
  targetCtx.drawImage(fillCtx.canvas, bx, by, bw, bh, bx, by, bw, bh);
  targetCtx.globalAlpha = 1.0;
  targetCtx.drawImage(strokeCtx.canvas, bx, by, bw, bh, bx, by, bw, bh);
  targetCtx.restore();
}

export function computeImageBox() {
  if (!view.imageLoaded) {
    view.imageBox = { x: 0, y: 0, width: 0, height: 0, scale: 1 };
    return;
  }

  const rect = canvas.getBoundingClientRect();
  // Contain-fit: at zoom 1 the whole image fits inside the canvas on both axes,
  // so a tall image is never cut off at the top and bottom edges.
  const baseScale = Math.min(
    rect.width / view.imageElement.naturalWidth,
    rect.height / view.imageElement.naturalHeight
  );
  view.baseScale = baseScale;
  const scale = baseScale * view.viewZoom;
  const width = view.imageElement.naturalWidth * scale;
  const height = view.imageElement.naturalHeight * scale;

  view.imageBox = {
    x: (rect.width - width) / 2 + view.viewPan.x,
    y: (rect.height - height) / 2 + view.viewPan.y,
    width,
    height,
    scale
  };
}

export function drawImageLayer() {
  if (!view.imageLoaded) return;
  backgroundImage.style.left = view.imageBox.x + "px";
  backgroundImage.style.top = view.imageBox.y + "px";
  backgroundImage.style.width = view.imageBox.width + "px";
  backgroundImage.style.height = view.imageBox.height + "px";
}

export function drawStaticLayer() {
  const rect = staticCanvas.getBoundingClientRect();
  staticCtx.clearRect(0, 0, rect.width, rect.height);
  if (!view.imageLoaded) return;

  const drawnGroups = new Set();
  const cw = staticCtx.canvas.width;
  const ch = staticCtx.canvas.height;

  state.annotations.forEach((annotation) => {
    if (isAnnotationHidden(annotation)) return;
    if (!isAnnotationVisible(annotation, cw, ch)) return;
    const isSelected = state.selectedIds.has(annotation.id);
    const isDragging = view.drag?.annotationId === annotation.id || view.drag?.originals?.find(a => a.id === annotation.id);
    if (!isSelected && !isDragging) {
      if (annotation.groupId) {
        if (!drawnGroups.has(annotation.groupId)) {
          drawnGroups.add(annotation.groupId);
          const groupAnns = state.annotations.filter(a => a.groupId === annotation.groupId && !isAnnotationHidden(a) && !state.selectedIds.has(a.id) && !(view.drag?.annotationId === a.id || view.drag?.originals?.find(orig => orig.id === a.id)));
          drawGroupUnion(groupAnns, false, staticCtx);
          groupAnns.forEach(ann => drawAnnotation(ann, false, staticCtx, true, groupAnns));
        }
      } else {
        drawAnnotation(annotation, false, staticCtx);
      }
    }
  });
}

let pendingDraw = false;
let pendingDrawAll = false;

function doDrawAllSync() {
  pendingDrawAll = false;
  pendingDraw = false;
  computeImageBox();
  drawImageLayer();
  drawStaticLayer();
  doDrawSync();
}

export function drawAllLayers() {
  if (!pendingDrawAll) {
    pendingDrawAll = true;
    requestAnimationFrame(doDrawAllSync);
  }
}

export function draw() {
  if (pendingDrawAll) return;
  if (!pendingDraw) {
    pendingDraw = true;
    requestAnimationFrame(doDrawSync);
  }
}

function doDrawSync() {
  pendingDraw = false;
  const rect = canvas.getBoundingClientRect();
  ctx.clearRect(0, 0, rect.width, rect.height);
  computeImageBox();

  if (!view.imageLoaded) return;

  const drawnGroups = new Set();
  const cw = ctx.canvas.width;
  const ch = ctx.canvas.height;

  state.annotations.forEach((annotation) => {
    // Filtered here as well as in drawStaticLayer: without this a hidden
    // annotation would reappear the moment it became selected.
    if (isAnnotationHidden(annotation)) return;
    if (!isAnnotationVisible(annotation, cw, ch)) return;
    const isSelected = state.selectedIds.has(annotation.id);
    const isDragging = view.drag?.annotationId === annotation.id || view.drag?.originals?.find(a => a.id === annotation.id);
    if (isSelected || isDragging) {
      if (annotation.groupId) {
        if (!drawnGroups.has(annotation.groupId)) {
          drawnGroups.add(annotation.groupId);
          const groupAnns = state.annotations.filter(a => a.groupId === annotation.groupId && !isAnnotationHidden(a) && (state.selectedIds.has(a.id) || view.drag?.annotationId === a.id || view.drag?.originals?.find(orig => orig.id === a.id)));
          drawGroupUnion(groupAnns, true, ctx);
          groupAnns.forEach(ann => drawAnnotation(ann, true, ctx, true, groupAnns));
        }
      } else {
        drawAnnotation(annotation, isSelected, ctx);
      }
    }
  });

  if (view.drag?.draft) {
    drawAnnotation(view.drag.draft, true, ctx);
  }

  if (view.drag?.type === "marquee") {
    ctx.save();
    ctx.strokeStyle = "#4dabf7";
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.fillStyle = "rgba(77, 171, 247, 0.2)";
    const w = view.drag.currentX - view.drag.startX;
    const h = view.drag.currentY - view.drag.startY;
    ctx.fillRect(view.drag.startX, view.drag.startY, w, h);
    ctx.strokeRect(view.drag.startX, view.drag.startY, w, h);
    ctx.restore();
  }

  if (view.pendingCommentPoint) {
    const screenX = view.imageBox.x + view.pendingCommentPoint.x * view.imageBox.scale;
    const screenY = view.imageBox.y + view.pendingCommentPoint.y * view.imageBox.scale;
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
  if (view.drag?.type === "draw-polygon") {
    const annotation = state.annotations.find((item) => item.id === view.drag.annotationId);
    const pts = annotation?.points || [];
    const label = annotation ? labelById(annotation.labelId) : null;
    const edgeColor = label ? label.color : "#0f8b8d";
    // Matches the committed-annotation fill so the shape does not visibly
    // change shade the moment the polygon is closed.
    const fillColor = hexToRgba(label ? label.color : "#0f8b8d", annotationOpacity.drawing);

    // The starting point is now distinguished by filling it with the class color via drawVertexHandles
    // Draw preview line from last point to cursor
    if (pts.length >= 1 && view.drag.preview) {
      const last = pts[pts.length - 1];
      const sx = view.imageBox.x + last.x * view.imageBox.scale;
      const sy = view.imageBox.y + last.y * view.imageBox.scale;
      const ex = view.imageBox.x + view.drag.preview.x * view.imageBox.scale;
      const ey = view.imageBox.y + view.drag.preview.y * view.imageBox.scale;

      ctx.save();

      // Draw dynamic fill for the polygon being drawn
      if (pts.length >= 2) {
        ctx.beginPath();
        pts.forEach((pt, i) => {
          const px = view.imageBox.x + pt.x * view.imageBox.scale;
          const py = view.imageBox.y + pt.y * view.imageBox.scale;
          if (i === 0) ctx.moveTo(px, py);
          else ctx.lineTo(px, py);
        });
        ctx.lineTo(ex, ey);
        ctx.closePath();
        ctx.fillStyle = fillColor;
        ctx.fill();
      }

      ctx.setLineDash([6, 4]);
      ctx.strokeStyle = edgeColor;
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(ex, ey);
      ctx.stroke();
      ctx.restore();
    }
  }
}

export function drawAnnotation(annotation, selected = false, targetCtx = ctx, skipBaseLayer = false, groupAnns = null) {
  if (annotation.type === "comment") {
    const screenPoint = {
      x: view.imageBox.x + annotation.x * view.imageBox.scale,
      y: view.imageBox.y + annotation.y * view.imageBox.scale
    };
    targetCtx.save();
    targetCtx.fillStyle = selected ? "#f4a261" : "#e85d75";
    targetCtx.beginPath();
    targetCtx.arc(screenPoint.x, screenPoint.y, 8, 0, Math.PI * 2);
    targetCtx.fill();
    targetCtx.strokeStyle = "#ffffff";
    targetCtx.lineWidth = 2;
    targetCtx.stroke();

    const author = annotation.author || (annotation.extra && annotation.extra.author) || 'User';
    const text = `${author}: ${annotation.text}`;
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
    x: view.imageBox.x + point.x * view.imageBox.scale,
    y: view.imageBox.y + point.y * view.imageBox.scale
  }));

  targetCtx.save();
  targetCtx.lineWidth = selected ? 3 : 2;
  targetCtx.strokeStyle = label.color;
  // Fill matches the outline colour but stays well below it in opacity, so the
  // class reads at a glance without obscuring the pixels being annotated.
  const isDraft = annotation.id === "draft" || view.drag?.draft === annotation;
  const fillAlpha = isDraft
    ? annotationOpacity.drawing
    : (selected ? annotationOpacity.selected : annotationOpacity.normal);
  targetCtx.fillStyle = hexToRgba(label.color, fillAlpha);

  if (!screenPoints.length) {
    targetCtx.restore();
    return;
  }

  if (!skipBaseLayer) {
    targetCtx.beginPath();
    screenPoints.forEach((point, index) => {
      if (index === 0) {
        targetCtx.moveTo(point.x, point.y);
      } else {
        targetCtx.lineTo(point.x, point.y);
      }
    });
    const isBeingDrawn = view.drag?.type === "draw-polygon" && view.drag?.annotationId === annotation.id;
    if (screenPoints.length >= 3 && !isBeingDrawn) {
      targetCtx.closePath();
      targetCtx.fill();
    }
    targetCtx.stroke();
  }
  
  const isBeingDrawn = view.drag?.type === "draw-polygon" && view.drag?.annotationId === annotation.id;

  // No class-name tag is drawn on the canvas: the Objects panel lists every
  // annotation, and on-image text obscures the pixels being annotated.

  // Draw highlighted/selected line segments on the selected annotation
  if (selected && annotation.id === state.selectedId && screenPoints.length >= 3) {
    // Draw hovered line highlight
    if (view.hoveredLineIndex !== -1 && view.hoveredLineIndex !== view.selectedLineIndex) {
      const p1 = screenPoints[view.hoveredLineIndex];
      const p2 = screenPoints[(view.hoveredLineIndex + 1) % screenPoints.length];
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
    if (view.selectedLineIndex !== -1 && view.selectedLineIndex < screenPoints.length) {
      const p1 = screenPoints[view.selectedLineIndex];
      const p2 = screenPoints[(view.selectedLineIndex + 1) % screenPoints.length];
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
    const visibleScreenPoints = screenPoints.map((sp, i) => {
      if (groupAnns && isPointInsideOtherGroupPolygons(points[i], annotation, groupAnns)) {
        return null;
      }
      return sp;
    });
    drawVertexHandles(visibleScreenPoints, label.color, targetCtx, isBeingDrawn);
  }
  targetCtx.restore();
}

export function drawVertexHandles(points, color, targetCtx = ctx, isBeingDrawn = false) {
  const radius = annotationSettings.vertexHandleRadius;
  targetCtx.strokeStyle = color;
  targetCtx.lineWidth = 2;
  points.forEach((point, i) => {
    if (!point) return;
    targetCtx.beginPath();
    targetCtx.arc(point.x, point.y, radius, 0, Math.PI * 2);
    if (i === 0 && isBeingDrawn) {
      targetCtx.fillStyle = color;
    } else {
      targetCtx.fillStyle = "#ffffff";
    }
    targetCtx.fill();
    targetCtx.stroke();
  });
}

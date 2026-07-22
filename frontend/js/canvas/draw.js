import { canvas, ctx, imageCanvas, imageCtx, staticCanvas, staticCtx } from "../dom.js?v=1";
import { state, handleSize, labelById, isAnnotationHidden } from "../state.js?v=1";
import { view } from "./view.js?v=1";
import { annotationPoints, hexToRgba } from "./geometry.js?v=1";

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
  const rect = imageCanvas.getBoundingClientRect();
  imageCtx.clearRect(0, 0, rect.width, rect.height);
  if (!view.imageLoaded) return;
  imageCtx.drawImage(view.imageElement, view.imageBox.x, view.imageBox.y, view.imageBox.width, view.imageBox.height);
}

export function drawStaticLayer() {
  const rect = staticCanvas.getBoundingClientRect();
  staticCtx.clearRect(0, 0, rect.width, rect.height);
  if (!view.imageLoaded) return;

  state.annotations.forEach((annotation) => {
    if (isAnnotationHidden(annotation)) return;
    const isSelected = state.selectedIds.has(annotation.id);
    const isDragging = view.drag?.annotationId === annotation.id || view.drag?.originals?.find(a => a.id === annotation.id);
    if (!isSelected && !isDragging) {
      drawAnnotation(annotation, false, staticCtx);
    }
  });
}

export function drawAllLayers() {
  computeImageBox();
  drawImageLayer();
  drawStaticLayer();
  draw();
}

export function draw() {
  const rect = canvas.getBoundingClientRect();
  ctx.clearRect(0, 0, rect.width, rect.height);
  computeImageBox();

  if (!view.imageLoaded) return;

  state.annotations.forEach((annotation) => {
    // Filtered here as well as in drawStaticLayer: without this a hidden
    // annotation would reappear the moment it became selected.
    if (isAnnotationHidden(annotation)) return;
    const isSelected = state.selectedIds.has(annotation.id);
    const isDragging = view.drag?.annotationId === annotation.id || view.drag?.originals?.find(a => a.id === annotation.id);
    if (isSelected || isDragging) {
      drawAnnotation(annotation, isSelected, ctx);
    }
  });

  if (view.drag?.draft) {
    drawAnnotation(view.drag.draft, true, ctx);
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
    const fillColor = label ? hexToRgba(label.color, 0.50) : "rgba(15, 139, 141, 0.50)";

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

export function drawAnnotation(annotation, selected = false, targetCtx = ctx) {
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
    x: view.imageBox.x + point.x * view.imageBox.scale,
    y: view.imageBox.y + point.y * view.imageBox.scale
  }));

  targetCtx.save();
  targetCtx.lineWidth = selected ? 3 : 2;
  targetCtx.strokeStyle = label.color;
  // Fill matches the outline colour but stays well below it in opacity, so the
  // class reads at a glance without obscuring the pixels being annotated.
  targetCtx.fillStyle = hexToRgba(label.color, selected ? 0.65 : 0.50);

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
  const isBeingDrawn = view.drag?.type === "draw-polygon" && view.drag?.annotationId === annotation.id;
  if (screenPoints.length >= 3 && !isBeingDrawn) {
    targetCtx.closePath();
    targetCtx.fill();
  }
  targetCtx.stroke();

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
    drawVertexHandles(screenPoints, label.color, targetCtx, isBeingDrawn);
  }
  targetCtx.restore();
}

export function drawVertexHandles(points, color, targetCtx = ctx, isBeingDrawn = false) {
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

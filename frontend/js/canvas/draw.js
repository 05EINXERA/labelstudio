import { canvas, ctx, imageCanvas, imageCtx, staticCanvas, staticCtx } from "../dom.js?v=4";
import { state, labelById, isAnnotationHidden } from "../state.js?v=7";
import { annotationSettings, annotationOpacity } from "../feature-flags.js?v=1";
import { view } from "./view.js?v=1";
import { annotationPoints, hexToRgba } from "./geometry.js?v=1";
import {
  commentScreenGeometry, COMMENT_FONT, COMMENT_PILL_RADIUS,
  COMMENT_TEXT_INSET_X, COMMENT_TEXT_BASELINE_Y
} from "./comment-geometry.js?v=2";

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
  const rect = imageCanvas.getBoundingClientRect();
  imageCtx.clearRect(0, 0, rect.width, rect.height);
  if (!view.imageLoaded) return;

  // Resampling policy. `scale` is device-independent pixels per image pixel.
  //
  // Smoothing stays ON at every zoom, at "high" quality (the browser default
  // is "low" — a cheap bilinear blend, and the original source of softness).
  //
  // Turning smoothing OFF while magnifying was tried and reverted: it exposes
  // the raw pixel grid, and hard blocky edges are harder to annotate against
  // than a smooth one, not easier. Sharpness at high zoom is bounded by the
  // image's own resolution — past 1:1 there is no detail left to recover, and
  // nearest-neighbour only makes the missing detail louder. The fix for real
  // blur is keeping the backing store matched to the display box (see
  // resizeCanvas in init.js), not disabling interpolation.
  imageCtx.imageSmoothingEnabled = true;
  imageCtx.imageSmoothingQuality = "high";

  let { x, y, width, height } = view.imageBox;

  if (view.imageBox.scale >= 1) {
    // Snap the destination to whole device pixels. imageBox.x/y are arbitrary
    // floats (pan offset + a centring term), and the canvas transform scales
    // them by devicePixelRatio — commonly fractional (1.25 / 1.5) on Windows.
    // Unsnapped, every image pixel straddles a device pixel boundary, so the
    // interpolator blends neighbours even at exactly 1:1 where it should be
    // sampling each source pixel cleanly — softening the image at all zooms.
    //
    // Deliberately snapped HERE and not in computeImageBox: view.imageBox is
    // the one shared coordinate transform, and interactions.js inverts exactly
    // these numbers to turn cursor positions into stored image coordinates
    // (see interactions.js screen->image). Rounding imageBox itself would move
    // that inverse, so identical clicks would map to different saved
    // coordinates depending on pan — annotation drift. The snap is display-only.
    const ratio = window.devicePixelRatio || 1;
    const snap = (v) => Math.round(v * ratio) / ratio;
    x = snap(x);
    y = snap(y);
    width = snap(width);
    height = snap(height);
  }

  imageCtx.drawImage(view.imageElement, x, y, width, height);
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
    // Fill for the shape still being drawn. Set annotationOpacity.drawing equal
    // to .normal if you want no shade change at the moment the polygon closes;
    // it is slightly higher by default so the in-progress shape stands out.
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

export function drawAnnotation(annotation, selected = false, targetCtx = ctx) {
  if (annotation.type === "comment") {
    targetCtx.save();
    // The font is set before measuring: commentScreenGeometry's pill width is
    // only correct if the measurer is using the font the text is painted in.
    targetCtx.font = COMMENT_FONT;
    const { dot, pill, text } = commentScreenGeometry(
      annotation,
      view.imageBox,
      (t) => targetCtx.measureText(t).width
    );

    targetCtx.fillStyle = selected ? "#f4a261" : "#e85d75";
    targetCtx.beginPath();
    targetCtx.arc(dot.cx, dot.cy, dot.r, 0, Math.PI * 2);
    targetCtx.fill();
    targetCtx.strokeStyle = "#ffffff";
    targetCtx.lineWidth = 2;
    targetCtx.stroke();

    targetCtx.fillStyle = "rgba(0,0,0,0.75)";
    targetCtx.beginPath();
    targetCtx.roundRect(pill.x, pill.y, pill.width, pill.height, COMMENT_PILL_RADIUS);
    targetCtx.fill();
    targetCtx.fillStyle = "#ffffff";
    targetCtx.fillText(text, dot.cx + COMMENT_TEXT_INSET_X, dot.cy + COMMENT_TEXT_BASELINE_Y);
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
  targetCtx.fillStyle = hexToRgba(
    label.color,
    selected ? annotationOpacity.selected : annotationOpacity.normal
  );

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
    // The index is bounds-checked as well as compared: it can outlive the
    // polygon it was measured on (selection changing to a smaller shape,
    // points deleted mid-hover) and would then index past screenPoints.
    if (view.hoveredLineIndex !== -1 &&
        view.hoveredLineIndex !== view.selectedLineIndex &&
        view.hoveredLineIndex < screenPoints.length) {
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
  const radius = annotationSettings.vertexHandleRadius;
  targetCtx.strokeStyle = color;
  targetCtx.lineWidth = 2;
  points.forEach((point, i) => {
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

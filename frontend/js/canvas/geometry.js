import { round } from "../utils.js?v=1";

export function annotationPoints(annotation) {
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

export function updateAnnotationBounds(annotation) {
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

export function pointInPolygon(point, polygon) {
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

export function hexToRgba(hex, alpha) {
  const clean = hex.replace("#", "");
  const value = parseInt(clean.length === 3 ? clean.split("").map((c) => c + c).join("") : clean, 16);
  const r = (value >> 16) & 255;
  const g = (value >> 8) & 255;
  const b = value & 255;
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

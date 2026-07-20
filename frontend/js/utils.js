export function generateUUID() {
  if (typeof crypto !== 'undefined' && crypto['randomUUID']) {
    return crypto['randomUUID']();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
    var r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

export function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

export function round(value) {
  return Math.round(value * 100) / 100;
}

export function normalizeClassName(className) {
  return String(className || "object").trim().toLowerCase().replace(/_/g, " ");
}

export function formatClassName(className) {
  return normalizeClassName(className)
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

export function formatTime(secondsToFormat) {
  const h = Math.floor(secondsToFormat / 3600).toString().padStart(2, '0');
  const m = Math.floor((secondsToFormat % 3600) / 60).toString().padStart(2, '0');
  const s = (secondsToFormat % 60).toString().padStart(2, '0');
  return `${h}:${m}:${s}`;
}

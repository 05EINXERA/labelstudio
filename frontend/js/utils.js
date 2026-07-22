export function generateUUID() {
  if (typeof crypto !== 'undefined' && crypto['randomUUID']) {
    return crypto['randomUUID']();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
    var r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

// Escape before interpolating user-controlled text into innerHTML. Project and
// task names come from the database and are rendered as HTML in several tables.
export function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>'"]/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;"
  }[ch]));
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

// HH:MM:SS. Hours are not capped at two digits — a long-running task renders
// as e.g. 145:02:00 rather than silently wrapping (docs/TIMER_AUDIT.md F11).
export function formatTime(secondsToFormat) {
  const total = Math.max(0, Math.floor(secondsToFormat || 0));
  const h = Math.floor(total / 3600).toString().padStart(2, '0');
  const m = Math.floor((total % 3600) / 60).toString().padStart(2, '0');
  const s = (total % 60).toString().padStart(2, '0');
  return `${h}:${m}:${s}`;
}

export function generateUUID() {
  if (typeof crypto !== 'undefined' && crypto['randomUUID']) {
    return crypto['randomUUID']();
  }
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
    var r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
    return v.toString(16);
  });
}

// Identifies this browser tab to the backend's conflict detection, so a write
// is never treated as conflicting with an earlier write from the same tab.
//
// sessionStorage, not localStorage: two tabs on the same machine are two
// genuinely independent editors and must get different ids, which is exactly
// what sessionStorage's per-tab scope gives. It also survives a reload, so a
// refresh does not look like a new client to the server.
const CLIENT_ID_KEY = "annotation-client-id";

export function clientId() {
  // localStorage (not sessionStorage) so the same tab ID survives page
  // reloads. The whole point of client_id is distinguishing "this browser"
  // from "another browser" — a new ID on every reload made the tab conflict
  // with its own previous saves, firing the spurious 409 dialog on load.
  let id = null;
  try {
    id = localStorage.getItem(CLIENT_ID_KEY);
    if (!id) {
      id = generateUUID();
      localStorage.setItem(CLIENT_ID_KEY, id);
    }
  } catch {
    // Storage blocked (private mode, etc.): fall back to an in-memory id.
    // This suppresses self-conflicts within the page's lifetime only.
    if (!window.__annotationClientId) {
      window.__annotationClientId = generateUUID();
    }
    id = window.__annotationClientId;
  }
  return id;
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

/**
 * The canonical form of a class name.
 *
 * **Deliberate mirror of `normalize_label_name` in `formats/common.py`.**
 * There is no build step, so the two cannot share a definition; they are kept
 * in step by `tests/test_label_name_normalization.py`, which runs both over one
 * fixture list. Change one and you must change the other.
 *
 * This used to be `.trim().toLowerCase().replace(/_/g, " ")`, which normalised
 * in the wrong order and left three ways to produce a name the server would
 * store as a distinct class:
 *   "_object_" -> " object "   (underscores became spaces *after* the trim)
 *   "a__b"     -> "a  b"       (a double space is not a double underscore)
 *   "  "       -> ""           (an empty class name, which nothing rejects)
 * Substituting first and collapsing whitespace after fixes all three, and the
 * empty result falls back to "object" as the falsy input already did.
 */
export function normalizeClassName(className) {
  return cleanClassName(className).toLowerCase();
}

/**
 * The stored form of a class name: tidied, **case preserved**.
 *
 * **Deliberate mirror of `clean_label_name` in `formats/common.py`.**
 * Guarded against drift by `tests/test_label_name_normalization.py`.
 *
 * The display casing is data, not decoration: the COCO export writes it into
 * `categories[].name` and the FastLabel export into `title`, and both
 * round-trip through import — so folding "AF Paint" to "af paint" on the way
 * in loses it permanently. Matching is case-insensitive via
 * `normalizeClassName`; storage keeps what the user typed.
 */
export function cleanClassName(className) {
  const out = String(className || "").replace(/_/g, " ").trim()
    .split(/\s+/).join(" ");
  return out || "object";
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

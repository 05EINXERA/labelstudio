/**
 * Client mirror of the image size vocabulary in `app/schemas.py`.
 *
 * There is no build step shared between the backend and this frontend, so the
 * browser needs its own copy to render pills and fill the filter dropdown.
 * This follows the same convention as the TASK_STATUSES mirror, and carries
 * the same two rules:
 *
 *   1. This copy is for RENDERING ONLY.
 *   2. The SERVER resolves the actual filter — it rejects a category it does
 *      not recognise (422) rather than trusting what the client sends.
 *
 * A stale client bundle showing a wrong label is cosmetic. A server that
 * trusted a client-supplied category would not be.
 *
 * `tests/test_image_inventory.py` compares this file against the Python
 * definition and fails on drift, so the two cannot silently diverge.
 */

// Must stay in the same order as schemas.IMAGE_SIZE_CATEGORIES: named sizes
// largest-first, then the two catch-alls.
export const IMAGE_SIZE_CATEGORIES = ["Full", "Half", "Other", "Unknown"];

// The two catch-alls are structurally different and must not be merged.
// "Other" = measured, but not a known size (a fact about the image).
// "Unknown" = no usable dimensions recorded (a gap in our data).
export const IMAGE_SIZE_OTHER = "Other";
export const IMAGE_SIZE_UNKNOWN = "Unknown";

/**
 * Pill modifier class for a size category.
 *
 * Each *named* category gets its own colour so a real answer about the image
 * draws the eye. "Other" and "Unknown" deliberately share one muted neutral
 * style: neither is an answer about the image, so neither should read as a
 * real category. That shared pairing is the one thing here that cannot be
 * derived from the name, which is why this is an explicit map rather than the
 * string-derivation trick `statusPillClass` uses.
 */
export function sizePillClass(category) {
  const c = String(category || "").trim();
  if (!c) return "";
  if (c === IMAGE_SIZE_OTHER || c === IMAGE_SIZE_UNKNOWN) return "is-size-muted";
  return "is-size-" + c.toLowerCase().replace(/[^a-z0-9]+/g, "-");
}

/** Human-readable resolution, or a dash when the row was never measured. */
export function formatResolution(width, height) {
  if (!width || !height || width <= 0 || height <= 0) return "—";
  return `${width} × ${height}`;
}

/**
 * Human-readable file size.
 *
 * `null` renders as a dash, never as "0 B". A missing multi-megabyte original
 * displayed as "0 B" is a quietly wrong number in a report whose entire job is
 * to be right about sizes — and zero is plausible enough that nobody questions
 * it. The dash is the honest rendering of "we could not read this file".
 */
export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return "—";
  const n = Number(bytes);
  if (!isFinite(n) || n < 0) return "—";
  if (n === 0) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.min(Math.floor(Math.log(n) / Math.log(1024)), units.length - 1);
  const value = n / Math.pow(1024, i);
  return `${i === 0 ? value : value.toFixed(1)} ${units[i]}`;
}

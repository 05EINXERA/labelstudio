/**
 * Image resolution categories — the Full / Half vocabulary.
 *
 * ⚠️ THIS IS A DELIBERATE MIRROR OF `formats/image_sizes.py`. ⚠️
 *
 * The project has no build step (rule 13), so the vocabulary cannot be shared
 * between Python and the browser without adding a toolchain the project
 * refuses. `tests/test_image_sizes.py` parses both files and fails if they
 * drift; if you add a size there, add it here.
 *
 * **This copy is for rendering only.** It answers "what pill do I draw?" and
 * "what options go in the select?" — never "which rows may this user see?".
 * The `category` filter is resolved in SQL by the server
 * (`GET /api/projects/{id}/image-info`), so a stale bundle showing the wrong
 * label is a cosmetic bug, never a data-exposure one (rule 18b).
 *
 * `Other` and `Unknown` are not padding — see the Python module's docstring.
 * `Unknown` means "nobody measured this image", which is a prompt to run
 * `scripts/backfill_image_dimensions.py`, not a description of the image.
 */

/** "<w>x<h>" -> category name. Mirrors `SIZE_CATEGORIES`.
 *  Keyed by string because JS objects cannot key on a tuple. */
export const SIZE_CATEGORIES = {
  "5184x3888": "Full",
  "2592x1944": "Half",
};

export const OTHER = "Other";
export const UNKNOWN = "Unknown";

/** Display order for the summary strip and the category select. Mirrors
 *  `CATEGORY_ORDER`. */
export const CATEGORY_ORDER = ["Full", "Half", OTHER, UNKNOWN];

/**
 * The category name for one image's dimensions.
 *
 * Zero counts as absent, matching the Python side: `image_size()` yields (0,0)
 * for an unreadable file, which is the same state as never measured.
 */
export function categorize(width, height) {
  if (!width || !height) return UNKNOWN;
  return SIZE_CATEGORIES[`${width}x${height}`] || OTHER;
}

/**
 * The CSS modifier for a category pill.
 *
 * The two catch-alls share the muted styling deliberately: neither is a real
 * answer about the image, so neither should draw the eye the way a named size
 * does.
 */
export function categoryClass(category) {
  switch (category) {
    case "Full": return "cat-full";
    case "Half": return "cat-half";
    default: return "cat-muted";
  }
}

/** "5184 × 3888", or an em dash when the image was never measured. */
export function formatResolution(width, height) {
  if (!width || !height) return "—";
  return `${width} × ${height}`;
}

/**
 * Bytes as a human-readable size.
 *
 * Returns an em dash for null, which is what the API sends when the file is
 * missing from disk — distinct from "0 bytes", which would be a real (and
 * alarming) measurement.
 */
export function formatBytes(bytes) {
  if (bytes === null || bytes === undefined) return "—";
  if (bytes < 1024) return `${bytes} B`;
  const mb = bytes / (1024 * 1024);
  if (mb < 1) return `${(bytes / 1024).toFixed(0)} KB`;
  if (mb < 1024) return `${mb.toFixed(2)} MB`;
  return `${(mb / 1024).toFixed(2)} GB`;
}

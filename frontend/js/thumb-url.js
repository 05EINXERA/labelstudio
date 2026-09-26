/**
 * thumb-url.js — list-view thumbnails of task images.
 *
 * The Tasks and Move tables used to put the full original upload in a 40 px
 * <img>: p50 9.9 MB and 20 MP each, so a 10-row page cost ~99 MB on the wire
 * and ~800 MB of decoded bitmaps per annotator, on every page, sort, filter
 * and search (.devnotes/frontend-telemetry/07_TASKS_PAGE_THUMBNAILS.md). The
 * server now makes a ~2 KB WebP at /thumbs/<upload name>.
 *
 * THE RULE THIS MODULE EXISTS TO KEEP: a list view never loads the original.
 * A thumbnail that fails becomes a neutral placeholder — it does NOT fall
 * back to /uploads/, which would silently reinstate the whole cost on
 * whichever rows happened to fail. tests/js/thumb_url_spec.mjs pins this.
 * The canvas (init.js) is untouched: annotators still draw on the original.
 *
 * Pure apart from installThumbFallback, which only touches the elements it is
 * given.
 */

/** A neutral grey tile, used when a thumbnail cannot be shown. */
export const THUMB_PLACEHOLDER =
  "data:image/svg+xml," +
  encodeURIComponent(
    '<svg xmlns="http://www.w3.org/2000/svg" width="54" height="40">' +
    '<rect width="54" height="40" rx="4" fill="#8884"/></svg>'
  );

/**
 * The thumbnail URL for a task's stored `image_path` ("uploads/<uuid>.jpg",
 * possibly with Windows separators), or null when there is no image.
 */
export function thumbUrl(imagePath) {
  if (!imagePath) return null;
  const name = String(imagePath).replace(/\\/g, "/").split("/").pop();
  return name ? `/thumbs/${encodeURIComponent(name)}` : null;
}

/**
 * One delegated listener that swaps any failed `img[data-thumb]` under `root`
 * for the placeholder. Capture phase, because `error` does not bubble. No
 * inline onerror, which would block a future CSP (hardening item T-4).
 */
export function installThumbFallback(root) {
  if (!root || root.__thumbFallback) return;
  root.__thumbFallback = true;
  root.addEventListener(
    "error",
    (event) => {
      const img = event.target;
      if (!img || !img.dataset || img.dataset.thumb === undefined) return;
      // An empty src (the edit preview before it is opened) fires `error`
      // too; that is not a failed thumbnail.
      if (typeof img.getAttribute === "function" && !img.getAttribute("src")) return;
      if (img.src === THUMB_PLACEHOLDER) return;   // never loop
      img.src = THUMB_PLACEHOLDER;
      if (img.classList) img.classList.add("task-thumb--missing");
    },
    true
  );
}

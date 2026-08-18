import { stageWrap } from "./dom.js?v=4";

// The #commentOverlay markup ships in app.html on most pages, but this
// fallback injects it if missing. commentOverlay/commentOverlayInput are
// therefore reassigned after the initial querySelector, so — like view.js —
// they're grouped in a mutable object rather than exported as loose bindings,
// which ES module imports treat as read-only.
export const commentOverlayRefs = {
  commentOverlay: document.querySelector("#commentOverlay"),
  commentOverlayInput: document.querySelector("#commentOverlayInput"),
  // Image-space point the overlay is pinned to; see anchorCommentOverlay.
  anchor: null
};

if (!commentOverlayRefs.commentOverlay) {
  const styleHtml = `
    <style>
      .comment-overlay {
        position: absolute;
        top: 0;
        left: 0;
        background-color: var(--panel);
        border: 1px solid var(--line);
        border-radius: 8px;
        padding: 12px;
        width: 240px;
        box-shadow: var(--shadow);
        z-index: 100;
        display: flex;
        flex-direction: column;
        gap: 8px;
      }
      .comment-overlay textarea {
        width: 100%;
        resize: vertical;
        background-color: var(--bg);
        border: 1px solid var(--line);
        border-radius: 4px;
        color: var(--ink);
        padding: 8px;
        font-family: inherit;
        font-size: 0.9rem;
      }
      .comment-overlay textarea:focus {
        outline: none;
        border-color: var(--accent);
        box-shadow: 0 0 0 2px rgba(15, 139, 141, 0.2);
      }
      .comment-overlay.is-hidden {
        display: none;
      }
    </style>
  `;
  document.head.insertAdjacentHTML('beforeend', styleHtml);

  const overlayHtml = `
    <div id="commentOverlay" class="comment-overlay is-hidden">
      <textarea id="commentOverlayInput" placeholder="Enter comment and press Enter..." rows="3"></textarea>
    </div>
  `;
  stageWrap.insertAdjacentHTML('beforeend', overlayHtml);
  commentOverlayRefs.commentOverlay = document.querySelector("#commentOverlay");
  commentOverlayRefs.commentOverlayInput = document.querySelector("#commentOverlayInput");
}

/**
 * Open the overlay to edit an existing comment, anchored over its dot.
 *
 * Shared by every way in: the Edit button in the Objects panel and a
 * double-click on the comment itself. Both used to be judged by eye against
 * the same six lines of positioning, focus and value-seeding; one copy means
 * a second entry point cannot open the overlay slightly differently (leaving
 * pendingCommentEditId unset would turn an edit into a silent no-op on Enter).
 *
 * `imageBox` is passed in rather than imported so this module keeps its single
 * dependency on dom.js and stays usable before the canvas view exists.
 */
export function openCommentEditor(annotation, imageBox, setEditId) {
  if (!annotation || !commentOverlayRefs.commentOverlay) return;
  setEditId(annotation.id);
  commentOverlayRefs.commentOverlayInput.value = annotation.text || "";
  commentOverlayRefs.commentOverlay.classList.remove("is-hidden");
  anchorCommentOverlay({ x: annotation.x, y: annotation.y }, imageBox);
  commentOverlayRefs.commentOverlayInput.focus();
  // Caret at the end, not selecting the whole text: a double-click to edit
  // usually means appending or fixing a word, and a full selection would let
  // the next keystroke wipe the existing comment.
  const end = commentOverlayRefs.commentOverlayInput.value.length;
  commentOverlayRefs.commentOverlayInput.setSelectionRange(end, end);
}

/**
 * Pin the overlay over an image-space point, and remember that point.
 *
 * The overlay is absolutely positioned in screen pixels, but it belongs to a
 * place on the *image*. Those only agree until the view moves: panning or
 * zooming changes the image->screen transform, and an overlay positioned once
 * at click time stays where it was while the image slides out from under it,
 * so a comment being written drifts away from the spot it describes.
 *
 * Storing the anchor here is what makes re-anchoring possible at all — the
 * screen position alone cannot be inverted once the transform has changed.
 * repositionCommentOverlay() replays it on every redraw.
 */
export function anchorCommentOverlay(imagePoint, imageBox) {
  commentOverlayRefs.anchor = { x: imagePoint.x, y: imagePoint.y };
  repositionCommentOverlay(imageBox);
}

/**
 * Re-place the overlay from its stored anchor under the current transform.
 *
 * Called from draw(), which already runs on every pan, zoom and resize, so the
 * overlay tracks the image without needing each of those paths to know it
 * exists. A no-op when the overlay is hidden or was never anchored.
 */
export function repositionCommentOverlay(imageBox) {
  const el = commentOverlayRefs.commentOverlay;
  const anchor = commentOverlayRefs.anchor;
  if (!el || !anchor || el.classList.contains("is-hidden")) return;
  const screenX = imageBox.x + anchor.x * imageBox.scale;
  const screenY = imageBox.y + anchor.y * imageBox.scale;
  el.style.left = `${screenX + 15}px`;
  el.style.top = `${screenY - 15}px`;
}

/** Forget the anchor when the overlay closes, so a stale one cannot resurface. */
export function clearCommentOverlayAnchor() {
  commentOverlayRefs.anchor = null;
}

import { stageWrap } from "./dom.js?v=1";

// The #commentOverlay markup ships in app.html on most pages, but this
// fallback injects it if missing. commentOverlay/commentOverlayInput are
// therefore reassigned after the initial querySelector, so — like view.js —
// they're grouped in a mutable object rather than exported as loose bindings,
// which ES module imports treat as read-only.
export const commentOverlayRefs = {
  commentOverlay: document.querySelector("#commentOverlay"),
  commentOverlayInput: document.querySelector("#commentOverlayInput")
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

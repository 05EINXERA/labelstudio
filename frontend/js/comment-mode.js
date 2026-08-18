/**
 * Pure decision logic for the comment overlay's lifecycle.
 *
 * The overlay is a free-floating textarea over the canvas, and three separate
 * event sources can end its life: Enter (commit), Escape/Backspace (cancel),
 * and a click on the canvas. Getting those wrong is what made comments feel
 * fragile — a click anywhere used to silently discard typed text, and the tool
 * stayed armed so the same click immediately started another comment.
 *
 * The rules live here, apart from the DOM, so they can be asserted directly
 * (tests/js/comment_mode_spec.mjs) instead of only through a live canvas.
 * The handlers in init.js/interactions.js stay thin wrappers around them.
 */

/**
 * Does a canvas click get to act while the comment overlay is open?
 *
 * No, whenever the user has typed something: their text is unsaved work, and a
 * click is not how it is discarded (Enter commits, Escape cancels). Previously
 * the click fell straight through to the canvas, which — with the comment tool
 * still armed — reset the pending point and blanked the textarea, losing the
 * text with no way back.
 *
 * An empty overlay carries nothing to lose, so a click is free to move it.
 */
export function shouldCanvasClickBeBlocked(overlayOpen, text) {
  return !!overlayOpen && String(text ?? "").trim() !== "";
}

/**
 * What should Backspace do inside the comment textarea?
 *
 * "cancel" only from the just-clicked, nothing-typed state, where there is no
 * text to erase and Backspace is the natural "never mind". The moment there is
 * text — including whitespace the user may be mid-word on — Backspace is an
 * editing key and must reach the textarea ("edit"), or it would destroy the
 * comment being written.
 *
 * Deliberately keyed on the field's own emptiness rather than a mode flag:
 * typing then deleting back to empty genuinely returns the user to the
 * nothing-typed state, and one predicate cannot drift out of sync with it.
 *
 * The global Delete/Backspace handler (interactions.js) already ignores events
 * whose target is a textarea, so annotations are never deleted from here.
 */
export function backspaceAction(text) {
  return String(text ?? "") === "" ? "cancel" : "edit";
}

/**
 * The tool state to return to once a comment is committed.
 *
 * Leaving `shape` as "comment" meant the very next click dropped another
 * comment, which is not what finishing one implies. Mirrors what finalizing a
 * polygon does: disarm to "select" so a stray click selects instead of
 * creating, and leave the shape on "polygon" as the ordinary default the user
 * is most likely to want next.
 *
 * `mode` is "select", not "draw": arming the next tool immediately would make
 * a stray click after typing start a polygon — the same accident, moved.
 */
export function modeAfterCommentCommit() {
  return { mode: "select", shape: "polygon" };
}

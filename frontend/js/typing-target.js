/**
 * typing-target.js
 *
 * Decides whether a keyboard event landed somewhere the user is entering text,
 * and so whether the canvas shortcut handler must keep its hands off.
 *
 * Pure apart from reading properties off the passed element: no DOM queries,
 * no `state`, no `view`. Exported separately from interactions.js so the rule
 * is unit-testable without a browser.
 *
 * The distinction that matters: `<input>` is not one control. A text field
 * swallows every printable key and must own them. A RANGE SLIDER swallows
 * nothing — it responds to arrow keys, Home/End and Page Up/Down, and leaves
 * every letter and digit untouched. Treating the two alike meant that after an
 * annotator so much as clicked the opacity slider, the slider kept focus and
 * every canvas shortcut silently died: H to hide, the number keys for class
 * changes, Delete, Ctrl+Z. The shape still looked selected, so nothing on
 * screen explained why the keyboard had stopped working.
 *
 * The same was true of the three AI sliders (magic-wand precision, confidence,
 * NMS) long before the opacity one existed; that group is just disabled in
 * this deployment, so nobody reached it.
 *
 * Kept as an allow-list of non-typing input types rather than a deny-list of
 * typing ones: a type this file has not heard of (`date`, `color`, whatever
 * ships next) is treated as text and keeps its keys, which is the safe
 * direction to be wrong in. A new control losing arrow keys is a small bug; a
 * text field losing every letter is a broken page.
 */

/**
 * `<input type="...">` values that never consume printable characters, and so
 * should not block canvas shortcuts.
 *
 * `checkbox` and `radio` take Space only, `button`/`submit`/`reset` take
 * Enter and Space, and `range` takes arrows and Home/End/PageUp/PageDown.
 * None of them care about letters or digits.
 */
const NON_TYPING_INPUT_TYPES = new Set([
  "range", "checkbox", "radio", "button", "submit", "reset", "file", "image"
]);

/**
 * True when `target` is a control the user types text into, and which should
 * therefore keep the keystroke to itself.
 *
 * Works off duck-typed properties rather than `instanceof`, so it can be
 * tested under node with plain objects — `instanceof HTMLInputElement` needs a
 * real DOM and is what made the original rule untestable.
 *
 * @param {EventTarget|null} target Usually `event.target`.
 * @returns {boolean}
 */
export function isTypingTarget(target) {
  if (!target || typeof target !== "object") return false;

  // contenteditable regions are text entry whatever tag they are on.
  if (target.isContentEditable === true) return true;

  const tag = typeof target.tagName === "string" ? target.tagName.toUpperCase() : "";

  if (tag === "TEXTAREA") return true;
  if (tag === "SELECT") {
    // A select consumes letters for type-ahead option matching, so a shortcut
    // fired while it is open would both jump the list and act on the canvas.
    return true;
  }
  if (tag !== "INPUT") return false;

  // A missing type attribute means `text` — the typing case, so default to
  // blocking rather than allowing.
  const type = typeof target.type === "string" ? target.type.toLowerCase() : "text";
  return !NON_TYPING_INPUT_TYPES.has(type);
}

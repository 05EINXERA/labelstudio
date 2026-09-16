// Keyboard-shortcut decisions for the annotation canvas.
//
// Deliberately pure, in the same spirit as objects-filter.js: no imports, no
// DOM, no `state`. Everything arrives as an argument, so the rules can be
// tested without a browser (tests/js/shortcuts_spec.mjs) and so the handler in
// canvas/interactions.js stays a thin wire-up over decisions that are checked
// in isolation.
//
// The handler itself cannot be unit-tested — it binds to `window` at module
// scope and pulls in the whole canvas DOM — which is exactly why the parts
// worth getting wrong live here instead.

/** How many classes get a single-key binding: 1-9 then 0 for the tenth. */
export const MAX_CLASS_SHORTCUTS = 10;

/**
 * The index into `state.labels` a digit key selects, or -1 for anything else.
 *
 * Keyed on `KeyboardEvent.code`, not `.key`, so the binding follows the
 * physical digit row rather than the character the layout produces — on an AZERTY
 * keyboard `.key` for the unshifted "1" key is "&", which would leave the
 * shortcut silently dead. Numpad digits are accepted for the same reason a
 * numpad user expects them to work.
 *
 * `0` maps to the tenth class because that is where it sits on the keyboard,
 * matching the panel's "10." row. Classes past the tenth have no binding: there
 * is no eleventh digit, and a two-digit type-ahead would make pressing "1"
 * twice in quick succession ambiguous with selecting class 11.
 */
export function labelIndexForCode(code) {
  const match = /^(?:Digit|Numpad)([0-9])$/.exec(code || "");
  if (!match) return -1;
  const digit = Number(match[1]);
  // 1-9 are themselves; 0 is the tenth slot, not the zeroth.
  return digit === 0 ? MAX_CLASS_SHORTCUTS - 1 : digit - 1;
}

/**
 * Every annotation id the "H" key should toggle, given the current selection.
 *
 * Expands the selection to whole groups: a group is drawn, selected and
 * eye-toggled as one unit in the Objects panel, so hiding a single member from
 * the keyboard would leave the row's own eye button disagreeing with what is on
 * screen. Selecting any member therefore hides the lot.
 *
 * Returns [] for an empty selection, which is what makes "a class is highlighted"
 * a no-op: an active class lives in `state.activeLabelId` and never puts
 * anything in `selectedIds`, so there is nothing here to act on.
 *
 * Read-only — neither `annotations` nor the annotations in it are mutated.
 */
export function hideTargetIds(selectedIds, annotations) {
  if (!selectedIds || selectedIds.size === 0) return [];
  const list = Array.isArray(annotations) ? annotations : [];

  const groupIds = new Set();
  list.forEach((a) => {
    if (a && a.groupId && selectedIds.has(a.id)) groupIds.add(a.groupId);
  });

  const ids = new Set(selectedIds);
  list.forEach((a) => {
    if (a && a.groupId && groupIds.has(a.groupId)) ids.add(a.id);
  });
  return Array.from(ids);
}

/**
 * Should "H" hide (true) or show (false) the batch?
 *
 * One direction is chosen for the whole selection from a single representative —
 * the primary annotation when it is part of the batch, otherwise the first id.
 * Deciding per-annotation would invert a mixed selection into a different mixed
 * selection, so pressing H twice would not return to where it started.
 *
 * `isHidden` takes an id. Defaults to hiding when it is missing or tells us
 * nothing, which is the direction the user asked for by pressing the key.
 */
export function shouldHide(ids, primaryId, isHidden) {
  if (!Array.isArray(ids) || ids.length === 0) return false;
  if (typeof isHidden !== "function") return true;
  const representative = ids.includes(primaryId) ? primaryId : ids[0];
  return !isHidden(representative);
}

/**
 * What should an "H" key event do?
 *
 * A tap and a hold are different gestures and cannot share one handler. The
 * original binding toggled on every keydown, so the auto-repeat stream a held
 * key produces inverted the state at the platform's repeat rate (~30/s) — the
 * shapes flickered, and whether they ended up hidden depended on whether the
 * repeat count happened to be odd or even.
 *
 * Splitting them needs no timer, because the browser already tells us: the
 * first keydown of a press has `repeat === false`, every one after it `true`.
 *
 *  - first keydown  -> "toggle"     the sticky behaviour, unchanged.
 *  - a repeat, not yet peeking -> "peek-start"  the key is being held.
 *  - any further repeat -> "none"   idempotent, which is what stops the flicker.
 *  - keyup -> "peek-end" when peeking, else "none" (the tap's release).
 *
 * `peeking` is the caller's current hold state. Keeping it an argument rather
 * than module state leaves this function pure and lets the spec drive a whole
 * press/hold/release sequence through it.
 */
export function hideKeyAction({ type, repeat, peeking } = {}) {
  if (type === "keyup") return peeking ? "peek-end" : "none";
  if (type !== "keydown") return "none";
  if (!repeat) return "toggle";
  return peeking ? "none" : "peek-start";
}

/**
 * Screen-space geometry of a comment annotation: the dot and its text pill.
 *
 * A comment is `{ type: "comment", x, y, text, author }` — a bare image-space
 * point with no `width`/`height` and no `points`. Everything visible about it
 * (an 8px dot, a 24px-tall pill of 12px text) is painted at a *fixed screen
 * size*, unscaled by zoom, so its geometry cannot be expressed in image space
 * the way a box or polygon can.
 *
 * That is exactly why this module exists. draw.js paints from these rects and
 * interactions.js hit-tests against them, so the drawn shape and the clickable
 * shape are the same shape by construction. They were previously independent:
 * the pill was pure paint with no hit-test counterpart at all, and hitTest()
 * fell through to its bbox branch where a missing width/height collapsed the
 * target to a zero-area point — a comment could only be selected by landing on
 * one exact image pixel.
 *
 * Pure: no DOM, no canvas, no `state`/`view` imports. The pill's width needs
 * text measurement, so the measurer is injected rather than imported — that
 * keeps the module testable headlessly (tests/js/comment_geometry_spec.mjs).
 */

// Paint constants. draw.js reads these so the numbers live in one place.
export const COMMENT_DOT_RADIUS = 8;
export const COMMENT_PILL_OFFSET_X = 12;
export const COMMENT_PILL_OFFSET_Y = -12;
export const COMMENT_PILL_HEIGHT = 24;
export const COMMENT_PILL_PADDING = 12;
export const COMMENT_PILL_RADIUS = 4;
export const COMMENT_TEXT_INSET_X = 18;
export const COMMENT_TEXT_BASELINE_Y = 4;
export const COMMENT_FONT = "600 12px Inter, system-ui, sans-serif";

/** The one-line string painted in the pill. */
export function commentPillText(annotation) {
  return `${annotation.author || "User"}: ${annotation.text}`;
}

/**
 * Screen-space geometry for one comment.
 *
 * @param {object} annotation  a `type === "comment"` annotation
 * @param {object} imageBox    view.imageBox — needs `x`, `y`, `scale`
 * @param {(text: string) => number} [measureText]
 *        Returns the painted width of the pill text. Omit it (or pass a
 *        non-function) to get `pill: null` — the caller then has the dot only,
 *        which is the correct degraded answer when no measurer is available.
 * @returns {{ dot: {cx, cy, r}, pill: {x, y, width, height}|null, text: string }}
 */
export function commentScreenGeometry(annotation, imageBox, measureText) {
  const cx = imageBox.x + annotation.x * imageBox.scale;
  const cy = imageBox.y + annotation.y * imageBox.scale;
  const dot = { cx, cy, r: COMMENT_DOT_RADIUS };
  const text = commentPillText(annotation);

  let pill = null;
  if (typeof measureText === "function") {
    pill = {
      x: cx + COMMENT_PILL_OFFSET_X,
      y: cy + COMMENT_PILL_OFFSET_Y,
      width: measureText(text) + COMMENT_PILL_PADDING,
      height: COMMENT_PILL_HEIGHT
    };
  }

  return { dot, pill, text };
}

/**
 * Is a screen-space point on this comment?
 *
 * True anywhere on the dot or anywhere on the pill — the whole comment body is
 * the target, not just the dot. `margin` widens the dot only (a forgiving grab
 * radius, in screen px); the pill is already a large target and is tested at
 * its drawn bounds so that what you see is what you can click, including for a
 * long comment whose pill is wide.
 */
export function commentHitTest(point, annotation, imageBox, measureText, margin = 0) {
  const { dot, pill } = commentScreenGeometry(annotation, imageBox, measureText);

  if (Math.hypot(point.x - dot.cx, point.y - dot.cy) <= dot.r + margin) return true;

  return !!pill &&
    point.x >= pill.x && point.x <= pill.x + pill.width &&
    point.y >= pill.y && point.y <= pill.y + pill.height;
}

/**
 * Move a comment by a screen-space delta, keeping every coordinate it carries
 * in sync.
 *
 * A comment is stored with more than the anchor the canvas draws: `x`/`y` plus
 * a 20x20 `width`/`height` box and a four-point `points` ring (init.js writes
 * all of them when the comment is created). Only `x`/`y` are painted, so a
 * move that updated just those would leave `points` behind at the old
 * location — invisible on screen, but it is what the exporters and the generic
 * bounds/vertex machinery read. Translating everything together keeps the
 * stored shape and the drawn dot describing the same place.
 *
 * `dx`/`dy` are in image space. Mutates `annotation` and returns it.
 */
export function translateComment(annotation, dx, dy) {
  annotation.x += dx;
  annotation.y += dy;
  if (Array.isArray(annotation.points)) {
    annotation.points = annotation.points.map((p) => ({ x: p.x + dx, y: p.y + dy }));
  }
  return annotation;
}

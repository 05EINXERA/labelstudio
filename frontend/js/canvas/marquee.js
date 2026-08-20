/**
 * Which annotations a marquee (Shift + left-drag) rectangle selects.
 *
 * Deliberately pure: no DOM, no canvas, no `state`/`view` imports. Geometry
 * goes in, ids come out, and nothing here can reach `state.annotations` to
 * mutate it. That is the same discipline `objects-filter.js` follows and for
 * the same reason — a selection rule that could touch the annotation set is
 * one bug away from a user dragging a box and saving over their work. The
 * hidden-ness predicate and the comment's screen geometry are injected rather
 * than imported, which keeps the module testable headlessly
 * (tests/js/marquee_spec.mjs).
 *
 * See .devnotes/drag-selection/01_DESIGN.md § 4.
 */
import { pointInPolygon } from "./geometry.js?v=1";

/**
 * A normalised rect from two corner points, so dragging in any direction gives
 * the same rectangle. Coordinates are whatever space the caller passes in —
 * interactions.js works in image space, so pan/zoom cannot desync the rect
 * from the shapes it is testing against.
 */
export function normalizeRect(a, b) {
  return {
    x1: Math.min(a.x, b.x),
    y1: Math.min(a.y, b.y),
    x2: Math.max(a.x, b.x),
    y2: Math.max(a.y, b.y)
  };
}

/**
 * Is this rect too small to be a deliberate drag?
 *
 * `rect` is in image space and `scale` is image-pixels-to-screen-pixels
 * (view.imageBox.scale), so the threshold stays a constant physical size on
 * screen however far the annotator is zoomed in — the same conversion
 * hitTestPoint and hitTestLine already do for their grab radii.
 *
 * A degenerate marquee is a Shift+click that twitched, and the caller treats
 * it as such: the existing selection is left exactly as it was, rather than
 * being replaced by whatever a 1px box happened to touch.
 */
export function rectIsDegenerate(rect, scale, minScreenPx = 3) {
  if (!rect) return true;
  const s = Number(scale) || 1;
  return (rect.x2 - rect.x1) * s < minScreenPx && (rect.y2 - rect.y1) * s < minScreenPx;
}

/** Is a point inside (or on the edge of) the rect? */
function pointInRect(point, rect) {
  return point.x >= rect.x1 && point.x <= rect.x2 &&
    point.y >= rect.y1 && point.y <= rect.y2;
}

/** Do two axis-aligned rects overlap at all? */
function rectsOverlap(a, b) {
  return a.x1 <= b.x2 && a.x2 >= b.x1 && a.y1 <= b.y2 && a.y2 >= b.y1;
}

/** Orientation sign of the triple (p, q, r): >0 ccw, <0 cw, 0 collinear. */
function cross(p, q, r) {
  return (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x);
}

/**
 * Do segments p1p2 and q1q2 cross?
 *
 * The plain straddle test, without the collinear-overlap special cases: a
 * marquee edge exactly collinear with a polygon edge is already caught by the
 * vertex-in-rect and corner-in-polygon tests in all but measure-zero cases,
 * and selection is forgiving by nature — a missed pixel-perfect collinearity
 * costs the user one extra drag, while the extra branches cost every caller
 * on every edge of every polygon.
 */
function segmentsIntersect(p1, p2, q1, q2) {
  const d1 = cross(q1, q2, p1);
  const d2 = cross(q1, q2, p2);
  const d3 = cross(p1, p2, q1);
  const d4 = cross(p1, p2, q2);
  return ((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) &&
    ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0));
}

/** The rect's four corners, in ring order. */
function rectCorners(rect) {
  return [
    { x: rect.x1, y: rect.y1 },
    { x: rect.x2, y: rect.y1 },
    { x: rect.x2, y: rect.y2 },
    { x: rect.x1, y: rect.y2 }
  ];
}

/**
 * Does this annotation intersect the rect?
 *
 * *Intersect*, not *contain*: on a zoomed-in canvas most objects extend past
 * the viewport, so a contain-only rule would select nothing and read as
 * broken (design § 4.1).
 *
 * Cheapest test first. The bounding box (`x`/`y`/`width`/`height`, kept
 * current by updateAnnotationBounds) rejects most candidates outright and is
 * the whole answer for a box. A polygon then needs three more questions, each
 * catching a case the others miss:
 *
 *   - a vertex inside the rect — the ordinary overlap;
 *   - a rect corner inside the polygon — a small marquee drawn entirely
 *     within one large polygon, which has no vertex anywhere near it;
 *   - an edge crossing — a polygon that spans the rect from side to side
 *     without putting a vertex in it.
 *
 * Comments are not handled here: nothing paints their stored 20x20 box, so
 * they are decided in screen space by marqueeHits' injected `commentRect`.
 */
export function annotationIntersectsRect(annotation, rect) {
  if (!annotation || !rect) return false;

  const points = Array.isArray(annotation.points) ? annotation.points : null;

  const bounds = boundsOf(annotation, points);
  if (!bounds || !rectsOverlap(bounds, rect)) return false;

  // A box is fully described by its bounds, so the overlap above is the answer.
  // Anything with fewer than 3 points has no interior to test either.
  const isPolygon = annotation.type === "polygon" ||
    (points && points.length !== 4 && points.length >= 3);
  if (!isPolygon || !points) return true;

  if (points.some((point) => pointInRect(point, rect))) return true;

  const corners = rectCorners(rect);
  if (corners.some((corner) => pointInPolygon(corner, points))) return true;

  for (let i = 0; i < points.length; i += 1) {
    const p1 = points[i];
    const p2 = points[(i + 1) % points.length];
    for (let c = 0; c < corners.length; c += 1) {
      if (segmentsIntersect(p1, p2, corners[c], corners[(c + 1) % corners.length])) return true;
    }
  }

  return false;
}

/**
 * The annotation's axis-aligned bounds, preferring its points over the stored
 * x/y/width/height. The stored bounds are normally in sync, but deriving them
 * from the geometry that is actually tested below keeps the reject step from
 * ever discarding a shape whose cached bounds lagged an edit.
 */
function boundsOf(annotation, points) {
  if (points && points.length) {
    const xs = points.map((p) => Number(p.x) || 0);
    const ys = points.map((p) => Number(p.y) || 0);
    return {
      x1: Math.min(...xs), y1: Math.min(...ys),
      x2: Math.max(...xs), y2: Math.max(...ys)
    };
  }
  if (typeof annotation.x !== "number" || typeof annotation.y !== "number") return null;
  const x = annotation.x;
  const y = annotation.y;
  return {
    x1: x, y1: y,
    x2: x + (Number(annotation.width) || 0),
    y2: y + (Number(annotation.height) || 0)
  };
}

/**
 * Does the rect cover a comment's *drawn* body — the dot or the pill?
 *
 * `geometry` is what commentScreenGeometry returns, in screen space, so the
 * rect must be in screen space too. The dot is tested as its bounding square
 * rather than as a circle: it is 8px across, and the corner-vs-arc difference
 * is invisible at that size while the square is a great deal cheaper to say.
 */
function rectCoversComment(rect, geometry) {
  if (!geometry) return false;
  const { dot, pill } = geometry;
  if (dot && rectsOverlap(
    { x1: dot.cx - dot.r, y1: dot.cy - dot.r, x2: dot.cx + dot.r, y2: dot.cy + dot.r },
    rect
  )) return true;
  return !!pill && rectsOverlap(
    { x1: pill.x, y1: pill.y, x2: pill.x + pill.width, y2: pill.y + pill.height },
    rect
  );
}

/**
 * Every annotation the marquee selects, as an array of ids in `annotations`
 * order.
 *
 * @param {Array} annotations   state.annotations (never mutated)
 * @param {object} rect         normalised, image space
 * @param {object} options
 * @param {(a) => boolean} [options.isHidden]
 *        Hidden annotations are not on screen, so they must not be selectable
 *        — the same rule hitTest applies to clicks. Without it a marquee would
 *        quietly hand the user objects they cannot see, to then group, merge
 *        or delete.
 * @param {(a) => object} [options.commentRect]
 *        Screen-space geometry for one comment (commentScreenGeometry).
 * @param {object} [options.screenRect]
 *        `rect` converted to screen space, for the comment test. Comments are
 *        skipped entirely when either this or `commentRect` is missing —
 *        the honest answer when we cannot say where they are drawn.
 */
export function marqueeHits(annotations, rect, options = {}) {
  if (!Array.isArray(annotations) || !rect) return [];
  const { isHidden, commentRect, screenRect } = options;

  const hits = [];
  annotations.forEach((annotation) => {
    if (!annotation) return;
    if (typeof isHidden === "function" && isHidden(annotation)) return;

    if (annotation.type === "comment") {
      if (typeof commentRect !== "function" || !screenRect) return;
      if (rectCoversComment(screenRect, commentRect(annotation))) hits.push(annotation.id);
      return;
    }

    if (annotationIntersectsRect(annotation, rect)) hits.push(annotation.id);
  });
  return hits;
}

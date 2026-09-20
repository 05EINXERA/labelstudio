/**
 * feature-flags.js
 *
 * Single source of truth for which toolbar sections are available in this
 * deployment.  Set a flag to `false` to disable (visually block) that entire
 * section of the toolbar.  `renderControls()` in workspace.js reads these
 * values on every render cycle, so a change here is picked up immediately
 * without any additional wiring.
 *
 * Flags:
 *   smooth  — the FFT Smooth group (Smooth button, strength slider,
 *              auto-smooth toggle).  Requires the FFT smooth module.
 *   ai      — the AI group (AI Settings dropdown, Detect button,
 *              Auto-Tag button, Magic Wand tool button).
 *              Set to false when no ML back-end is available.
 */
export const toolAvailability = {
  smooth: true,
  ai: true,
};

/**
 * Annotation drawing/interaction tunables.
 *
 * Same contract as `toolAvailability`: edit a value here and it takes effect
 * on the next draw or the next pointer event — no other file needs touching.
 * Each value is read at its single point of use, so nothing has to be threaded
 * through call chains.
 *
 * vertexHandleRadius
 *   Radius, in on-screen pixels, of the round vertex handles DRAWN on a
 *   selected shape at the default (fit-to-window) zoom. The radius actually
 *   used is this value passed through `zoomScaledRadius()`, which shrinks the
 *   handles as the annotator zooms in: at high zoom the annotator is working
 *   on individual pixels, and a full-size handle covers exactly the detail
 *   they are trying to place. Lower it when vertices sit close together and
 *   the handles overlap each other.
 *
 * vertexGrabRadius
 *   Radius, in on-screen pixels, within which a click counts as grabbing a
 *   vertex, at the default zoom. Kept independent of (and by default LARGER
 *   than) the drawn radius: a forgiving click target makes vertices easy to
 *   catch without drawing handles big enough to hide the pixels underneath.
 *   Also passed through `zoomScaledRadius()`, so the grab area tracks the
 *   handle the annotator can actually see. Raise it for touch or pen input;
 *   if it exceeds roughly half the spacing between neighbouring vertices,
 *   adjacent grab areas start to overlap and the wrong vertex wins.
 *
 * vertexZoomShrink
 *   How strongly zoom shrinks the two radii above, 0 to 1. 0 = no shrink
 *   (constant on-screen size at every zoom, the old behaviour); 1 = the
 *   handle is pinned to the image, shrinking on screen in exact proportion
 *   to the zoom. Values in between shrink sub-linearly, which keeps handles
 *   grabbable while still uncovering the pixels underneath.
 *
 * vertexMinRadius
 *   Floor, in on-screen pixels, for the shrunk radii. Without it deep zoom
 *   would shrink handles until they are invisible and impossible to hit.
 *
 * edgeGrabRadius
 *   Radius, in on-screen pixels, within which a click counts as landing on a
 *   polygon EDGE (used to insert or select a segment) rather than on empty
 *   space. Keep at or below vertexGrabRadius — when the two compete the vertex
 *   should win, since dragging a corner is the more common intent.
 *
 * freehandPointSpacing
 *   Minimum distance, in on-screen pixels, the cursor must travel before
 *   freehand drag-draw commits another polygon point. This is the "how many
 *   points get laid down" control: SMALLER = points emitted more often =
 *   denser, smoother outlines but heavier annotations (more vertices to
 *   store, render and hit-test); LARGER = sparser, coarser, cheaper traces.
 *   Measured per on-screen pixel, so tracing at high zoom naturally yields
 *   finer detail without changing this number.
 */
export const annotationSettings = {
  vertexHandleRadius: 4.5,
  vertexGrabRadius: 6,
  edgeGrabRadius: 6,
  freehandPointSpacing: 10,
  vertexZoomShrink: 0.6,
  vertexMinRadius: 1.5,
};

/**
 * Shrink an on-screen radius as the view zooms in.
 *
 * `zoom` is `view.viewZoom`: 1 at fit-to-window, higher when zoomed in. At or
 * below 1 the base radius is returned untouched — zooming OUT must not grow
 * handles, which would bury a small shape under its own vertices. Above 1 the
 * radius is divided by `zoom ** vertexZoomShrink`, then clamped to
 * `vertexMinRadius` so a handle never shrinks out of existence.
 *
 * Kept here, next to the tunables it reads, so the drawn handle (draw.js) and
 * its click target (interactions.js) can never drift apart.
 */
export function zoomScaledRadius(baseRadius, zoom) {
  if (!Number.isFinite(zoom) || zoom <= 1) return baseRadius;
  const shrunk = baseRadius / Math.pow(zoom, annotationSettings.vertexZoomShrink);
  return Math.max(annotationSettings.vertexMinRadius, shrunk);
}

/**
 * Annotation fill opacity, 0 (invisible) to 1 (opaque).
 *
 * The outline is always drawn at full strength; only the interior fill uses
 * these. The fill exists to make a shape's class readable at a glance, so it
 * has to stay light enough to see the pixels underneath — which is exactly
 * what annotators are judging. Lower both values when working on fine detail
 * or dark imagery; raise them when shapes are small and hard to spot.
 *
 * `selected` is deliberately the higher of the two: the active shape should
 * separate from its neighbours without any other visual cue.
 */
export const annotationOpacity = {
  normal: 0.35,
  selected: 0.5,
  /** Fill for the in-progress shape being drawn (before it is committed). */
  drawing: 0.5,
};

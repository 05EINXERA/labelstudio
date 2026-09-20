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
 * selectedEdgeWidth
 *   The `lineWidth` draw.js strokes a SELECTED shape's outline at. Vertex
 *   handles are only ever drawn on a selected shape, so this is the line a
 *   handle has to stay distinguishable from. Mirrored here (rather than read
 *   from draw.js) so the floor below can be derived from it; if the stroke
 *   width in drawAnnotation() changes, change this with it.
 *
 * vertexMinRadiusEdgeRatio
 *   The real floor on the shrunk radii, expressed as a multiple of the
 *   selected edge's HALF-width. This is the constraint that matters: a handle
 *   whose radius merely equals the half-width (ratio 1) is exactly as wide as
 *   the line it sits on, so it reads as a bump in the outline rather than as a
 *   grabbable corner. The ratio must stay comfortably above 1 — at 2 the
 *   handle is twice the line's half-width, which together with its own 2px
 *   stroke keeps a visible disc of white proud of the edge at any zoom.
 *   Raising the ratio makes deep-zoom handles more prominent but starts to
 *   cover the pixels the annotator is placing; that trade-off, not
 *   invisibility, is the reason not to raise it much further.
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
  selectedEdgeWidth: 3,
  vertexMinRadiusEdgeRatio: 2,
};

/**
 * The smallest on-screen radius a vertex handle may shrink to.
 *
 * Derived, not hand-tuned: below roughly the selected outline's half-width a
 * handle stops reading as a corner and merges into the edge it sits on, which
 * is precisely the thing an annotator needs to be able to pick out in order to
 * drag it. The floor is that half-width times `vertexMinRadiusEdgeRatio`.
 */
export function minVertexRadius() {
  const edgeHalfWidth = annotationSettings.selectedEdgeWidth / 2;
  return edgeHalfWidth * annotationSettings.vertexMinRadiusEdgeRatio;
}

/**
 * Shrink an on-screen radius as the view zooms in.
 *
 * `zoom` is `view.viewZoom`: 1 at fit-to-window, higher when zoomed in. At or
 * below 1 the base radius is returned untouched — zooming OUT must not grow
 * handles, which would bury a small shape under its own vertices. Above 1 the
 * radius is divided by `zoom ** vertexZoomShrink`, then clamped to
 * `minVertexRadius()` so a handle can never shrink to the point where it is
 * indistinguishable from the outline it sits on.
 *
 * The clamp is applied to the base radius too, so a base smaller than the
 * floor is raised to it rather than being silently trusted.
 *
 * Kept here, next to the tunables it reads, so the drawn handle (draw.js) and
 * its click target (interactions.js) can never drift apart.
 */
export function zoomScaledRadius(baseRadius, zoom) {
  const floor = minVertexRadius();
  if (!Number.isFinite(zoom) || zoom <= 1) return Math.max(floor, baseRadius);
  const shrunk = baseRadius / Math.pow(zoom, annotationSettings.vertexZoomShrink);
  return Math.max(floor, shrunk);
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

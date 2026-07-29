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
 *   selected shape. Screen-space on purpose: a handle stays the same physical
 *   size at every zoom, so it neither disappears when zoomed out nor swallows
 *   the shape when zoomed in. Lower it when vertices sit close together and
 *   the handles overlap each other.
 *
 * vertexGrabRadius
 *   Radius, in on-screen pixels, within which a click counts as grabbing a
 *   vertex. Kept independent of (and by default LARGER than) the drawn radius:
 *   a forgiving click target makes vertices easy to catch without drawing
 *   handles big enough to hide the pixels underneath. Raise it for touch or
 *   pen input; if it exceeds roughly half the spacing between neighbouring
 *   vertices, adjacent grab areas start to overlap and the wrong vertex wins.
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
};

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
  normal: 0.5,
  selected: 0.6,
  /** Fill for the in-progress shape being drawn (before it is committed). */
  drawing: 0.5,
};

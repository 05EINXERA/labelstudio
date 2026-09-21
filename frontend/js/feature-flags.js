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
 *   ai      — the AI group (AI Settings dropdown, Detect button,
 *              Auto-Tag button, Magic Wand tool button).
 *              Set to false when no ML back-end is available.
 */
export const toolAvailability = {
  ai: false,
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
 *   selected shape, AT FIT ZOOM (viewZoom === 1). Above that the handles
 *   shrink along the curve described under vertexHandleFalloff. Lower this
 *   when vertices sit close together and the handles overlap each other.
 *
 *   Screen pixels, not image pixels: handles must not scale 1:1 with the
 *   image, or they would vanish when zoomed out. But a FIXED screen size was
 *   wrong in the other direction — a handle that stays this size at 4000%
 *   covers exactly the pixels the annotator is trying to judge, and a dense
 *   polygon's handles merge into a chain of white beads. Hence the falloff
 *   below.
 *
 *   Also sets the ring width: a handle at fit zoom gets a 2px ring, and
 *   thinner rings as it shrinks (canvas/handle-size.js derives the divisor
 *   from this value, so the proportion holds if you change it).
 *
 * vertexHandleFalloff
 *   Exponent controlling how fast the drawn handle shrinks as the user zooms:
 *
 *       radius = clamp(min, vertexHandleRadius / viewZoom ** falloff, max)
 *
 *   0 disables the effect entirely (constant screen size — the behaviour
 *   before this was added). 1 would shrink in exact proportion to zoom, which
 *   is far too aggressive. Values around 0.3-0.4 keep the handle visible while
 *   noticeably getting out of the way. Raise it to shrink harder.
 *
 *   Keyed off `view.viewZoom`, NOT `view.imageBox.scale`. Scale folds in
 *   baseScale, which depends on the image's natural size versus the canvas
 *   box — keying off it would give a 6000px photo and a 400px thumbnail
 *   different handle sizes at the same "fit" view, and would resize the
 *   handles when the browser window resized. viewZoom is 1 at fit for every
 *   image, so the curve tracks the user's zoom gesture and nothing else.
 *
 * vertexHandleMinRadius
 *   Floor for the above, in on-screen pixels. This is the "never lost
 *   visually" guarantee: past roughly viewZoom 6 the handle stops shrinking
 *   and holds this size all the way to maximum zoom. Raise it if handles read
 *   poorly against the 3px selected outline they sit on.
 *
 * vertexHandleMaxRadius
 *   Ceiling for the above, in on-screen pixels, which bites when zoomed OUT
 *   past fit (viewZoom < 1). Keep it at or below vertexGrabRadius: a handle
 *   drawn larger than its own click target would be visible but unclickable
 *   around its rim. tests/js/handle_size_spec.mjs asserts this.
 *
 * vertexGrabRadius
 *   Radius, in on-screen pixels, within which a click counts as grabbing a
 *   vertex. Kept independent of, and never smaller than, the drawn radius: a
 *   forgiving click target makes vertices easy to catch without drawing
 *   handles big enough to hide the pixels underneath. It equals
 *   vertexHandleMaxRadius, so the two coincide at full zoom-out and the grab
 *   area is strictly larger everywhere else. Raise it for touch or pen
 *   input; if it exceeds roughly half the spacing between neighbouring
 *   vertices, adjacent grab areas start to overlap and the wrong vertex wins.
 *
 *   Deliberately NOT subject to vertexHandleFalloff: the grab area is
 *   invisible, so there is no visual cost to leaving it generous, and
 *   shrinking the click target would make vertices hardest to catch at
 *   exactly the zoom where precision work happens. At high zoom the grab
 *   area is therefore larger than the drawn circle. That is intended.
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
  vertexHandleRadius:    5.6,
  vertexHandleFalloff:   0.35,
  vertexHandleMinRadius: 3,
  vertexHandleMaxRadius: 7.5,
  vertexGrabRadius:      7.5,
  edgeGrabRadius:        6,
  freehandPointSpacing:  10,
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
 *
 * `selected` is ALSO the default position of the toolbar's Opacity slider,
 * which writes back to it live as the user drags (opacity-controls.js). That
 * makes this value the single source of truth for the slider's starting point
 * — app.html carries a hardcoded 60% fallback for the no-JS case, and
 * tests/js/opacity_slider_spec.mjs fails if the two drift apart.
 *
 * `drawing` is driven by the same slider, keeping whatever RATIO it has to
 * `selected` here (0.30 / 0.60 = half). Annotators thin the fill while
 * tracing, not only after the shape closes. Change either number and the
 * ratio changes with it — that is intended, and it is why opacity-scale.js
 * derives the ratio instead of hardcoding 0.5. Keep `drawing` below
 * `selected` unless you want the in-progress and committed states to look
 * identical at the moment a polygon closes.
 *
 * `normal` is deliberately NOT on the slider: it paints the static canvas
 * layer, so binding it would turn every slider input event into a full static
 * repaint.
 *
 * The slider is session-only: it mutates this object and nothing else, so a
 * reload re-evaluates this module and the defaults are back. Do not add
 * persistence without revisiting that contract.
 */
export const annotationOpacity = {
  normal:   0.5,
  selected: 0.6,
  /** Fill for the in-progress shape being drawn (before it is committed). */
  drawing:  0.30,
};

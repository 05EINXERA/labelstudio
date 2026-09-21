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
 *   used is this passed through `zoomScaledRadius()`, which scales it with the
 *   zoom in both directions, so this is the size at 1x and the anchor the
 *   floor and ceiling are measured against.
 *
 *   Currently 5: a 10px-wide dot. With `vertexZoomScale` at 0 this is the
 *   size at EVERY zoom, not just at fit-to-window, so it is the only number
 *   that decides how big handles look.
 *
 *   It is bounded from both sides. Too large and handles cover the detail
 *   being annotated — which a constant size makes worse at deep zoom, since
 *   nothing shrinks out of the way. Too small and a handle stops reading as a
 *   grabbable corner against the 3px outline. It is also capped by the
 *   freehand invariant: `vertexGrabRadius` must stay under half of
 *   `freehandPointSpacing`, and raising the spacing to make room is what
 *   annotators see as the gap between vertices growing. 5 is the value that
 *   satisfies all three.
 *
 * vertexGrabRadius
 *   Radius, in on-screen pixels, within which a click counts as grabbing a
 *   vertex, at the default zoom. Kept independent of (and by default LARGER
 *   than) the drawn radius: a forgiving click target makes vertices easy to
 *   catch without drawing handles big enough to hide the pixels underneath.
 *   Tracks the drawn handle via `vertexGrabScreenRadius()`, so the grab area
 *   follows the handle the annotator can actually see, subject to
 *   `maxGrabRadiusImagePx` below. Raise it for touch or pen input;
 *   if it exceeds roughly half the spacing between neighbouring vertices,
 *   adjacent grab areas start to overlap and the wrong vertex wins. That is
 *   the constraint that sets the current 5: it must stay under half of
 *   `freehandPointSpacing`, or every freehand-traced outline has overlapping
 *   grab zones by construction.
 *
 *   The two move together, but only DOWNWARD. Raising this forces the spacing
 *   up to match, and a larger spacing is what annotators see as the distance
 *   between vertices growing and traced outlines turning coarse — this pair
 *   was briefly 7.5-and-15 and was reported exactly that way. 5-and-10 is the
 *   established pair; treat 10 as a ceiling on the spacing, not a starting
 *   point. A test enforces both halves.
 *
 * vertexZoomScale
 *   How zoom scales the two radii above, as an EXPONENT on the zoom factor.
 *   The sign sets the direction and the magnitude sets the strength:
 *
 *     negative  handles SHRINK as the annotator zooms in.
 *     zero      constant on-screen size at every zoom.
 *     positive  handles GROW as the annotator zooms in; 1 is exact
 *               image-pinned tracking.
 *
 *   Currently 0, and that is a settled decision rather than a default. Both
 *   other directions were tried and annotators objected to both:
 *
 *     -0.6, then -0.5  handles shrank on zoom-in so they would stop covering
 *                      the pixels being annotated. Reported as vertices
 *                      "becoming smaller" — twice, at two different floors.
 *                      The second attempt shrank LESS than the first and was
 *                      still reported, which is the useful datum: the
 *                      objection is to the size changing, not to how small it
 *                      gets.
 *     +0.5, then +1    handles grew on zoom-in, tracking the image. Reported
 *                      as too big; at +1 it drew 40px discs over the work.
 *
 *   A constant size is the one behaviour nobody can report as the handles
 *   changing under them. The cost is real and accepted: a constant handle
 *   covers more image detail the further in the annotator zooms, which is the
 *   very thing the negative values were trying to fix. `vertexHandleRadius` is
 *   kept small enough that this stays tolerable (a 10px dot spans about 1.25
 *   image pixels at 8x), and a test bounds it.
 *
 *   Before changing this again: the complaint that motivates a change is
 *   usually "handles cover the detail" (wants negative) or "handles are too
 *   big" (wants a smaller base, NOT positive). Neither is well served by
 *   reintroducing zoom-dependent sizing — that has now been tried four times.
 *
 * vertexMaxRadiusBaseRatio
 *   Ceiling on the scaled radii, as a multiple of the BASE radius. Bounds
 *   whichever end of the zoom range GROWS: with the current negative
 *   `vertexZoomScale` that is the zoomed-OUT end, where the inverse exponent
 *   would otherwise inflate handles without limit as the view pulls back
 *   (10px radius at 0.25x, 16px at 0.1x) and bury a small shape under its own
 *   corners.
 *
 *   Inert while `vertexZoomScale` is 0, since nothing scales: the base sits
 *   strictly between this ceiling and the floor at every zoom, and a test
 *   asserts that neither clamp is secretly dictating handle size. Kept so that
 *   re-enabling zoom scaling is one number away and bounded from the start.
 *
 *   Expressed against the base rather than as flat pixels so it moves with
 *   `vertexHandleRadius` instead of silently overriding it.
 *
 * minGrabScreenRadius
 *   Floor, in on-screen pixels, under the vertex CLICK target — independent of
 *   the drawn dot. It exists so the dot could shrink on zoom-in without making
 *   vertices harder to grab.
 *
 *   Set equal to `vertexGrabRadius` (5). While `vertexZoomScale` is 0 nothing
 *   scales below it anyway, so this is inert — it is kept so the click target
 *   stays comfortable if zoom scaling is ever re-enabled. It is bounded above
 *   by `maxGrabRadiusImagePx`, so raising it cannot make neighbouring vertices
 *   swallow each other when zoomed out.
 *
 * maxGrabRadiusImagePx
 *   Hard cap on how far a vertex grab may reach, measured in IMAGE pixels
 *   rather than screen pixels. Guards the zoomed-OUT end, where one screen
 *   pixel spans many image pixels: `minGrabScreenRadius` is a generous target
 *   on screen, but at 0.25x that same radius reaches 20 image pixels and
 *   swallows every vertex near the click — the effect annotators described as
 *   vertices "snapping like a magnet".
 *
 *   Zoomed IN it does not bind, because the drawn radius is shrinking and the
 *   grab radius is held at a constant number of SCREEN pixels, so its reach in
 *   image space falls away on its own (about 0.6 image px at 8x). That is the
 *   opposite of when radii grew with zoom, where this cap was the whole
 *   precision mechanism; it is now only a zoom-out guard.
 *
 *   At 6 the grab area never claims more than a 6-image-pixel radius, so on a
 *   shape viewed small the annotator can still separate neighbouring vertices
 *   by zooming in a little. Scaled with the rest of the curve. Lower it for finer placement when zoomed out, at
 *   the cost of a fiddlier click target there. It may never pull the grab area
 *   below the drawn dot.
 *
 * selectedEdgeWidth
 *   The `lineWidth` draw.js strokes a SELECTED shape's outline at. Vertex
 *   handles are only ever drawn on a selected shape, so this is the line a
 *   handle has to stay distinguishable from. Mirrored here (rather than read
 *   from draw.js) so the floor below can be derived from it; if the stroke
 *   width in drawAnnotation() changes, change this with it.
 *
 * vertexMinRadiusEdgeRatio
 *   The floor on the shrunk radii, expressed as a multiple of the
 *   selected edge's HALF-width. This is the constraint that matters: a handle
 *   whose radius merely equals the half-width (ratio 1) is exactly as wide as
 *   the line it sits on, so it reads as a bump in the outline rather than as a
 *   grabbable corner, so the ratio must stay comfortably above 1.
 *
 *   Expressed as a ratio rather than a flat pixel count so the floor tracks
 *   `selectedEdgeWidth`: thin the outline and handles may scale down further,
 *   thicken it and they stop sooner, with the "distinguishable from the edge"
 *   guarantee holding either way.
 *
 *   At the current 3px edge, 1.67 puts the floor at ~2.5px. Inert while
 *   `vertexZoomScale` is 0, since nothing scales down to reach it; it bounds
 *   any future shrink rather than the current size.
 *
 *   Its purpose, should scaling return: an early shrink attempt bottomed out
 *   at a 3px radius against a 3px-wide outline, so the handle read as a bump
 *   in the line rather than a corner. Keeping this floor above the edge's
 *   half-width is what would make shrinking safe.
 *
 * edgeGrabRadius
 *   Radius, in on-screen pixels, within which a click counts as landing on a
 *   polygon EDGE (used to insert or select a segment) rather than on empty
 *   space. Currently EQUAL to vertexGrabRadius, which is fine because the two
 *   never actually compete on distance: the vertex test runs first and returns
 *   early (see hitTestPoint/hitTestLine call order in interactions.js), so a
 *   corner always wins a tie. Do not raise it above vertexGrabRadius — the
 *   edge would then claim ground outside any handle that the annotator has no
 *   visual cue for.
 *
 * freehandPointSpacing
 *   Minimum distance, in on-screen pixels, the cursor must travel before
 *   freehand drag-draw commits another polygon point. This is the "how many
 *   points get laid down" control: SMALLER = points emitted more often =
 *   denser, smoother outlines but heavier annotations (more vertices to
 *   store, render and hit-test); LARGER = sparser, coarser, cheaper traces.
 *   Measured per on-screen pixel, so tracing at high zoom naturally yields
 *   finer detail without changing this number. Keep it above twice
 *   `vertexGrabRadius` (see above): below that, freehand emits points closer
 *   together than the grab test can tell apart, and editing a traced shape
 *   grabs whichever neighbour happens to win.
 */
export const annotationSettings = {
  vertexHandleRadius: 5,
  vertexGrabRadius: 5,
  edgeGrabRadius: 5,
  freehandPointSpacing: 10,
  vertexZoomScale: 0,
  vertexMaxRadiusBaseRatio: 1.4,
  minGrabScreenRadius: 5,
  maxGrabRadiusImagePx: 6,
  selectedEdgeWidth: 3,
  vertexMinRadiusEdgeRatio: 1.67,
};

/**
 * The smallest on-screen radius a vertex handle may scale down to.
 *
 * Live again now that `vertexZoomScale` scales both ways: this is the bound
 * that keeps handles on a zoomed-OUT shape from scaling away to nothing.
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
 * Scale an on-screen radius WITH the view zoom, in both directions.
 *
 * With `vertexZoomScale` at its current 0 this is an identity (bounded by the
 * clamps): `zoom ** 0` is 1, so every handle is `baseRadius` on screen at every
 * zoom. That is the intended behaviour — see the note on `vertexZoomScale`
 * above for why zoom-dependent sizing was abandoned.
 *
 * The scaling machinery is kept rather than deleted so the behaviour stays one
 * number away, and bounded from the start if it returns. When `vertexZoomScale`
 * is non-zero: `zoom` is `view.viewZoom`, 1 at fit-to-window, higher zoomed in,
 * lower zoomed out. The radius is multiplied by `zoom ** vertexZoomScale`, so a
 * positive exponent pins the handle to the image (growing as the annotator
 * zooms in) and a negative one shrinks it as they zoom in.
 *
 * Both ends are clamped, and with a non-zero exponent neither clamp is
 * optional:
 *
 *  - FLOOR, `minVertexRadius()`. Zoomed far out a shape is small and its
 *    vertices are close together; without a floor they scale away to nothing
 *    and the shape cannot be grabbed at all.
 *  - CEILING, `vertexHandleRadius * vertexMaxRadiusBaseRatio`. Multiplying by
 *    zoom is unbounded — at 64x an 8px handle computes to 512px, a blob that
 *    would swallow the shape and everything near it. The ceiling is what makes
 *    image-pinned scaling safe at depth.
 *
 * A zoom that is zero, negative or non-finite returns the base radius: those
 * are not meaningful views, and scaling by them yields 0 or NaN.
 *
 * Kept here, next to the tunables it reads, so the drawn handle (draw.js) and
 * its click target (interactions.js) can never drift apart.
 */
export function zoomScaledRadius(baseRadius, zoom) {
  const floor = minVertexRadius();
  const ceiling = baseRadius * annotationSettings.vertexMaxRadiusBaseRatio;
  if (!Number.isFinite(zoom) || zoom <= 0) return baseRadius;
  const scaled = baseRadius * Math.pow(zoom, annotationSettings.vertexZoomScale);
  return Math.min(ceiling, Math.max(floor, scaled));
}

/**
 * The grab radius for a vertex hit test, in on-screen pixels.
 *
 * Follows the drawn handle through `zoomScaledRadius()`, so what the annotator
 * sees broadly matches what they can click, with two adjustments the drawn dot
 * does not need.
 *
 * `minGrabScreenRadius` is the important one. Handles SHRINK as the annotator
 * zooms in, deliberately, so they stop covering the pixels being annotated —
 * but a click target that shrank with them would make vertices progressively
 * harder to hit at exactly the zoom where the annotator is doing the most
 * precise work. Holding the grab area at a comfortable minimum decouples the
 * two: the dot gets out of the way visually while staying easy to grab. The
 * grab area being larger than the dot is intentional and is the long-standing
 * behaviour of `vertexGrabRadius`.
 *
 * `maxGrabRadiusImagePx` caps how far a grab may reach into the IMAGE. With a
 * constant screen radius this binds when zoomed OUT, where one screen pixel
 * spans many image pixels and a generous screen radius would swallow every
 * nearby vertex. Zoomed in a constant screen radius covers steadily less of
 * the image, so the cap is inert there.
 *
 * `imageScale` is `view.imageBox.scale` — screen pixels per image pixel. When
 * missing or degenerate the cap is skipped rather than guessed, since a wrong
 * scale would silently collapse the grab area.
 *
 * The cap may never pull the grab area below the drawn handle: a dot the
 * annotator can see but cannot click is the one outcome worse than either.
 */
export function vertexGrabScreenRadius(baseRadius, zoom, imageScale) {
  const tracked = zoomScaledRadius(baseRadius, zoom);
  const comfortable = Math.max(tracked, annotationSettings.minGrabScreenRadius);
  if (!Number.isFinite(imageScale) || imageScale <= 0) return comfortable;
  // Never claim more than a fixed patch of the IMAGE. Only binds when zoomed
  // OUT, where one screen pixel spans many image pixels; zoomed in the shrunk
  // radius is already far tighter than this.
  const imageCapInScreenPx = annotationSettings.maxGrabRadiusImagePx * imageScale;
  return Math.max(tracked, Math.min(comfortable, imageCapInScreenPx));
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

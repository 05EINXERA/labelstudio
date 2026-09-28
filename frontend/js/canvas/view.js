
/// Mutable canvas view-state, shared by the draw layer and (once extracted)
// the interaction handlers. Grouped into one object rather than loose `let`
// bindings because ES module imports are read-only: a module that exports
// these must own them as properties so importers can mutate without
// reassigning the binding itself. See .devnotes/refactor/REFACTOR_PLAN.md §2.4.
export const view = {
  imageElement: new Image(),
  imageLoaded: false,
  viewZoom: 1,
  // Contain-fit scale at viewZoom === 1: pixels-per-image-pixel when the whole
  // image fills the canvas. Written by computeImageBox on every draw, read by
  // the zoom display to convert viewZoom into a pixel-accurate percentage and
  // by setZoom so the two derivations can never drift apart.
  baseScale: 1,
  viewPan: { x: 0, y: 0 },
  // When true, computeImageBox clamps viewPan so the image stays centred while
  // it fits the canvas and cannot be panned past its own edges once zoomed in.
  // A per-browser preference toggled from the zoom control (zoom-control.js).
  lockImageToCanvas: true,
  isPanning: false,
  panStart: { x: 0, y: 0, panX: 0, panY: 0 },
  imageBox: { x: 0, y: 0, width: 0, height: 0, scale: 1 },
  drag: null,
  // The polygon finalized most recently, kept so Ctrl+Z can keep trimming its
  // last vertex instead of deleting the whole shape the moment it is closed
  // (undoLastFinalizedPoint in interactions.js). This is set for every
  // finalized polygon, sticky class or not, and is nulled by any
  // edit that isn't one of those trims (canvas pointerdown, delete, undo).
  lastFinalizedPolygonId: null,
  hoveredLineIndex: -1,
  selectedLineIndex: -1,
  hoveredPointIndex: -1,
  pendingCommentPoint: null,
  pendingCommentEditId: null
};

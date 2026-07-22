
/// Mutable canvas view-state, shared by the draw layer and (once extracted)
// the interaction handlers. Grouped into one object rather than loose `let`
// bindings because ES module imports are read-only: a module that exports
// these must own them as properties so importers can mutate without
// reassigning the binding itself. See .devnotes/refactor/REFACTOR_PLAN.md §2.4.
export const view = {
  imageElement: new Image(),
  imageLoaded: false,
  viewZoom: 1,
  viewPan: { x: 0, y: 0 },
  isPanning: false,
  panStart: { x: 0, y: 0, panX: 0, panY: 0 },
  imageBox: { x: 0, y: 0, width: 0, height: 0, scale: 1 },
  drag: null,
  hoveredLineIndex: -1,
  selectedLineIndex: -1,
  hoveredPointIndex: -1,
  pendingCommentPoint: null,
  pendingCommentEditId: null
};

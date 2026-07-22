import { normalizeClassName, formatClassName } from "./utils.js?v=1";
import { view } from "./canvas/view.js?v=1";

export const storageKey = "image-annotation-mvp-v1";
export const labelStudioStorageKey = "image-annotation-label-studio-settings";
export const handleSize = 9;
export const closeThreshold = 1;

export const labelPalette = [
  "#0f8b8d", "#e85d75", "#f4a261", "#2a9d8f", "#7b2cbf",
  "#3f88c5", "#d95d39", "#65727f", "#8d6e63", "#4dabf7",
  "#c84c4c", "#096769", "#b5179e", "#4895ef"
];

export const state = {
  labels: [],
  annotations: [],
  image: null,
  gallery: [],
  galleryIndex: -1,
  _selectedId: null,
  selectedIds: new Set(),
  // Visibility toggles from the sidebar's eye buttons. Session-only: never
  // written to localStorage and never sent to the backend, so the persisted
  // annotation shape is unaffected. Cleared naturally on reload.
  hiddenLabelIds: new Set(),
  hiddenAnnotationIds: new Set(),
  activeLabelId: null,
  mode: "draw",
  shape: "polygon",
  history: [],
  redoHistory: []
};

// Setting selectedId cascades to selectedIds: selecting a grouped annotation
// selects its whole group. Must stay attached to the same object literal above.
Object.defineProperty(state, "selectedId", {
  get() {
    return this._selectedId;
  },
  set(id) {
    this._selectedId = id;
    if (id === null) {
      this.selectedIds.clear();
    } else {
      if (!this.selectedIds.has(id)) {
        this.selectedIds.clear();
        const ann = this.annotations.find(a => a.id === id);
        if (ann && ann.groupId) {
          this.annotations.forEach(a => {
            if (a.groupId === ann.groupId) this.selectedIds.add(a.id);
          });
        } else {
          this.selectedIds.add(id);
        }
      }
    }
  }
});

let nextColorIndex = -1;

export function colorForName(name) {
  if (nextColorIndex === -1) {
    nextColorIndex = state.labels.length;
  }
  const color = labelPalette[nextColorIndex % labelPalette.length];
  nextColorIndex++;
  return color;
}

export function labelByName(name) {
  const normalized = normalizeClassName(name);
  return state.labels.find((label) => label.name === normalized) || null;
}

// Single source of truth for visibility, shared by the draw loops and the
// canvas hit-test so the two can never disagree about what is on screen.
// A hidden class wins over an annotation's own toggle; revealing the class
// returns each annotation to its individual state, which falls out of checking
// both sets rather than mutating one from the other.
export function isAnnotationHidden(annotation) {
  if (!annotation) return false;
  // The shape being drawn right now is always visible, even if its class is
  // hidden: the annotator needs to see the vertices they are placing. A polygon
  // is pushed into state.annotations on its first click, so without this it
  // would disappear mid-draw. It becomes subject to the class toggle as soon as
  // the shape is closed and view.drag is cleared.
  if (view.drag?.annotationId && view.drag.annotationId === annotation.id) return false;
  if (state.hiddenAnnotationIds.has(annotation.id)) return true;
  if (annotation.labelId && state.hiddenLabelIds.has(annotation.labelId)) return true;
  return false;
}

export function labelById(id) {
  const label = state.labels.find((item) => item.id === id);
  if (label) return label;
  return { id, name: "object", color: "#65727f" };
}

export function labelDisplayName(label) {
  return formatClassName(label?.name || "object");
}

export function snapshot() {
  state.redoHistory = [];
  state.history.push(JSON.stringify({
    labels: state.labels,
    annotations: state.annotations,
    selectedId: state.selectedId
  }));
  if (state.history.length > 50) {
    state.history.shift();
  }
}

export function resetWorkspaceForNewImage() {
  // state.labels is deliberately not cleared to persist classes across images
  state.annotations = [];
  state.selectedId = null;
}

export function selectedAnnotation() {
  return state.annotations.find((item) => item.id === state.selectedId) || null;
}

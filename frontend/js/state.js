import { normalizeClassName, formatClassName } from "./utils.js?v=2";
import { view } from "./canvas/view.js?v=1";

export const storageKey = "image-annotation-mvp-v1";

// Local draft of the annotations for one task, used to recover work that had
// not reached the server yet (a refresh mid-edit, a failed save, a conflict).
//
// Keyed per task. The legacy `storageKey` above is a single global slot shared
// by every task, which meant opening a second task — or a second tab firing a
// cross-tab `storage` event — overwrote the draft of the first with unrelated
// annotations. See .devnotes/deployment-hardening/04_ANNOTATION_SAVE_LOSS.md.
// Namespaced by origin: task ids come from a per-server database, so the same
// id means different work on a different host. Without the origin a draft
// written against one server would silently be restored over another's task —
// which is not hypothetical here, since the deployment's address has changed
// before (.devnotes/offline/01_OFFLINE_RESILIENCE_PLAN.md gap G3).
export function draftKey(taskId) {
  return `annotation-draft-v1:${window.location.origin}:${taskId}`;
}

/**
 * Whether a draft written under `draftProjectId` may be restored while the
 * canvas is open on `openProjectId`.
 *
 * Label ids are per project: project A and project AA each hold their own row
 * for "Rust Area", with different ids. A task can be moved between projects
 * (.devnotes/move-task-feature/), and the server remaps every stored
 * annotation's label_id as it goes — but a draft sitting in this browser still
 * carries the source project's ids. Restoring it discards the correctly
 * remapped set and the next autosave writes the stale ids back, where the
 * server can only orphan them.
 *
 * `undefined`/`null` on either side is NOT a mismatch. Drafts written before
 * this field existed carry no project, and treating "unknown" as "wrong" would
 * refuse to recover legitimate pending work on every task until each is saved
 * once — a real regression in the save-loss net for the sake of a rarer bug.
 *
 * Pure, and here rather than in workspace.js, so it is testable: workspace.js
 * imports dom.js, which needs a real canvas element at module load.
 */
export function draftMatchesProject(draftProjectId, openProjectId) {
  if (draftProjectId == null || openProjectId == null) return true;
  return String(draftProjectId) === String(openProjectId);
}

// Drafts written before the origin was part of the key. Read-only: used to
// migrate a pre-existing draft on first open so the change does not itself
// orphan work that was pending during an upgrade.
export function legacyDraftKey(taskId) {
  return `annotation-draft-v1:${taskId}`;
}
export const labelStudioStorageKey = "image-annotation-label-studio-settings";
// Vertex handle size moved to feature-flags.js (annotationSettings) so the
// drawn radius and the click-target radius are configured in one place.
export const closeThreshold = 1;

export const labelPalette = [
  "#0f8b8d", "#e85d75", "#f4a261", "#2a9d8f", "#7b2cbf",
  "#3f88c5", "#d95d39", "#65727f", "#8d6e63", "#4dabf7",
  "#c84c4c", "#096769", "#b5179e", "#4895ef"
];

export const state = {
  // The project the canvas was opened for, from `?projectId=` in the URL.
  // Set once at boot and never changed, because the canvas page is per-project.
  //
  // It exists for the draft: label ids are per project, so a draft written
  // under one project cannot be restored under another. A task can now move
  // between projects (.devnotes/move-task-feature/), and a draft carrying the
  // old project's label ids is what silently orphaned every shape on the moved
  // task — see 07_DRAFT_STALENESS.md.
  projectId: null,
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
  // Momentary "peek" hide: populated while the "H" key is physically held and
  // emptied on its keyup. Deliberately separate from hiddenAnnotationIds so a
  // release never has to reconstruct what the sticky state was before the hold
  // — it just drops this set, which is what makes a hold idempotent and safe to
  // interrupt. Nothing but the hold's own keyup touches it, so a click, a
  // right-click pan or a wheel zoom cannot break the hold.
  // Session-only view state, like the sets above: never persisted, never sent
  // to the backend, never written to a draft.
  peekHiddenIds: new Set(),
  // Objects panel: when true the panel lists only the hidden objects. Same
  // nature as the two sets above — session-only view state, never persisted,
  // never sent to the backend, and read only at render time.
  hiddenFilterActive: false,
  activeLabelId: null,
  mode: "select",
  shape: "polygon",
  // When false (the default), a whole annotation cannot be dragged — only its
  // vertices/edges move. This guards against accidentally shoving a finished
  // shape off its object. The Move Objects toolbar toggle flips it. Session-only:
  // never persisted, so every reload starts locked.
  moveObjectsUnlocked: false,
  history: [],
  redoHistory: [],
  needsLabelSelection: false,
  // True only between finalizing a shape and the next canvas click. It exists so
  // that first click can release the finished shape's selection (otherwise picking
  // a class for the *next* shape would re-label the finished one). It must be
  // one-shot: needsLabelSelection stays true until a class is picked, so reusing
  // that flag here would swallow every later click and break vertex editing.
  justFinalized: false
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
  // Both sides folded. `label.name` is the *display* name and keeps the casing
  // its author typed ("Rust Area"), so comparing it against a normalised input
  // would miss every class whose name is not already lowercase — and a miss
  // here means ensureLabel() tries to create a class that already exists.
  const normalized = normalizeClassName(name);
  return state.labels.find(
    (label) => normalizeClassName(label.name) === normalized
  ) || null;
}

/**
 * Repoint annotations at classes that exist in `labels`. **Never creates one.**
 *
 * Pure, and here rather than in workspace.js, so it is testable: workspace.js
 * imports dom.js, which needs a real canvas element at module load. Same
 * arrangement as `draftMatchesProject` above.
 *
 * Returns a new array; `labels` is never modified. That is the invariant worth
 * stating twice, because the caller this replaced did the opposite — it called
 * `ensureLabel()` for every unresolvable id, which POSTs to /api/labels and so
 * minted a real project-wide class named "object" on every task open. See
 * .devnotes/fix-class-creation/01_AUDIT.md § 2.
 *
 * Resolution order per annotation:
 *   1. `labelId` already names a known class — unchanged.
 *   2. `detectedClass` names a known class (normalised) — repointed to it.
 *      This is the legitimate recovery case: a detector wrote the name, the
 *      class exists, only the id went stale.
 *   3. Neither — left exactly as-is. An unresolvable id is stale data, not a
 *      new class; `labelById()` renders it as "object", which is the correct
 *      end state for a placeholder. The id is preserved rather than nulled
 *      because it may become resolvable again and because
 *      `extra._orphanedLabelId` recovery depends on the value surviving.
 */
export function resolveAnnotationLabels(annotations, labels) {
  const byId = new Set(labels.map((label) => label.id));
  // Keyed on the folded form: `label.name` carries the author's casing.
  const byName = new Map(
    labels.map((label) => [normalizeClassName(label.name), label])
  );

  return annotations.map((annotation) => {
    if (byId.has(annotation.labelId)) return annotation;
    if (!annotation.detectedClass) return annotation;

    const match = byName.get(normalizeClassName(annotation.detectedClass));
    return match ? { ...annotation, labelId: match.id } : annotation;
  });
}

// Single source of truth for visibility, shared by the draw loops and the
// canvas hit-test so the two can never disagree about what is on screen.
// A hidden class wins over an annotation's own toggle; revealing the class
// returns each annotation to its individual state, which falls out of checking
// both sets rather than mutating one from the other.
export function isAnnotationHidden(annotation) {
  if (!annotation) return false;

  // A momentary peek outranks everything, the mid-draw force-show below
  // included: the annotator is holding "H" to see what is *underneath*, and the
  // shape being drawn is the most likely thing in the way.
  if (state.peekHiddenIds.has(annotation.id)) return true;

  // An explicit per-annotation hide beats the mid-draw force-show too. It used
  // to lose to it, which is why pressing "H" mid-draw appeared to do nothing
  // until the polygon closed (.devnotes/new-hide-interactions/01_ANALYSIS.md
  // § 4.1): the id went into the set, the Objects row flipped, and the resolver
  // discarded it on every frame.
  if (state.hiddenAnnotationIds.has(annotation.id)) return true;

  // The shape being drawn right now survives a *class* hide: the annotator
  // needs to see the vertices they are placing, and a polygon inherits
  // state.activeLabelId, so if that class's eye is off it would vanish on its
  // first click with nothing on screen explaining why. It becomes subject to
  // the class toggle as soon as the shape is closed and view.drag is cleared.
  //
  // Scoped to draw-polygon: view.drag is also set for move and reshape drags,
  // which have no such problem, and matching those meant dragging a
  // class-hidden shape briefly revealed it.
  const isBeingDrawn = view.drag?.type === "draw-polygon"
    && view.drag.annotationId === annotation.id;
  if (isBeingDrawn) return false;

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

// Undo/redo is deliberately annotation-only. Classes are project-level state
// owned by the labels API, shared by every task and every annotator: rolling
// them back from one annotator's canvas history would blank the class panel
// (and orphan every annotation referencing a class this tab had "undone").
// Restoring only annotations keeps undo scoped to what the canvas actually
// edits. See clearHistory() for the session-scoping half of the same rule.
export function snapshot() {
  state.redoHistory = [];
  state.history.push(JSON.stringify({
    annotations: state.annotations,
    selectedId: state.selectedId
  }));
  if (state.history.length > 50) {
    state.history.shift();
  }
}

// Undo history is per-task and per-session. Opening a task must start with an
// empty stack: work saved by a previous session (or by another annotator) is
// not this session's to undo, and a stack carried across a task switch let the
// first Ctrl+Z restore the *outgoing* task's state over the incoming one —
// which, because a task is hydrated after the switch, meant undoing onto an
// empty annotation array and saving that wipe to the server.
export function clearHistory() {
  state.history = [];
  state.redoHistory = [];
}

export function resetWorkspaceForNewImage() {
  // state.labels is deliberately not cleared to persist classes across images
  state.annotations = [];
  state.selectedId = null;
  // View state from the sidebar's eye controls does not survive a task switch.
  // Annotation ids are per-task, so a carried-over hidden id is at best dead
  // weight; a carried-over *class* hide genuinely leaks (hide "car" on one task
  // and it stays hidden on the next, with nothing on screen explaining why);
  // and a carried-over filter would open the next task on an empty
  // "hidden only" list. See .devnotes/object-selection/01_DESIGN.md § 5.1.
  state.hiddenAnnotationIds.clear();
  state.peekHiddenIds.clear();
  state.hiddenLabelIds.clear();
  state.hiddenFilterActive = false;
  clearHistory();
  // Re-arm the label gate for each new task: the annotator must pick a class
  // before drawing, rather than inheriting the previous task's armed state.
  state.mode = "select";
  state.activeLabelId = null;
  state.needsLabelSelection = false;
  state.justFinalized = false;
}

export function selectedAnnotation() {
  return state.annotations.find((item) => item.id === state.selectedId) || null;
}

// --- Hydration gate -------------------------------------------------------
//
// A task's annotations are fetched on open (init.js switchImage), and every
// save is a wholesale replacement of the stored blob. So a save issued while
// `state.annotations` is the empty array that resetWorkspaceForNewImage() just
// installed — but before the server copy has landed — writes `[]` over real
// work. That is the annotation-wipe bug (.agents/annotation-wipe-fix/).
//
// Two rules make the gate safe, and both matter:
//
//   1. It is *positive*. Only `hydrationOk()` returning true permits a save.
//      An earlier version tested `task._hydrated === false`, which let the
//      `undefined` of a not-yet-touched task pass every guard — fail-open on a
//      destructive operation.
//
//   2. It is keyed on a *generation counter*, not a per-task boolean. Rapid
//      paging re-enters switchImage before the previous call's awaits have
//      resolved; a per-task flag set by the superseded call would mark the
//      new task hydrated while its canvas was still empty. Each switch takes a
//      new generation, and a hydration result is only honoured if its
//      generation is still current.
//
// The gate is module state rather than a field on the gallery item precisely
// so it cannot be stale-true: there is exactly one current generation, and
// anything that is not it is by definition not hydrated.

let hydrationGeneration = 0;
let hydratedGeneration = -1;
let hydrationFailedGeneration = -1;

// How many annotations the server actually sent for the open task.
//
// This is what separates "the user deleted everything" from "the canvas never
// got populated". Both look identical at save time — `state.annotations` is
// `[]` either way — but only the first is a legitimate delete-all. Without
// this, Ctrl+S on a blank canvas inferred `allow_clear` from the emptiness
// itself and instructed the server to bypass its clear-guard, which is the
// wipe: the one defence designed to catch an empty overwrite was switched off
// by the very condition it exists to detect.
let hydratedAnnotationCount = 0;

// Fingerprint of the annotation set as it arrived from the server, used to
// answer "has the user actually edited anything since this task opened?".
//
// The count alone cannot answer that: moving a box or relabelling it leaves the
// count identical. This exists because "saving a Completed task demotes it to
// In Progress" must fire only for a real content change. Time-only and
// incidental saves (the 30s drain, a gallery switch, an autosave triggered by a
// non-content action) otherwise silently un-completed a task the annotator had
// just finished — including on merely opening it.
let hydratedAnnotationFingerprint = null;

/**
 * Was the task that is currently open hydrated?
 *
 * Read *before* `beginHydration()` moves the generation, so a caller switching
 * away can ask "did the task I am leaving ever get its annotations from the
 * server?" — `hydrationOk()` cannot answer that, because by the time the
 * outgoing task is flushed the generation already belongs to the incoming one.
 *
 * This exists because the gallery-switch flush (init.js) sends the outgoing
 * task's annotation set deliberately outside the hydration gate, to rescue
 * genuine unsaved work. When the outgoing task never hydrated, the set it
 * copies off the canvas is `[]`, and that empty array is a valid annotation
 * payload: it reaches the server as "make this task empty" and is only stopped
 * by the clear-guard's 422. That is the source of the empty-payload wipe
 * attempts logged 67 times in eight days
 * (.devnotes/wipe-guard-bypass-fix/04_VERIFICATION.md §C).
 */
export function openTaskWasHydrated() {
  return hydratedGeneration === hydrationGeneration;
}

/** Open a new hydration attempt. Returns the generation token for it. */
export function beginHydration() {
  hydrationGeneration += 1;
  // Forget the previous task's count immediately. Carrying it across a switch
  // would let the new task inherit "it had work when it loaded" and so qualify
  // for a clear it never earned.
  hydratedAnnotationCount = 0;
  // Same reasoning for the fingerprint: an inherited one would make the new
  // task's untouched annotations look edited (or vice versa).
  hydratedAnnotationFingerprint = null;
  return hydrationGeneration;
}

/** Mark `generation` hydrated. Ignored if a newer switch has superseded it. */
export function completeHydration(generation) {
  if (generation !== hydrationGeneration) return false;
  hydratedGeneration = generation;
  return true;
}

/** Mark `generation` failed. Ignored if a newer switch has superseded it. */
export function failHydration(generation) {
  if (generation !== hydrationGeneration) return false;
  hydrationFailedGeneration = generation;
  return true;
}

/** True only when the open task's annotations came from the server. */
export function hydrationOk() {
  return hydratedGeneration === hydrationGeneration;
}

/** Record what the server sent, so a later delete-all can be proven genuine. */
export function noteHydratedAnnotationCount(count) {
  hydratedAnnotationCount = Number.isFinite(count) ? count : 0;
}

export function getHydratedAnnotationCount() {
  return hydratedAnnotationCount;
}

/** Record the server's annotation set verbatim, for change detection. */
export function noteHydratedAnnotations(annotations) {
  try {
    hydratedAnnotationFingerprint = JSON.stringify(annotations ?? []);
  } catch {
    // Unserialisable state should not break saving; fall back to "unknown",
    // which annotationsChangedSinceHydration() treats as "assume edited" —
    // the same behaviour as before this check existed.
    hydratedAnnotationFingerprint = null;
  }
}

/**
 * Have the open task's annotations changed since it hydrated?
 *
 * Fails safe: with no fingerprint (hydration failed, or an older bundle) this
 * reports `true`, so the caller behaves exactly as it did before — the status
 * demotion is applied. Only a positive, verified match suppresses it.
 */
export function annotationsChangedSinceHydration(annotations) {
  if (hydratedAnnotationFingerprint === null) return true;
  try {
    return JSON.stringify(annotations ?? []) !== hydratedAnnotationFingerprint;
  } catch {
    return true;
  }
}

// --- Deliberate deletions (the wipe guard) --------------------------------
//
// Every save names the shapes the user deliberately removed since the server
// last accepted a save of that task (`deleted_ids`), and the server refuses a
// large loss those ids do not explain — the shape of a canvas that lost its
// annotations on its own (task 660, 2026-09-24). This replaces the old
// `clearIsUserIntent()`, which *inferred* a delete-all from an empty canvas and
// so authorised exactly the empty state the guard exists to catch.
//
// Keyed by task id, not reset per switch: the gallery-switch flush saves the
// *outgoing* task after beginHydration() has already moved to the incoming
// one, and a bulk delete followed by a quick page-away must still carry its
// ids. See .devnotes/bulk-loss-guard/01_DESIGN.md.
const pendingDeletions = new Map();   // taskId -> Set of annotation ids
const serverIdsByTask = new Map();    // taskId -> Set of ids the server holds

function openTaskId() {
  const task = state.gallery?.[state.galleryIndex];
  return task ? task.id : null;
}

/**
 * Record the shapes a user action removed: every id in `before` that is not in
 * `after`. Called by each deliberate removal path (delete, clear all, merge,
 * undo/redo, AI replace, cancelling a half-drawn polygon) — never by hydration
 * or draft restore, which are not the user's doing.
 */
export function noteUserRemoved(before, after, taskId = openTaskId()) {
  if (taskId == null) return;
  const kept = new Set((after || []).map((a) => a && a.id).filter(Boolean).map(String));
  let set = pendingDeletions.get(taskId);
  for (const ann of before || []) {
    if (!ann || !ann.id) continue;
    const id = String(ann.id);
    if (kept.has(id)) continue;
    if (!set) {
      set = new Set();
      pendingDeletions.set(taskId, set);
    }
    set.add(id);
  }
}

/** Merge ids from a recovered draft back into the task's pending set. */
export function restorePendingDeletions(taskId, ids) {
  if (taskId == null || !Array.isArray(ids) || ids.length === 0) return;
  let set = pendingDeletions.get(taskId);
  if (!set) {
    set = new Set();
    pendingDeletions.set(taskId, set);
  }
  ids.forEach((id) => set.add(String(id)));
}

/** The ids to send with the next save of `taskId`, as an array. */
export function pendingDeletedIds(taskId) {
  const set = pendingDeletions.get(taskId);
  return set ? [...set] : [];
}

/**
 * The server accepted a save that carried `sentIds`: those are settled.
 *
 * Only the ids that save carried — a delete made while it was in flight rides
 * the next save, exactly as `sentAnnotations` does in workspace.js.
 */
export function acknowledgeDeletedIds(taskId, sentIds) {
  const set = pendingDeletions.get(taskId);
  if (!set) return;
  (sentIds || []).forEach((id) => set.delete(String(id)));
  if (set.size === 0) pendingDeletions.delete(taskId);
}

/** Record which annotation ids the server holds for `taskId`. */
export function noteServerAnnotationIds(taskId, annotations) {
  if (taskId == null) return;
  const ids = new Set();
  for (const ann of annotations || []) {
    if (ann && ann.id) ids.add(String(ann.id));
  }
  serverIdsByTask.set(taskId, ids);
}

/** The ids the server holds for `taskId` as far as this tab knows, or null. */
export function serverAnnotationIds(taskId) {
  return serverIdsByTask.get(taskId) || null;
}

/** True when the current task's hydration fetch was attempted and failed. */
export function hydrationFailed() {
  return hydrationFailedGeneration === hydrationGeneration;
}

/** Current generation token, for callers that need to re-check after an await. */
export function currentHydrationGeneration() {
  return hydrationGeneration;
}

/**
 * The reason a save must be refused right now, or null when saving is allowed.
 *
 * Single source of truth for all four save entry points (Ctrl+S, the debounced
 * autosave, the manual-save overlay, and syncToBackend itself) so they cannot
 * drift apart.
 */
export function hydrationSaveBlock() {
  if (hydrationOk()) return null;
  if (hydrationFailed()) return "Cannot save: task failed to load — reload the page";
  return "Cannot save: task is still loading…";
}

import { generateUUID, normalizeClassName } from "../utils.js?v=1";
import { apiFetch } from "../api.js?v=3";
import {
  state, storageKey, draftKey, legacyDraftKey, colorForName, labelByName, labelById,
  labelDisplayName, snapshot, selectedAnnotation, hydrationOk, hydrationSaveBlock,
  clearIsUserIntent, annotationsChangedSinceHydration, noteHydratedAnnotations,
  isAnnotationHidden
} from "../state.js?v=7";
import { visibleRows, hiddenRowCount } from "../objects-filter.js?v=1";
import { MAX_CLASS_SHORTCUTS } from "../shortcuts.js?v=1";
import { pendingCount, retryablePendingCount, isServerUnreachable, peekWrite } from "../offline-queue.js?v=4";
import { annotationPoints, updateAnnotationBounds } from "../canvas/geometry.js?v=1";
import { view } from "../canvas/view.js?v=1";
import { drainTaskTime, DRAIN_SKIPPED, refreshTimerDisplays } from "./timer.js?v=5";
import { timerState } from "../timer-state.js?v=3";
import { detectState } from "../ai/detect-state.js?v=3";
import { draw, drawAllLayers } from "../canvas/draw.js?v=6";
import {
  emptyState, classesList, annotationList, annotationCount, selectedInfo,
  hiddenFilterButton, hiddenCount,
  drawMode, selectMode, boxMode, polygonMode, commentMode, magicWandMode,
  autoDetectButton, aiSettingsMenuButton, autoTagButton, fftToolGroup,
  undoButton, redoButton, deleteButton, clearButton,
  shapeHint, saveStatus
} from "../dom.js?v=4";
import { commentOverlayRefs, openCommentEditor } from "../comment-overlay.js?v=2";
import { toolAvailability } from "../feature-flags.js?v=1";
// Per-task write gating. `isReadOnly()` is project-role only, so it is false
// for an annotator who simply is not assigned the open task — the sidepanel
// needs the per-task answer, which is what taskWriteBlock() gives.
// canvas-permissions.js does not import this module, so there is no cycle.
import { taskWriteBlock } from "../canvas-permissions.js?v=9";
import { isTerminal } from "../task-status.js?v=3";


/**
 * The message the indicator settles on when nothing transient is being shown.
 *
 * This used to be the literal string "Saved", unconditionally, 3 seconds after
 * any message. During the DHCP outage that meant annotators saw a brief error
 * flash and then a steady, confident "Saved" while every single write was
 * failing — worse than showing nothing at all
 * (.devnotes/offline/01_OFFLINE_RESILIENCE_PLAN.md gap G1). The resting state
 * must reflect whether work is actually on the server.
 */
function restingStatus() {
  const pending = pendingCount();
  if (pending === 0) return "Saved";
  // Only count entries that are actually being retried. Forbidden (permission-
  // denied) and conflicted (waiting on a human) entries will never go away on
  // their own, so showing a "retrying" spinner for them is misleading and
  // creates a permanent "unsaved changes" badge on tasks the user IS allowed to
  // annotate (E-27).
  const retryable = retryablePendingCount();
  if (retryable === 0) return "Saved";
  const plural = retryable === 1 ? "change" : "changes";
  return isServerUnreachable()
    ? `⚠ Offline — ${retryable} ${plural} held locally`
    : `${retryable} unsaved ${plural} — retrying`;
}

export function setStatus(text) {
  saveStatus.textContent = text;
  window.clearTimeout(setStatus.timer);
  setStatus.timer = window.setTimeout(() => {
    saveStatus.textContent = restingStatus();
  }, 3000);
}

/** Re-render the resting message immediately. Called when the queue changes so
 *  the count is not stale for up to 3s after a drain. */
export function refreshSaveStatus() {
  window.clearTimeout(setStatus.timer);
  saveStatus.textContent = restingStatus();
}

export function ensureLabel(className, customColor = null) {
  const name = normalizeClassName(className);
  const existing = labelByName(name);
  if (existing) return existing;

  const label = {
    id: generateUUID(),
    name,
    color: customColor || colorForName(name)
  };
  state.labels.push(label);

  const projectId = new URLSearchParams(window.location.search).get('projectId');
  if (projectId) {
    // Persist to backend asynchronously.
    //
    // The optimistic push above is rolled back when the server refuses the
    // class, and a refusal is NOT an exception: apiFetch resolves for a 403,
    // so the old `.catch()` never ran for the case that actually matters. A
    // class the server rejected therefore lived on in `state.labels` for the
    // rest of the session, looking to its author exactly like a real one.
    //
    // That is the class-panel half of the reported sidepanel bug: an annotator
    // who is not assigned the open task is refused by /api/labels (it requires
    // MANAGER), sees the class appear anyway, repoints an annotation at its
    // id, and every other user then renders that annotation as "Object" —
    // labelById() falls back to {name:"object"} for an id it does not know.
    //
    // Rolling back keeps one rule: `state.labels` contains only classes the
    // server has, or ones it has not answered on yet. It never keeps one that
    // was refused.
    apiFetch('/api/labels', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...label, projectId: Number(projectId) })
    }).then((res) => {
      if (res && res.ok) return;
      // A transport failure (res undefined) is left alone deliberately: the
      // class may well exist once the network recovers, and dropping it would
      // discard work during an outage. Only an explicit refusal is rolled back.
      if (!res) return;
      rollbackLabel(label, res.status);
    }).catch(err => console.error("Failed to save label to backend:", err));
  }

  return label;
}

/**
 * Undo an optimistic `ensureLabel` push the server refused.
 *
 * Any annotation that was repointed at this class in the meantime is returned
 * to whatever it referenced before, so the canvas never holds a labelId that
 * exists nowhere. Without that, the annotation renders as "Object" for its
 * author too, and — if it ever reaches the server by another path — for
 * everyone else permanently.
 */
function rollbackLabel(label, status) {
  const index = state.labels.findIndex((l) => l.id === label.id);
  if (index !== -1) state.labels.splice(index, 1);

  const orphaned = state.annotations.filter((a) => a.labelId === label.id);
  if (orphaned.length) {
    const fallback = state.labels[0] || null;
    orphaned.forEach((a) => { a.labelId = fallback ? fallback.id : null; });
  }
  if (state.activeLabelId === label.id) state.activeLabelId = null;

  console.warn(
    `Class "${label.name}" was refused by the server (${status}) and has been removed.`
  );
  setStatus(
    status === 403
      ? "Not allowed to create classes here"
      : `Class "${label.name}" was refused`
  );
  render();
}

export function repairLabelsFromAnnotations() {
  state.annotations = state.annotations.map((annotation) => {
    const existing = state.labels.find((label) => label.id === annotation.labelId);
    if (existing) return annotation;

    const label = ensureLabel(annotation.detectedClass || "object");
    return { ...annotation, labelId: label.id };
  });
}

/**
 * Why the open task may not be edited right now, or null when it may.
 *
 * The sidebar panels must consult this rather than `isReadOnly()`. The latter
 * asks only "can this role annotate the project at all", which is true for an
 * annotator who is not assigned the open task — so it left the Objects panel's
 * edit control fully live for exactly the user who must not use it. Every
 * mutating affordance outside the canvas is gated on this instead.
 *
 * Rendering only (rule 18b): the server refuses regardless, and these fixes
 * are about not showing a control that cannot work and not corrupting local
 * state when it is used.
 */
export function editBlockReason() {
  const task = currentTask();
  return taskWriteBlock(task);
}

/**
 * Push the open task's annotations to the server.
 *
 * `allowClear` marks a save the user explicitly meant to empty: this is the
 * only path that carries the real, user-authored annotation set, so it is the
 * only one entitled to confirm a delete-all past the server's clear-guard
 * (api/routers/tasks.py — the guard that exists because a half-hydrated client
 * once autosaved `[]` over 403 real polygons, INCIDENT_692). Time-only saves
 * elsewhere deliberately omit the annotation set entirely rather than sending
 * an empty one, so they can never reach that guard at all.
 */
export function syncToBackend({ useBeacon = false, keepStatus = false, allowClear = false, forceStatus = null, userInitiated = false } = {}) {
  if (typeof state === 'undefined' || state.galleryIndex < 0 || !state.gallery || !state.gallery[state.galleryIndex]) return;
  const currentTask = state.gallery[state.galleryIndex];
  if (!currentTask.id) return;

  // Refuse to save unless the open task's annotations came from the server.
  // Positive gate: anything other than a confirmed hydration blocks, so the
  // pre-hydration window (and a failed fetch) cannot ship `[]` over real work.
  if (!hydrationOk()) {
    console.warn("Save blocked:", hydrationSaveBlock());
    return Promise.resolve(false);
  }

  // Only touch status when this user can actually write the task. A read-only
  // viewer opening a New task must not silently flip it to In Progress — that
  // write would 403, the status change would still appear in the client, and
  // the next real annotator would see a confusing "In Progress" with no work.
  const canWrite = currentTask.can_write !== false;

  // An explicitly chosen status ("Save as Complete") is the user's instruction,
  // not a derived value, so it is read from the argument rather than from
  // `currentTask.status`. Threading it through as data fixes a save that
  // reported success while storing the wrong status: `saveAndComplete` mutated
  // `currentTask.status` and relied on that mutation surviving until the
  // payload was built, but the pill was already repainted locally, so a
  // payload that went out as "In Progress" still looked Completed on screen
  // until the next reload. The request now carries exactly what was asked for.
  let taskStatus = forceStatus || currentTask.status;

  // The status as stored *before* the derivations below. Compared against the
  // final value to tell "this save changes the status" from "this save merely
  // restates it" — the New→In Progress promotion and the terminal demotion are
  // both real changes and must not be suppressed.
  const statusBeforeDerivation = currentTask.status;

  if (canWrite && !keepStatus) {
    // Opening a New task and doing any work naturally starts it.
    if (taskStatus === 'New') taskStatus = 'In Progress';

    // Saving while the task claims to be finished — 'Completed', or any
    // approved-group status — means the annotator revised their work, so flip
    // back to In Progress and let a reviewer re-review it rather than silently
    // passing amended annotations under a sign-off granted to the *previous*
    // version. This matters more with batch statuses than it did with
    // 'Approved' alone: a task edited after being marked 'Verified' would
    // otherwise stay in the Verified batch and be exported as reviewed work
    // nobody has actually reviewed.
    //
    // Gated on the annotations having actually changed since the task
    // hydrated. Not every save is an edit: the 30s time drain, the gallery
    // switch flush and the visibilitychange beacon all run this path having
    // touched nothing, and demoting on those meant simply *opening* a
    // Completed task — or completing one and paging away — silently reverted
    // it to In Progress. `annotationsChangedSinceHydration` fails safe,
    // reporting "changed" whenever it cannot prove otherwise, so a genuine
    // revision is still demoted exactly as before.
    //
    // NOTE: keepStatus=true bypasses this so "Save as Complete" can lock the
    // status in place rather than having syncToBackend immediately revert it.
    if (isTerminal(taskStatus) &&
        annotationsChangedSinceHydration(state.annotations)) {
      taskStatus = 'In Progress';
    }
  }

  currentTask.status = taskStatus;
  currentTask.annotations = [...state.annotations];

  // A save that would change nothing is not made at all.
  //
  // This path always passes both `status` and `annotations`, so timer.js's own
  // gate never fires from here — the suppression has to be stated explicitly,
  // and this is the call site that matters: the 30s tick, the gallery switch
  // and the pagehide beacon all arrive through here having touched nothing.
  // Sending them grew `time_spent` and moved `updated_at` for a look-only
  // visit (.devnotes/unwanted-time-change/01_DIAGNOSIS.md).
  //
  // The conditions are all necessary:
  //   * `!forceStatus` — "Save as Complete" states an intent; never suppress it.
  //   * `taskStatus === currentTaskStatusBefore` — the derived New→In Progress
  //     promotion and the terminal→In Progress demotion above are real changes.
  //   * `!annotationsChangedSinceHydration(...)` — fails safe, reporting
  //     "changed" whenever it cannot prove otherwise (no fingerprint, an
  //     unserialisable state), so an unprovable case still saves.
  //   * `!allowClear` — a deliberate delete-all is an edit by definition, and
  //     must never be swallowed.
  //
  // Beacons matter most here: sendBeacon reports only that it queued, so a
  // pointless one can never be judged after the fact. Not sending it is the
  // only way to be sure it did nothing.
  //   * `!userInitiated` — the Save button is a stated intent. Suppressing it
  //     would report "Saved Successfully" for a request that was never sent,
  //     which is the same class of lie as the old unconditional "Saved" (see
  //     restingStatus() above). Automatic saves make no such promise, so only
  //     they are eligible for suppression.
  const nothingToSave = !userInitiated
    && !forceStatus
    && !allowClear
    && taskStatus === statusBeforeDerivation
    && !annotationsChangedSinceHydration(state.annotations);

  if (nothingToSave) {
    // Discard the seconds accrued while only looking, and leave the draft and
    // the offline queue alone: nothing was sent, so there is nothing to
    // confirm and nothing to retry.
    timerState.taskSessionSeconds = 0;
    refreshTimerDisplays();
    return Promise.resolve(DRAIN_SKIPPED);
  }

  // Time accounting (drain, retry-on-failure, task binding) lives in timer.js
  // so there is exactly one drain point for taskSessionSeconds. See
  // docs/TIMER_AUDIT.md F3/F4.
  return Promise.resolve(drainTaskTime(currentTask, {
    status: taskStatus,
    annotations: currentTask.annotations,
    useBeacon,
    allowClear
  })).then((ok) => {
    // The draft exists to cover work the server does not have. Once it has
    // taken the write, the draft is stale and must go, or the next load would
    // "recover" it over fresher server data.
    if (ok !== false && currentTask.id) {
      clearDraft(currentTask.id);
      // The server now holds exactly what was sent, so that becomes the new
      // baseline for "has this been edited?". Without this the fingerprint
      // stays pinned to the original hydration and every later save still
      // counts as an edit — which would demote a just-completed task on the
      // very next time drain.
      noteHydratedAnnotations(currentTask.annotations);
    }
    return ok;
  });
}

/** The task currently open, or null. */
function currentTask() {
  if (!state.gallery || state.galleryIndex < 0) return null;
  return state.gallery[state.galleryIndex] || null;
}

/**
 * Write a local draft for the open task.
 *
 * The draft is the safety net for everything the server has not acknowledged
 * yet. It is deliberately per-task (see state.draftKey) and is cleared only
 * once a save succeeds, so a refresh mid-edit — or after a failed save —
 * recovers the work instead of losing it.
 */
export function saveDraft({ task = null, annotations = null } = {}) {
  const target = task || currentTask();
  if (!target || !target.id) return;

  // Explicit arguments name the task and the set being drafted, and are
  // trusted as-is. The one caller that passes them is switchImage's
  // failed-outgoing-save path: there the gate is already shut for the
  // *incoming* task, but the work being drafted belongs to the *outgoing*
  // one and is real. Gating that on the incoming task's hydration would drop
  // precisely the work the draft exists to rescue.
  const set = annotations || state.annotations;

  // For the implicit case the draft describes the open task, so it is only
  // trustworthy once that task hydrated. Otherwise this persists the
  // pre-hydration empty set, which would outlive the session and be
  // "recovered" over the server copy on next open.
  if (!task && !hydrationOk()) return;

  try {
    localStorage.setItem(draftKey(target.id), JSON.stringify({
      annotations: set,
      labels: state.labels,
      savedAt: Date.now()
    }));
  } catch (e) {
    // Quota exceeded: the draft is best-effort, the server save is the real
    // path. Losing the net is worth knowing about but must not break editing.
    console.warn('Could not write local draft', e);
  }
}

export function clearDraft(taskId) {
  try {
    localStorage.removeItem(draftKey(taskId));
  } catch (e) {
    console.warn('Could not clear local draft', e);
  }
}

// Drafts older than this with no matching queued write are dropped. A draft's
// only job is covering work the server has not taken; once the outbox is empty
// for a task, an old draft is dead weight competing for the ~5 MB quota — and
// polygon-heavy drafts are not small. saveDraft() only warns on quota failure,
// so exhausting it silently disarms the safety net.
const DRAFT_TTL_MS = 7 * 24 * 60 * 60 * 1000;

/**
 * Drop stale drafts for this origin.
 *
 * Deliberately conservative: a draft is only removed when it is both past the
 * TTL and has no pending write in the outbox. Never touches drafts that
 * represent unsaved work, however old.
 */
export function pruneStaleDrafts() {
  const prefix = `annotation-draft-v1:${window.location.origin}:`;
  let removed = 0;
  try {
    // Collected before removing: mutating localStorage while indexing it by
    // position shifts the keys underneath the loop.
    const candidates = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && key.startsWith(prefix)) candidates.push(key);
    }
    for (const key of candidates) {
      const taskId = key.slice(prefix.length);
      if (peekWrite(taskId)) continue; // unsaved work — keep
      try {
        const draft = JSON.parse(localStorage.getItem(key));
        const savedAt = draft && draft.savedAt;
        if (typeof savedAt === 'number' && Date.now() - savedAt > DRAFT_TTL_MS) {
          localStorage.removeItem(key);
          removed++;
        }
      } catch {
        // Unparseable: it can never be restored, so it is pure waste.
        localStorage.removeItem(key);
        removed++;
      }
    }
  } catch (e) {
    console.warn('Could not prune drafts', e);
  }
  return removed;
}

/**
 * Restore a draft for `task` if it holds work the server does not have.
 *
 * Returns true if anything was restored. Called after the task's server-side
 * annotations are already in state, so the draft only wins when it actually
 * differs — otherwise every reload would report a phantom recovery.
 */
export function restoreDraft(task) {
  if (!task || !task.id) return false;
  let raw;
  try {
    raw = localStorage.getItem(draftKey(task.id));
    if (!raw) {
      // Fall back to the pre-origin key so a draft left pending across the
      // upgrade is still recovered, then migrate it forward.
      const legacy = localStorage.getItem(legacyDraftKey(task.id));
      if (legacy) {
        localStorage.setItem(draftKey(task.id), legacy);
        localStorage.removeItem(legacyDraftKey(task.id));
        raw = legacy;
      }
    }
  } catch {
    return false;
  }
  if (!raw) return false;

  try {
    const draft = JSON.parse(raw);
    if (!Array.isArray(draft.annotations)) {
      clearDraft(task.id);
      return false;
    }
    // Same content as the server's copy: nothing to recover.
    if (JSON.stringify(draft.annotations) === JSON.stringify(state.annotations)) {
      clearDraft(task.id);
      return false;
    }
    // An empty draft never wins over server work.
    //
    // A draft exists to carry work the server does not have, so "recovering"
    // emptiness onto a hydrated non-empty task is always a loss and never a
    // recovery. Bundles predating the hydration gate could write such a draft
    // (saveDraft ran during the pre-hydration window when state.annotations was
    // still `[]`), and module imports are version-pinned — those drafts sit in
    // localStorage until every annotator hard-reloads. Without this check the
    // stale draft is restored over the server copy and the next save persists
    // the wipe, reintroducing the bug the gate just closed.
    if (draft.annotations.length === 0 && state.annotations.length > 0) {
      clearDraft(task.id);
      return false;
    }
    state.annotations = draft.annotations;
    if (Array.isArray(draft.labels) && draft.labels.length) {
      state.labels = draft.labels;
    }
    repairLabelsFromAnnotations();
    return true;
  } catch {
    clearDraft(task.id);
    return false;
  }
}

/**
 * Debounced autosave.
 *
 * `allowClear` is threaded through for the one caller that legitimately empties
 * the annotation set (the Clear-all button). Without it that save is refused by
 * the server's clear-guard and surfaces to the annotator as a permission-style
 * warning about unsaved offline work — for an action they deliberately took.
 */
export function save({ allowClear = false } = {}) {
  const block = hydrationSaveBlock();
  if (block) {
    setStatus(block);
    return;
  }
  saveDraft();
  setStatus("Saving…");

  if (window.backendSyncTimeout) {
    clearTimeout(window.backendSyncTimeout);
  }
  window.backendSyncTimeout = setTimeout(() => {
    window.backendSyncTimeout = null;
    // "Saved" is only claimed once the server has actually taken the write.
    // Reporting it on the localStorage write alone told annotators their work
    // was safe while it existed nowhere but their own browser.
    // On failure the write is now in the outbox, so "retrying" is finally true
    // rather than aspirational — and refreshSaveStatus() keeps the pending count
    // on screen instead of reverting to "Saved" three seconds later.
    Promise.resolve(syncToBackend({ allowClear }))
      .then((ok) => (ok === false ? refreshSaveStatus() : setStatus("Saved")))
      .catch(() => refreshSaveStatus());
  }, 1000);
}

/**
 * Manual save with UI feedback.
 * 
 * This is called when the user clicks the Save button. It shows a spinner overlay
 * while saving, then displays "Saved Successfully" for 3 seconds.
 */
export async function manualSaveWithUI() {
  const block = hydrationSaveBlock();
  if (block) {
    setStatus(block);
    return;
  }
  const overlay = document.getElementById('saveOverlay');
  if (!overlay) return;

  // Show the overlay
  overlay.classList.add('is-active');

  // Belt-and-braces dismissal. apiFetch now bounds every request, so the await
  // below is guaranteed to settle — but this overlay covers the whole screen
  // and blocks the annotator completely, so it must not depend on any single
  // promise behaving. A stuck overlay is what made a save that never reached
  // the server look like annotations had been wiped: the canvas kept showing
  // local state, and the reload that followed revealed the last *completed*
  // save instead.
  //
  // Cleared in the finally block, so on the normal path this never fires.
  const failsafe = setTimeout(() => {
    overlay.classList.remove('is-active');
    setStatus("Save is taking longer than expected — your work is kept locally");
    refreshSaveStatus();
  }, 60_000);

  try {
    // Save the draft locally
    saveDraft();

    // An explicit Save of an empty canvas *may* be a deliberate delete-all —
    // but emptiness alone does not prove it. A canvas is equally empty when
    // hydration has not populated it yet, or when a reload race left it blank
    // while the gate happened to be open. Inferring allow_clear from the
    // emptiness itself therefore switched off the server's clear-guard in
    // exactly the situation the guard exists to catch, and a fast Ctrl+S on a
    // still-blank task wiped it.
    //
    // clearIsUserIntent() requires proof instead: the task hydrated, and it
    // hydrated with work that is now gone. That is only true when the user
    // actually removed annotations they could see. Otherwise the flag stays
    // off and the server refuses the empty write with a 422.
    const ok = await syncToBackend({
      allowClear: clearIsUserIntent(state.annotations.length),
      // The user pressed Save and is watching for an answer, so this one is
      // always sent even when nothing changed.
      userInitiated: true,
    });

    // A manual save is the one place the user is actively watching, so a
    // failure must be stated plainly rather than dressed up as success.
    if (ok === false) {
      refreshSaveStatus();
    } else {
      setStatus("Saved Successfully");
    }

    // Keep the overlay visible for a brief moment, then fade it out
    await new Promise(resolve => setTimeout(resolve, 800));

    overlay.classList.remove('is-active');

    // Then settle on whatever is actually true — "Saved" only if the outbox is
    // empty, otherwise the pending count.
    await new Promise(resolve => setTimeout(resolve, 3000));
    refreshSaveStatus();
  } catch (err) {
    // An aborted request (the 45s timeout in apiFetch) lands here, as does any
    // transport failure. The payload is already in the offline queue and the
    // draft is on disk, so nothing is lost — say so, rather than leaving the
    // annotator to infer it from a vanished overlay.
    const aborted = err && (err.name === 'AbortError' || err.name === 'TimeoutError');
    console.error('Manual save failed:', err);
    setStatus(
      aborted
        ? "Server did not respond — your work is kept locally and will retry"
        : "Save failed — your work is kept locally and will retry"
    );
    refreshSaveStatus();
  } finally {
    // The single guaranteed dismissal point. Previously the overlay was
    // removed on the success path and in the catch, which covered a *thrown*
    // failure but not a promise that never settled — and that is precisely the
    // case that occurred.
    clearTimeout(failsafe);
    overlay.classList.remove('is-active');
  }
}

export function loadSaved() {
  // Legacy global-slot draft. Kept only to migrate anything a previous version
  // left behind; drafts are per-task now (see restoreDraft).
  const saved = localStorage.getItem(storageKey);
  if (!saved) return;

  try {
    const payload = JSON.parse(saved);
    if (Array.isArray(payload.labels)) {
      state.labels = payload.labels;
    }
    repairLabelsFromAnnotations();
  } catch {
    // fall through to the removal below
  }
  // Drop the global slot either way: it is shared across tasks and across
  // tabs, so keeping it would let one task's annotations leak into another's.
  localStorage.removeItem(storageKey);
}

// Eye / eye-off pair, sized to match the existing 20x20 row action buttons.
const EYE_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>';
const EYE_OFF_SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"></path><line x1="1" y1="1" x2="23" y2="23"></line></svg>';

function visibilityButtonHTML(isHidden, title) {
  return `<span class="eye-btn${isHidden ? " is-hidden-state" : ""}" title="${title}">${isHidden ? EYE_OFF_SVG : EYE_SVG}</span>`;
}

/**
 * Make a class active, and relabel the current selection with it.
 *
 * Shared by the class row's click handler and the digit-key shortcut in
 * canvas/interactions.js. It lives outside renderClasses() so the two paths
 * cannot drift: everything past the first line is load-bearing, in particular
 * the editBlockReason() gate — a keyboard path that set `activeLabelId`
 * directly would relabel other people's work without the permission check.
 */
export function activateLabel(label) {
  if (!label) return;

  state.activeLabelId = label.id;
  state.needsLabelSelection = false;
  // The pending shape has been dealt with by this action, so the next canvas
  // click is an ordinary one — leaving this set would deselect spuriously.
  state.justFinalized = false;

  // Picking a class with nothing selected means "start the next annotation",
  // so drop back into draw mode instead of making the user press Draw. With a
  // selection the action means "relabel that", which must not change the mode.
  if (state.selectedIds.size === 0) {
    state.mode = "draw";
  }

  // Reassign class to selected annotations.
  //
  // Gated on the per-task check for the same reason as the Objects panel's
  // edit control: this mutates annotations and saves. Picking a class to
  // *draw* with is harmless and stays allowed — a read-only viewer can
  // still highlight a class to see which shapes belong to it — but
  // relabelling existing work is a write.
  const relabelBlocked = editBlockReason();
  if (state.selectedIds.size > 0 && relabelBlocked) {
    setStatus(relabelBlocked);
    render();
    return;
  }
  if (state.selectedIds.size > 0) {
    snapshot();
    let changed = false;
    state.annotations.forEach(a => {
      if (state.selectedIds.has(a.id) && a.type !== "comment" && a.labelId !== label.id) {
        a.labelId = label.id;
        changed = true;
      }
    });
    if (changed) {
      save();
    } else {
      state.history.pop();
    }
  }

  render();
}

/**
 * Hide or show a set of annotations together.
 *
 * Shared by the Objects row eye button and the "H" shortcut. Visibility is a
 * view concern: no snapshot() (not undoable) and no save() (nothing persisted
 * changed) — see GOTCHAS #18, filtering and hiding must never reach the saved
 * annotation set.
 */
export function toggleAnnotationsHidden(ids, hide) {
  (ids || []).forEach((id) => {
    if (hide) state.hiddenAnnotationIds.add(id);
    else state.hiddenAnnotationIds.delete(id);
  });
}

/**
 * Reveal every object hidden from the Objects panel, in one go.
 *
 * Shared by the Unhide button and the "U" shortcut. Returns how many rows were
 * revealed (0 if nothing was hidden) so the caller can report it — the shapes
 * coming back may sit off-screen at the current zoom, which makes the count the
 * only confirmation the user gets.
 *
 * Objects only: `state.hiddenLabelIds` is a separate axis owned by the Classes
 * panel's own eye, and an object hidden *by its class* stays hidden here —
 * correctly, since this never hid it. Same split the "H" shortcut observes.
 *
 * Also clears the hidden-only filter. Leaving it on would strand the user on an
 * empty "No hidden objects" list, because the filter's entire subject has just
 * ceased to exist.
 *
 * View-only, like every other visibility path: no snapshot() (not undoable) and
 * no save() (nothing persisted changed) — GOTCHAS #18.
 */
export function unhideAllObjects() {
  const revealed = state.hiddenAnnotationIds.size;
  if (!revealed) return 0;
  state.hiddenAnnotationIds.clear();
  state.hiddenFilterActive = false;
  return revealed;
}

export function renderClasses() {
  classesList.innerHTML = "";

  if (!state.labels.length) {
    const empty = document.createElement("p");
    empty.className = "chip-count";
    empty.textContent = "No classes defined";
    classesList.appendChild(empty);
  }

  // Note: we deliberately do NOT auto-activate the first class here.
  // The annotator must explicitly pick a class to start drawing, so that
  // the first canvas click is never silently attributed to an unintended class.

  state.labels.forEach((label, index) => {
    const item = document.createElement("button");
    item.type = "button";
    const labelHidden = state.hiddenLabelIds.has(label.id);
    item.className = `class-item${label.id === state.activeLabelId ? " is-active" : ""}${labelHidden ? " is-row-hidden" : ""}`;
    item.style.display = "flex";
    item.style.alignItems = "center";
    item.style.justifyContent = "space-between";
    const classAnns = state.annotations.filter(a => a.labelId === label.id && a.type !== "comment");
    const uniqueGroups = new Set();
    let count = 0;
    classAnns.forEach(a => {
      if (a.groupId) {
        if (!uniqueGroups.has(a.groupId)) {
          uniqueGroups.add(a.groupId);
          count++;
        }
      } else {
        count++;
      }
    });

    // Classes are fixed per project and managed from the dashboard, so the list
    // is read-only here: no edit or delete affordances.
    item.innerHTML = `
      <div style="display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0;">
        <span class="swatch" style="background:${label.color || '#65727f'}; flex-shrink: 0;"></span>
        <strong class="class-name" style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"></strong>
        <span class="class-count" style="font-size: 0.75rem; color: var(--muted); margin-left: 4px; flex-shrink: 0;">(${count})</span>
      </div>
      <div class="class-actions" style="display: flex; align-items: center; flex-shrink: 0;">
        ${visibilityButtonHTML(labelHidden, labelHidden ? "Show class annotations" : "Hide class annotations")}
      </div>
    `;
    item.querySelector(".class-name").textContent = `${index + 1}. ${labelDisplayName(label)}`;

    // Only the first ten rows have a single-key binding — 1-9 then 0 — so only
    // those advertise one. See shortcuts.js for why there is no eleventh key.
    if (index < MAX_CLASS_SHORTCUTS) {
      const digit = index === MAX_CLASS_SHORTCUTS - 1 ? 0 : index + 1;
      item.title = `${labelDisplayName(label)} — press ${digit}`;
    }

    item.querySelector(".eye-btn").addEventListener("click", (e) => {
      // Without this the row's own handler would also fire and make a class
      // active merely because its visibility was toggled.
      e.stopPropagation();
      if (labelHidden) state.hiddenLabelIds.delete(label.id);
      else state.hiddenLabelIds.add(label.id);
      // View-only: not undoable, nothing persisted changed.
      render();
    });

    // Click on the item itself sets it as active
    item.addEventListener("click", () => activateLabel(label));

    classesList.appendChild(item);
  });
}

/**
 * Collapse `state.annotations` into the panel's row list.
 *
 * One row per annotation, except that a group contributes a single row (its
 * first member) standing for every member — the same rule the panel has always
 * used, now computed up front instead of inline while appending.
 *
 * `index` is the row's permanent 1-based position in the *unfiltered* sequence.
 * It is assigned here, before any filtering, so a filtered list still numbers
 * its rows by where they really sit: selecting the 7th object shows "7.", not
 * "1.". See .devnotes/object-selection/01_DESIGN.md § 3.
 *
 * Read-only — it never mutates `state.annotations` or the annotations in it.
 */
function buildRows() {
  const processedGroups = new Set();
  const rows = [];

  state.annotations.forEach((annotation) => {
    if (annotation.groupId) {
      if (processedGroups.has(annotation.groupId)) return;
      processedGroups.add(annotation.groupId);
    }
    const isGroup = !!annotation.groupId;
    rows.push({
      annotation,
      isGroup,
      groupAnns: isGroup
        ? state.annotations.filter(a => a.groupId === annotation.groupId)
        : [annotation],
      index: rows.length + 1
    });
  });

  return rows;
}

/**
 * Repaint the Objects header's hidden-filter toggle and its count.
 *
 * Takes the row list the caller already built rather than recomputing it, and
 * derives the count fresh on every render instead of maintaining a tally — a
 * stored number would have to be adjusted by every path that hides or reveals
 * something (the two eye buttons, a class hide, a delete, a task switch), and
 * the first one missed would leave a permanently wrong badge.
 */
export function renderHiddenFilter(rows) {
  if (!hiddenFilterButton || !hiddenCount) return;
  hiddenCount.textContent = String(hiddenRowCount(rows, isAnnotationHidden));
  hiddenFilterButton.setAttribute("aria-pressed", String(!!state.hiddenFilterActive));
  hiddenFilterButton.title = state.hiddenFilterActive
    ? "Show all objects"
    : "Show only hidden objects";
}

// Wired once at module load, not inside renderHiddenFilter(): the panel
// re-renders on nearly every interaction, so attaching there would stack a new
// listener each time and a single click would end up toggling the filter dozens
// of times (i.e. randomly, depending on parity).
//
// A view concern only — no snapshot() (not undoable) and no save()/saveDraft()
// (nothing persisted changed), exactly like the per-row eye buttons.
hiddenFilterButton?.addEventListener("click", () => {
  state.hiddenFilterActive = !state.hiddenFilterActive;
  render();
});

export function renderAnnotations() {
  annotationList.innerHTML = "";

  // Built before anything is appended: the filters below decide which rows are
  // rendered, but never what a row *is* or what number it carries.
  const rows = buildRows();

  // Resolved once per render, not per row: it is the same answer for every
  // annotation in the panel and taskWriteBlock() walks the role/assignment
  // fields each call.
  //
  // When set, the per-row "Edit object class" control is not rendered at all.
  // Hiding rather than disabling is deliberate — the edit opens an inline form
  // whose Save mints a project-wide class via ensureLabel(), which is a
  // different permission (MANAGER on /api/labels) from the one that governs
  // this panel. A user who cannot write the task must not be offered it.
  const editBlocked = !!editBlockReason();

  // Filters are render-time only. `shown` is a new array of the same row
  // descriptors; nothing here touches `state.annotations`, which is what
  // syncToBackend()/saveDraft() serialise and what the hydration fingerprint is
  // taken over. A filtered panel therefore cannot truncate a save, a draft, or
  // make an untouched task look edited. See 01_DESIGN.md § 5.
  const shown = visibleRows(rows, {
    selectedIds: state.selectedIds,
    hiddenFilterActive: state.hiddenFilterActive,
    isHidden: isAnnotationHidden
  });

  if (!shown.length) {
    const empty = document.createElement("p");
    empty.className = "chip-count";
    // An empty *result* is not the same as an empty task: un-hiding the last
    // object while the hidden filter is on leaves a list with nothing in it and
    // annotations still on the canvas. Saying "No annotations yet" there reads
    // as data loss.
    empty.textContent = rows.length && state.hiddenFilterActive
      ? "No hidden objects"
      : "No annotations yet";
    annotationList.appendChild(empty);
  }

  shown.forEach((row) => {
    const { annotation, isGroup, groupAnns, index: displayCount } = row;

    const label = annotation.type === "comment" ? { name: "Comment", color: "#e85d75" } : labelById(annotation.labelId);
    const totalPoints = groupAnns.reduce((sum, a) => sum + annotationPoints(a).length, 0);

    const item = document.createElement("button");
    item.type = "button";
    const isActive = state.selectedIds.has(annotation.id);
    // Own toggle only — a class-level hide is reflected separately so the row
    // still shows this annotation's individual state underneath it.
    const annHidden = state.hiddenAnnotationIds.has(annotation.id);
    const classHidden = !!annotation.labelId && state.hiddenLabelIds.has(annotation.labelId);
    item.className = `annotation-item${isActive ? " is-active" : ""}${annHidden || classHidden ? " is-row-hidden" : ""}`;
    item.style.display = "flex";
    item.style.alignItems = "center";
    item.style.justifyContent = "space-between";
    item.innerHTML = `
      <div style="display: flex; align-items: center; gap: 8px; flex: 1; min-width: 0;">
        <span class="swatch" style="background:${label.color || '#65727f'}; flex-shrink: 0;"></span>
        <strong class="ann-name" style="white-space: nowrap; overflow: hidden; text-overflow: ellipsis;"></strong>
        <span class="ann-pts"></span>
      </div>
      <div class="annotation-actions" style="display: flex; align-items: center; gap: 4px; flex-shrink: 0;">
        ${editBlocked ? "" : `
        <span class="edit-ann-btn" title="Edit object class" style="cursor: pointer; color: var(--muted); display: grid; place-items: center; width: 20px; height: 20px;">
          <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
        </span>`}
        ${visibilityButtonHTML(annHidden, annHidden ? "Show object (H)" : "Hide object (H)")}
      </div>
    `;

    let text = annotation.type === "comment" ? `💬 ${annotation.text || "Comment"}` : `${displayCount}. ${labelDisplayName(label)}`;
    if (isGroup) {
      text = `${displayCount}. ${labelDisplayName(label)} (Group of ${groupAnns.length})`;
    }
    item.querySelector(".ann-name").textContent = text;
    // Bare count, no " pts" suffix — the unit costs sidebar width and the
    // number alone is what matters (e.g. spotting a malformed polygon).
    const ptsEl = item.querySelector(".ann-pts");
    ptsEl.textContent = annotation.type === "comment" ? "" : String(totalPoints);
    if (annotation.type !== "comment") ptsEl.title = `${totalPoints} points`;

    const escapeHTML = (str) => String(str).replace(/[&<>'"]/g, match => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[match]));

    // Absent when editBlocked — the control is not rendered at all above.
    const editBtn = item.querySelector(".edit-ann-btn");
    if (editBtn) editBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      // Re-checked at click time, not only at render time. The panel is not
      // re-rendered on every permission change (a task can be reassigned from
      // another tab mid-session), so a button drawn while the task was
      // writable must still refuse once it is not.
      const blocked = editBlockReason();
      if (blocked) {
        setStatus(blocked);
        return;
      }
      const currentName = label.name;
      const options = state.labels.map(l => `<option value="${escapeHTML(l.name)}"></option>`).join("");
      item.innerHTML = `
        <form class="edit-ann-form" style="display: flex; gap: 4px; width: 100%; align-items: center;" onsubmit="event.preventDefault();">
          <input type="text" list="classNamesDatalist_${annotation.id}" class="edit-ann-input" value="${escapeHTML(currentName)}" style="flex: 1; min-width: 0; padding: 2px 4px; font-size: 0.85rem;" onclick="event.stopPropagation()">
          <datalist id="classNamesDatalist_${annotation.id}">
            ${options}
          </datalist>
          <input type="color" class="edit-ann-color" value="${label.color}" style="width: 24px; height: 24px; padding: 0; border: none; flex-shrink: 0;" onclick="event.stopPropagation()">
          <button type="submit" class="primary save-edit-btn" style="padding: 2px 6px; font-size: 0.75rem; border: none; border-radius: 4px; flex-shrink: 0;" onclick="event.stopPropagation()">Save</button>
          <button type="button" class="cancel-edit-btn" style="padding: 2px 6px; font-size: 0.75rem; background: var(--panel-2); border: 1px solid var(--line); border-radius: 4px; flex-shrink: 0;" onclick="event.stopPropagation()">Cancel</button>
        </form>
      `;
      const form = item.querySelector(".edit-ann-form");
      const input = item.querySelector(".edit-ann-input");
      const colorInput = item.querySelector(".edit-ann-color");

      const finishEdit = (saveChanges) => {
        // The last gate before any mutation. ensureLabel() below creates a
        // project-wide class and the annotation is repointed at it, so a
        // permission change between opening this form and submitting it must
        // still stop both. Cancel is always allowed — it changes nothing.
        const blockedNow = saveChanges ? editBlockReason() : null;
        if (blockedNow) {
          setStatus(blockedNow);
          render();
          return;
        }
        if (saveChanges) {
          const newName = input.value.trim();
          const newColor = colorInput.value;
          if (newName) {
            const newLabel = ensureLabel(newName, newColor);
            if (newLabel.id !== annotation.labelId || newLabel.color !== newColor) {
              snapshot();
              if (newLabel.color !== newColor) {
                newLabel.color = newColor;
              }
              if (newLabel.id !== annotation.labelId) {
                if (isGroup) {
                  groupAnns.forEach(a => a.labelId = newLabel.id);
                } else {
                  annotation.labelId = newLabel.id;
                }
              }
              save();
            }
          }
        }
        render(); // re-render
      };

      form.addEventListener("submit", (ev) => {
        ev.preventDefault();
        finishEdit(true);
      });
      item.querySelector(".cancel-edit-btn").addEventListener("click", (ev) => {
        ev.stopPropagation();
        finishEdit(false);
      });
      form.addEventListener("click", (ev) => ev.stopPropagation());
    });

    item.querySelector(".eye-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      // A grouped row stands for every member, so it toggles them together —
      // matching how the row is drawn and selected as a unit.
      const ids = isGroup ? groupAnns.map(a => a.id) : [annotation.id];
      toggleAnnotationsHidden(ids, !state.hiddenAnnotationIds.has(annotation.id));
      render();
    });

    item.addEventListener("click", (event) => {
      state.mode = "select";
      if (event.shiftKey) {
        const toSelect = isGroup ? groupAnns.map(a => a.id) : [annotation.id];

        if (state.selectedIds.has(annotation.id)) {
          toSelect.forEach(id => state.selectedIds.delete(id));
        } else {
          toSelect.forEach(id => state.selectedIds.add(id));
        }
        state._selectedId = state.selectedIds.size > 0 ? Array.from(state.selectedIds)[0] : null;
      } else {
        state.selectedIds.clear();
        if (isGroup) {
          groupAnns.forEach(a => state.selectedIds.add(a.id));
        } else {
          state.selectedIds.add(annotation.id);
        }
        state.selectedId = annotation.id;
      }
      render();
      draw();
    });
    annotationList.appendChild(item);
  });

  // Deliberately the *unfiltered* total: it answers "how many objects are in
  // this image", which does not change because the user clicked one of them.
  // The hidden badge beside it carries the hidden number. (01_DESIGN.md § 4.)
  annotationCount.textContent = String(rows.length);

  renderHiddenFilter(rows);

  const selected = state.annotations.find((item) => item.id === state.selectedId);
  if (selected) {
    if (selected.type === "comment") {
      // The Edit control is hidden when the task may not be written, matching
      // the per-row class-edit control above. Rewriting a comment is an edit
      // like any other; offering it to a reader whose save the server will
      // refuse only invites lost work.
      const commentEditBlocked = !!editBlockReason();
      selectedInfo.innerHTML = `Comment by ${selected.author || "User"}${commentEditBlocked ? "" : ` <button id="editCommentBtn" class="icon-button" style="font-size: 0.8rem; margin-left: 8px;">✏️ Edit</button>`}`;
      if (!commentEditBlocked) {
        document.getElementById('editCommentBtn').addEventListener('click', () => {
          openCommentEditor(selected, view.imageBox, (id) => { view.pendingCommentEditId = id; });
        });
      }
    } else {
      selectedInfo.textContent = labelDisplayName(labelById(selected.labelId));
    }
  } else {
    selectedInfo.textContent = "None";
  }
}

export function renderControls() {
  drawMode.classList.toggle("is-active", state.mode === "draw");
  selectMode.classList.toggle("is-active", state.mode === "select");
  boxMode.classList.toggle("is-active", state.shape === "box");
  polygonMode.classList.toggle("is-active", state.shape === "polygon");
  commentMode.classList.toggle("is-active", state.shape === "comment");
  magicWandMode.classList.toggle("is-active", state.shape === "magicWand");
  if (state.shape === "polygon") {
    if (state.mode === "select") {
      shapeHint.textContent = state.activeLabelId
        ? "Class selected — press Draw or click a class to start drawing."
        : "Pick a class from the list to start drawing a polygon.";
    } else if (state.needsLabelSelection) {
      shapeHint.textContent = "Polygon drawn — pick a class to assign it.";
    } else if (!state.activeLabelId) {
      shapeHint.textContent = "Pick a class from the list to start drawing a polygon.";
    } else {
      shapeHint.textContent = "Click to add points. Click the first point to close the polygon.";
    }
  } else if (state.shape === "comment") {
    shapeHint.textContent = "Click anywhere on the image to leave a comment.";
  } else {
    // box / magicWand
    if (state.mode === "select") {
      shapeHint.textContent = state.activeLabelId
        ? "Class selected — press Draw or click a class to start drawing."
        : "Pick a class from the list to start drawing a bounding box.";
    } else if (!state.activeLabelId) {
      shapeHint.textContent = "Pick a class from the list to start drawing a bounding box.";
    } else {
      shapeHint.textContent = "Click and drag to draw a bounding box.";
    }
  }
  // ── AI section ────────────────────────────────────────────────────────────
  // When toolAvailability.ai is false every AI control is permanently disabled
  // regardless of any other runtime state.
  const aiBlocked = !toolAvailability.ai;
  autoDetectButton.disabled = aiBlocked || detectState.detectionBusy || !view.imageLoaded;
  const labelSpan = autoDetectButton.querySelector(".btn-label");
  if (labelSpan) {
    labelSpan.textContent = detectState.detectionBusy ? "Detecting..." : "Detect";
  }
  autoDetectButton.title = selectedAnnotation() ? "Detect objects inside the selected area" : "Detect objects in the whole image";
  if (aiSettingsMenuButton) aiSettingsMenuButton.disabled = aiBlocked;
  if (autoTagButton)        autoTagButton.disabled        = aiBlocked;
  // Magic Wand is in the Tools group but is AI-dependent — block it too.
  magicWandMode.disabled = aiBlocked;

  // ── Smooth section ────────────────────────────────────────────────────────
  // Disable every interactive control inside the FFT tool-group when
  // toolAvailability.smooth is false.  The container itself is not hidden so
  // the toolbar layout stays stable; the controls are just non-interactive.
  if (fftToolGroup) {
    fftToolGroup.querySelectorAll("button, input").forEach(el => {
      el.disabled = !toolAvailability.smooth;
    });
  }

  // ── Edit section ──────────────────────────────────────────────────────────
  undoButton.disabled = state.history.length === 0;
  redoButton.disabled = state.redoHistory.length === 0;
  deleteButton.disabled = state.selectedIds.size === 0;
  const groupButton = document.querySelector("#groupButton");
  if (groupButton) {
    const selectedList = state.annotations.filter(a => state.selectedIds.has(a.id));
    const allSameGroup = selectedList.length > 1 && selectedList.every(a => a.groupId && a.groupId === selectedList[0].groupId);
    groupButton.disabled = state.selectedIds.size <= 1 || allSameGroup;
  }
  const ungroupButton = document.querySelector("#ungroupButton");
  if (ungroupButton) {
    ungroupButton.disabled = !state.annotations.some(a => state.selectedIds.has(a.id) && a.groupId);
  }
  const mergeButton = document.querySelector("#mergeButton");
  if (mergeButton) {
    // Deliberately no overlap test here: it is O(n*m) per render pass, and the
    // command reports non-overlap itself when it runs. This only gates on what
    // is cheap to know — enough shapes, and permission to write.
    const mergeable = state.annotations.filter(
      a => state.selectedIds.has(a.id) && a.type !== "comment"
    );
    mergeButton.disabled = mergeable.length <= 1 || Boolean(editBlockReason());
  }
  clearButton.disabled = state.annotations.length === 0;
  // The old Export link was dimmed here when there was nothing to export. Its
  // replacement (Assign) is gated by *role*, not by canvas contents — a task
  // with no annotations yet is exactly one a manager may want to hand out — so
  // its visibility belongs to canvas-assign.js alone and is not touched here.
  emptyState.classList.toggle("is-hidden", view.imageLoaded);
}

export function render() {
  renderClasses();
  renderImageClasses();
  renderAnnotations();
  renderControls();
  drawAllLayers();
}

export function renderImageClasses() {
  const imageClassesList = document.getElementById("imageClassesList");
  if (!imageClassesList) return;

  const presentLabels = new Set();
  (state.annotations || []).forEach(ann => {
    if (ann.type !== "comment" && ann.labelId) {
      presentLabels.add(ann.labelId);
    }
  });

  imageClassesList.innerHTML = '';

  if (presentLabels.size === 0) {
    imageClassesList.innerHTML = '<p class="hint">No classes in current image.</p>';
    return;
  }

  Array.from(presentLabels).forEach(labelId => {
    const classDef = labelById(labelId);
    if (!classDef) return;

    const div = document.createElement("div");
    div.className = "class-item";
    div.style.gridTemplateColumns = "auto 1fr auto";

    const colorIndicator = document.createElement("div");
    colorIndicator.style.width = "12px";
    colorIndicator.style.height = "12px";
    colorIndicator.style.borderRadius = "50%";
    colorIndicator.style.background = classDef.color;

    const nameSpan = document.createElement("div");
    nameSpan.className = "chip-name";
    nameSpan.textContent = classDef.name;

    const countSpan = document.createElement("span");
    countSpan.style.fontSize = "0.75rem";
    countSpan.style.color = "var(--muted)";

    const classAnns = (state.annotations || []).filter(a => a.labelId === labelId && a.type !== "comment");
    const uniqueGroups = new Set();
    let count = 0;
    classAnns.forEach(a => {
      if (a.groupId) {
        if (!uniqueGroups.has(a.groupId)) {
          uniqueGroups.add(a.groupId);
          count++;
        }
      } else {
        count++;
      }
    });

    countSpan.textContent = `(${count})`;

    div.appendChild(colorIndicator);
    div.appendChild(nameSpan);
    div.appendChild(countSpan);
    imageClassesList.appendChild(div);
  });
}

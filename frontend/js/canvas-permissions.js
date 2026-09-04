/**
 * The canvas's permission surface: read-only mode, the assignment banner and
 * the reviewer's Approve / Reject controls (04_UI_UX.md § 7).
 *
 * A separate module rather than more code in `init.js` (already 1,000+ lines,
 * and rule 13 says stop growing it). Everything permission-shaped on the canvas
 * lives here, so "why can't this user draw?" is one file to read.
 *
 * ⚠️ This module must not touch the autosave, draft or conflict machinery
 * (rules 11 and 18, .devnotes/deployment-hardening/04_ANNOTATION_SAVE_LOSS.md).
 * It reports and it disables inputs; it never clears a draft, never cancels a
 * queued write, and never changes what a save sends. A user who lacks
 * permission still has unsaved work, and that work must survive.
 */
import { apiFetch } from "./api.js?v=5";
import { escapeHTML } from "./utils.js?v=1";
import { canAnnotate, canReview } from "./permissions.js?v=1";
import {
  APPROVED_STATUSES,
  isApproved,
  isFrozenForRole,
  isReviewStatus,
  statusClass,
  statusForReviewAction,
} from "./task-status.js?v=3";

const state = {
  project: null,
  role: null,
  task: null,
  banner: null,
  myUserId: null,
};

/** True when the caller may not write to this project at all. */
export function isReadOnly() {
  return !canAnnotate(state.role);
}

/**
 * Why this caller cannot write this task, or null if they can.
 *
 * A deliberate mirror of `can_write_task` in api/permissions.py (rule 18b:
 * rendering only — the server refuses regardless). Kept in the same order as
 * the server's branches so the two can be diffed by eye.
 *
 * Returns a reason string so the caller can show it; null means writable.
 */
export function taskWriteBlock(task) {
  if (!canAnnotate(state.role)) return "You have view-only access to this project.";

  // An approved task is frozen for everyone below reviewer. Checked before the
  // manager short-circuit and before the `can_write === true` short-circuit
  // below, mirroring the server's ordering in can_write_task: sign-off is the
  // one block that outranks assignment, so a task assigned to *you* is still
  // read-only once it has been signed off.
  if (task && isFrozenForRole(task.status, state.role)) {
    return `This task was marked ${task.status} and is locked. `
      + "Ask a reviewer to reject or re-open it if it needs changes.";
  }

  // Reviewers, like managers and owners, are never partitioned by assignment:
  // a reviewer may be asked to review any task in the project, and cannot give
  // meaningful feedback on work they may not interact with. This mirrors the
  // reviewer branch of can_write_task, which the mirror previously omitted —
  // so a reviewer looking at a task assigned to someone else was shown a
  // read-only canvas for a write the server would have accepted.
  if (canReview(state.role)) return null;
  if (!task) return null;

  // `can_write` comes from the server's own resolver (GET /api/tasks/{id}), so
  // when it is present it is the truth and the mirror below is only there to
  // explain *why*. An explicit `true` short-circuits: it means the server has
  // already said yes, whatever a stale assignment field on this row implies.
  if (task.can_write === true) return null;

  // `can_write === false` means the server already said no. Use it directly
  // rather than relying on the assignment fields, which may be stale (e.g. a
  // member was removed while this tab was open and the gallery entry has not
  // been refreshed yet). We still try to give a specific reason below, but
  // fall back to a generic "not permitted" if the fields have cleared.
  const serverSaidNo = task.can_write === false;

  if (task.assignee_user_id != null) {
    // Assigned to me — but an explicit server "no" still wins. The resolver
    // may be refusing for a reason this mirror cannot see (a revoked grant, a
    // team membership dropped mid-session), and drawing an editable control
    // against a server that will 403 is the failure this whole check exists to
    // avoid.
    if (task.assignee_user_id === state.myUserId && !serverSaidNo) return null;
    if (serverSaidNo && task.assignee_user_id === state.myUserId) {
      return "You can no longer save changes to this task.";
    }
    return `This task is assigned to ${task.assignee_name || "someone else"}. `
      + "You can view it, but not save changes.";
  }

  // assignee_user_id is null — either it was never set, or it was just cleared
  // (e.g. after a member removal). In both cases the name fields are gone too,
  // so we never say "assigned to Jayash" here.
  if (task.assigned_team_id == null) {
    // Fully unassigned. `can_write_task` in api/permissions.py returns False
    // here for anyone below manager — unassigned is deliberately NOT a shared
    // pool (PLAN.md § 8): work nobody has been handed is work nobody below
    // manager may start.
    //
    // This used to return "writable" whenever `can_write` was absent, which is
    // precisely the case for a gallery row (the list endpoint does not carry
    // the flag; only GET /api/tasks/{id} does). So every unassigned task
    // looked editable until the detail fetch landed — and the sidebar renders
    // from the gallery row. Blocking by default matches the server and fails
    // safe; an explicit `can_write: true` above already unblocks it.
    return "This task has not been assigned to anyone yet. "
      + "Ask a project manager to assign it to you or your team.";
  }

  const myTeamIds = new Set(state.myTeamIds || []);
  if (!myTeamIds.has(task.assigned_team_id)) {
    return `This task is assigned to ${task.assigned_team_name || "another team"}, `
      + "which you are not a member of. You can view it, but not save changes.";
  }

  // In my team by this row's fields — but if the server has explicitly said no,
  // it is right and the row is stale (membership can be revoked mid-session).
  if (serverSaidNo) return "You can no longer save changes to this task.";

  return null;
}

/** True when the task now open cannot be saved by this caller. */
export function isTaskReadOnly(task) {
  return taskWriteBlock(task) != null;
}

/** The caller's effective project role, or null before it has loaded. */
export function currentRole() {
  return state.role;
}

/**
 * Load the project and remember the caller's role.
 * Returns the project, or null if it could not be loaded.
 */
export async function loadProjectPermissions(projectId) {
  if (!projectId) return null;
  try {
    const res = await apiFetch(`/api/projects/${encodeURIComponent(projectId)}`);
    if (!res || !res.ok) return null;
    state.project = await res.json();
    state.role = state.project.my_role;
    return state.project;
  } catch (err) {
    console.error("Could not load project permissions", err);
    return null;
  }
}

// --- banner ----------------------------------------------------------------

function bannerHost() {
  if (state.banner) return state.banner;
  const el = document.createElement("div");
  el.id = "permissionBanner";
  el.className = "permission-banner";
  el.setAttribute("role", "status");
  document.body.prepend(el);
  state.banner = el;
  return el;
}

function showBanner(message, kind = "info") {
  const el = bannerHost();
  el.className = `permission-banner is-${kind}`;
  el.innerHTML = escapeHTML(message);
  el.hidden = false;
}

function hideBanner() {
  if (state.banner) state.banner.hidden = true;
}

/**
 * Decide and show the banner for the task now open.
 *
 * Shown **before** any drawing happens, not on the first rejected save: finding
 * out that your last ten minutes cannot be saved is the failure this prevents.
 */
export function updateTaskBanner(task) {
  state.task = task;

  const block = taskWriteBlock(task);

  // Read-only is applied per *task*, not just per project: an annotator with a
  // perfectly good role still cannot write a task assigned to someone else, and
  // discovering that only when Save fails is the whole complaint this fixes.
  document.body.classList.toggle("is-read-only", block != null);

  if (block) {
    showBanner(block, "warn");
  } else {
    hideBanner();
  }
}

/** Remember which teams the caller belongs to, for the banner check above. */
export function setMyTeams(teams) {
  state.myTeamIds = (teams || []).map((t) => t.id);
}

/** Remember who the caller is, so a task assigned to *them* stays writable. */
export function setMyUserId(userId) {
  state.myUserId = userId ?? null;
}

// --- read-only mode --------------------------------------------------------

/**
 * Disable the drawing surface when the caller cannot write.
 *
 * Adds a class the stylesheet uses to grey out and block pointer events on the
 * tool palette, rather than disabling each control individually — a viewer
 * gains nothing from a half-live toolbar, and one class is far easier to keep
 * correct than a list of ids that drifts as tools are added.
 *
 * Sets the class from the *project* role only. `updateTaskBanner` re-evaluates
 * it per task and may switch it back on for a task assigned elsewhere, so this
 * must toggle rather than one-way add — otherwise moving from a blocked task to
 * a writable one would leave the canvas dead.
 *
 * This is ergonomics, not security: every write is refused server-side anyway.
 */
export function applyReadOnlyMode() {
  document.body.classList.toggle("is-read-only", isReadOnly());
}

// --- save split-button menu ------------------------------------------------

/**
 * Wire and render the save split-button menu and the task status pill.
 *
 * The split button has two halves:
 *   Left  = "Save" — handled by init.js / saveButton (unchanged).
 *   Right = "▾"    — opens this menu with role-gated options.
 *
 * Menu options:
 *   Annotator only  : "Save as Complete"
 *   Reviewer+       : "Save as Complete", divider, "Approve", "Reject"
 *
 * Hidden entirely when the task is not writable by this user.
 *
 * @param {object}   task            the gallery task object
 * @param {Function} saveAndComplete called to trigger a save-then-status-change
 *                                   with signature (newStatus, opts?) => void
 * @param {Function} onStatusChange  called after a successful review action
 *                                   with the new status string
 */
export function renderSaveSplitMenu(task, saveAndComplete, onStatusChange) {
  const group   = document.getElementById('saveSplitGroup');
  const chevron = document.getElementById('saveChevronBtn');
  const menu    = document.getElementById('saveSplitMenu');
  if (!group || !chevron || !menu) return;

  updateTaskStatusPill(task);

  // Hide the chevron when the user cannot write or there are no extra actions.
  if (!canAnnotate(state.role) || !task) {
    chevron.hidden = true;
    menu.hidden = true;
    return;
  }

  const blocked = taskWriteBlock(task);
  if (blocked) {
    chevron.hidden = true;
    menu.hidden = true;
    return;
  }

  // Annotator-only: on Approved/Rejected tasks, only show "Re-open (In Progress)"
  // so they can act on reviewer feedback. They cannot approve/reject — that's
  // what the isReviewer block below handles.
  const isReviewer = canReview(state.role);
  const isAnnotatorOnly = !isReviewer;
  const inReviewState = isReviewStatus(task.status);

  chevron.hidden = false;

  // Build menu items.
  const items = [];

  if (isAnnotatorOnly && inReviewState) {
    // Rejected only — an annotator re-opens work sent back to them for rework.
    //
    // Approved-group statuses are deliberately excluded: re-opening one is a
    // review verdict (the server refuses it, _require_review_role_for_status),
    // and an approved task never reaches this code anyway because
    // `taskWriteBlock` above hides the whole menu. The condition is narrowed
    // rather than left to that accident, so the intent survives a refactor.
    if (task.status === 'Rejected') {
      items.push({ label: '↩ Re-open (In Progress)', action: 'inprogress' });
    }
  } else if (task.status !== 'Completed') {
    items.push({ label: '✓ Save as Complete', action: 'complete' });
  } else {
    items.push({ label: '↩ Save as In Progress', action: 'inprogress' });
  }

  if (isReviewer) {
    items.push({ divider: true });
    if (task.status === 'Completed' || task.status === 'Rejected') {
      // One entry per approval batch. They are identical verdicts — the batch
      // only decides which export picks the task up — so they are listed
      // together rather than hidden behind a second menu. `action` carries the
      // review verb, which is the status lowercased.
      for (const s of APPROVED_STATUSES) {
        items.push({ label: `✓ ${s}`, action: `review:${s.toLowerCase()}` });
      }
    }
    if (task.status === 'Completed' || isApproved(task.status)) {
      items.push({ label: '✗ Reject', action: 'review:rejected', danger: true });
    }
  }

  // Rebuild menu DOM (clone-replace to drop all old listeners).
  menu.innerHTML = items.map((it) => {
    if (it.divider) return `<div class="menu-divider" aria-hidden="true"></div>`;
    return `<button type="button" data-action="${it.action}"${it.danger ? ' class="is-danger"' : ''}>${it.label}</button>`;
  }).join('');

  // Wire item clicks.
  menu.querySelectorAll('button[data-action]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      menu.hidden = true;
      chevron.setAttribute('aria-expanded', 'false');

      const action = btn.dataset.action;

      if (action === 'complete') {
        // Save annotations and mark Completed in one operation.
        saveAndComplete('Completed');
        return;
      }

      if (action === 'inprogress') {
        saveAndComplete('In Progress');
        return;
      }

      // Review actions — use the review endpoint so they are logged. The verb
      // rides in the action itself ("review:verified"), so every batch goes
      // through the same path and lands in the audit trail.
      if (!action.startsWith('review:')) return;
      const reviewAction = action.slice('review:'.length);
      let note = null;
      if (reviewAction === 'rejected') {
        note = prompt('Why is this task being sent back?');
        if (note == null) return; // cancelled
      }

      const res = await apiFetch(`/api/tasks/${encodeURIComponent(task.id)}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: reviewAction, note: note || null }),
      });

      if (!res) return;
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        showBanner(body?.detail || `Could not record that review (${res.status}).`, 'warn');
        return;
      }

      const result = await res.json().catch(() => null);
      // Prefer the server's answer; the local fallback maps the verb back to
      // its status the same way REVIEW_ACTION_STATUS does server-side.
      task.status = result?.task_status ?? statusForReviewAction(reviewAction);
      onStatusChange?.(task.status);
      // Re-render menu and pill for the new status.
      renderSaveSplitMenu(task, saveAndComplete, onStatusChange);
    });
  });

  // Toggle open/close on chevron click.
  // Clone-replace to avoid stacking listeners across re-renders.
  const freshChevron = chevron.cloneNode(true);
  chevron.replaceWith(freshChevron);
  freshChevron.addEventListener('click', (e) => {
    e.stopPropagation();
    const open = menu.hidden;
    menu.hidden = !open;
    freshChevron.setAttribute('aria-expanded', open ? 'true' : 'false');
  });

  // Close on outside click.
  const outsideHandler = (e) => {
    const grp = document.getElementById('saveSplitGroup');
    if (grp && !grp.contains(e.target)) {
      menu.hidden = true;
      const ch = document.getElementById('saveChevronBtn');
      if (ch) ch.setAttribute('aria-expanded', 'false');
      document.removeEventListener('click', outsideHandler);
    }
  };
  document.addEventListener('click', outsideHandler);
}

/**
 * Update the task status pill colour and text.
 * Called on every task switch so the pill always reflects the live status.
 */
export function updateTaskStatusPill(task) {
  const pill = document.getElementById('taskStatusPill');
  if (!pill) return;
  if (!task || !canAnnotate(state.role)) {
    pill.hidden = true;
    return;
  }
  const s = task.status || 'New';
  // Each approved-group batch has its own colour modifier — see statusClass.
  const cls = statusClass(s) || 'is-new';
  pill.className = `status-pill task-status-pill ${cls}`;
  pill.textContent = s;
  pill.hidden = false;
}

/**
 * @deprecated Kept for call-site compatibility. Delegates to renderSaveSplitMenu.
 */
export function renderStatusDropdown(task, onStatusChange) {
  // No-op: the split-button wiring is done by renderSaveSplitMenu via
  // refreshTaskPermissionUI in init.js. Kept so old import references compile.
}

/**
 * @deprecated Kept for call-site compatibility.
 */
export function renderReviewControls(host, task, onReviewed) {
  if (host) host.innerHTML = '';
}

/**
 * Report a permission failure from a save.
 *
 * Deliberately distinct from the 409 conflict flow, and deliberately
 * non-destructive: the draft and any queued write are left exactly as they are
 * (E-24). The user may regain access, and even if they do not, silently
 * discarding their work is the worst possible outcome.
 */
export function reportSaveForbidden(detail) {
  showBanner(
    `${detail || "You do not have permission to save this task."} ` +
    "Your unsaved work is kept in this browser.",
    "warn"
  );
}

/**
 * Report a save the server refused on the merits (422) — *not* a permission
 * problem and *not* an offline problem.
 *
 * Kept separate from `reportSaveForbidden` because the two were previously
 * conflated: a 422 was mapped onto the `forbidden` queue state and reported
 * with its wording, so an annotator working normally on their own task, against
 * a server that was up the whole time, was told they lacked permission and that
 * their "offline work" was stranded. All three claims were false, and the
 * message gave them nothing to act on.
 *
 * The payload is still kept (rule 18 — a refused save must never destroy
 * unsaved work), so the wording says the work is safe without implying it is
 * lost or that access was revoked.
 */
export function reportSaveRefused(detail) {
  showBanner(
    `${detail || "That save was refused."} Your work is still here and unsaved.`,
    "warn"
  );
}

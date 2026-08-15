/**
 * Column definitions and cell renderers for the Tasks table.
 *
 * Split out of `tasks.js` rather than added to it: that module was already ~460
 * lines of upload handling, modal wiring and bulk actions, and Teams adds a
 * Team column, an assignee that prefers a linked account, review row-actions
 * and per-role degradation. Keeping the *presentation* here leaves `tasks.js`
 * about behaviour, and makes "why does this cell look like that?" a
 * single-file question.
 *
 * Everything here is a pure function of (row, view-state). No fetching, no
 * listeners — `tasks.js` owns those and delegates clicks by `data-action`.
 */
import { escapeHTML, formatTime } from "../../utils.js?v=1";
import { canAnnotate, canManage, canReview } from "../../permissions.js?v=1";
import {
  TASK_STATUSES,
  isApproved,
  isReviewStatus,
  statusClass,
  statusSelectClass as statusSelectClassFor,
} from "../../task-status.js?v=1";

const ICON_EDIT = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/></svg>`;
const ICON_DELETE = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>`;
// A person with a "+" — the same visual language as the Assignee column.
const ICON_ASSIGN = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" y1="8" x2="19" y2="14"/><line x1="22" y1="11" x2="16" y2="11"/></svg>`;

/**
 * Task statuses shown in the filter. Re-exported from `task-status.js` (the
 * single mirror of `schemas.TASK_STATUSES`) rather than listed again here —
 * this module used to keep its own copy *and* a second inline copy in the
 * status <select> below, so adding a status meant finding both.
 */
export const STATUSES = TASK_STATUSES;

export function statusPill(status) {
  const s = status || "New";
  return `<span class="pill ${statusClass(s)}">${escapeHTML(s)}</span>`;
}

/**
 * CSS modifier class for the inline status <select>, keyed by status string.
 * Exported so tasks.js can swap the class on `change` without a full re-render.
 */
export function statusSelectClass(status) {
  return statusSelectClassFor(status);
}

/**
 * Comment and distinct-class counts for one task row.
 *
 * Prefers the server-supplied `comment_count` / `class_count`, which is how the
 * Tasks view gets these without downloading every annotation blob
 * (.devnotes/server-optimization/03_TASKS_PAGE.md). Falls back to counting a
 * hydrated `annotations` array so the function stays correct for any caller
 * that does have one — and so a client running against an older server, or a
 * cached bundle mid-rollout, degrades to the previous behaviour instead of
 * rendering zeroes.
 */
export function countAnnotations(task) {
  if (
    typeof task?.comment_count === "number" &&
    typeof task?.class_count === "number"
  ) {
    return { comments: task.comment_count, classes: task.class_count };
  }

  let anns = task.annotations;
  if (typeof anns === "string") {
    try { anns = JSON.parse(anns); } catch { anns = []; }
  }
  if (!Array.isArray(anns)) return { comments: 0, classes: 0 };
  const comments = anns.filter((a) => a.type === "comment").length;
  const classes = new Set(anns.filter((a) => a.labelId).map((a) => a.labelId)).size;
  return { comments, classes };
}

/**
 * The Team cell.
 *
 * "Unassigned" is not an error state — it is the shared pool, workable by
 * anyone with an annotate-capable grant (01_DESIGN.md § 3.3) — so it reads as
 * muted information rather than a warning.
 */
function teamCell(row, teamsById) {
  if (!row.assigned_team_id) {
    return `<span class="muted" title="Anyone with access can work on this task.">— Unassigned</span>`;
  }
  const name = teamsById.get(row.assigned_team_id) || `Team ${row.assigned_team_id}`;
  return `<span class="team-chip">${escapeHTML(name)}</span>`;
}

/**
 * The Assignee cell.
 *
 * Three states:
 *   - Individual assigned: show their name.
 *   - Team assigned, no individual: "Anyone in team" — the task is in the
 *     team pool and any member may take it.
 *   - Neither: "— Unassigned" — matches the Team column treatment and makes
 *     it clear a manager still needs to distribute this task.
 */
function assigneeCell(row, usersById) {
  if (row.assignee_user_id) {
    const name = usersById.get(row.assignee_user_id);
    // If the user isn't in the project's assignable-members map they have been
    // removed from their team; the server will have cleared assignee_user_id,
    // so this only fires transiently before the next table reload.
    return name
      ? escapeHTML(name)
      : `<span class="muted" title="This user has been removed from the team.">— Unassigned</span>`;
  }
  if (row.assigned_team_id) {
    // Team assigned but no specific person — anyone on the team may work it.
    return `<span class="muted" title="Any member of the assigned team can work on this task.">Anyone in team</span>`;
  }
  return `<span class="muted" title="No one has been assigned yet.">— Unassigned</span>`;
}

/**
 * Row actions, levelled by role.
 *
 * Review actions appear only where they mean something: a task nobody has
 * finished cannot be approved, and an already-approved task offers Reject
 * rather than a second Approve.
 */
function actionsCell(row, role) {
  const buttons = [];

  if (canReview(role)) {
    // Approve is offered on anything not already signed off; Reject on anything
    // not already sent back. Using the group rather than naming "Approved"
    // means a task approved into any batch offers Reject, not a pointless
    // second Approve.
    if (row.status === "Completed" || row.status === "Rejected") {
      buttons.push(`<button type="button" data-action="approve" title="Approve">✓</button>`);
    }
    if (row.status === "Completed" || isApproved(row.status)) {
      buttons.push(`<button type="button" data-action="reject" title="Reject — send back for rework">✗</button>`);
    }
  }

  // Editing metadata, assigning work and deleting are administrative, not
  // annotation. Assignment sits with `manager` because handing someone a task
  // now *reserves* it — others in the team can no longer save it — which is a
  // scheduling decision, not something an annotator should make for a peer.
  if (canManage(role)) {
    buttons.push(`<button type="button" data-action="assign" title="Assign to a team or person">${ICON_ASSIGN}</button>`);
    buttons.push(`<button type="button" data-action="edit" title="Edit task">${ICON_EDIT}</button>`);
    buttons.push(`<button type="button" data-action="delete" class="danger" title="Delete task">${ICON_DELETE}</button>`);
  }

  if (!buttons.length) return "";
  return `<div class="row-actions">${buttons.join("")}</div>`;
}

/**
 * Build the column set for the current viewer.
 *
 * @param {object} opts
 * @param {string} opts.role         the caller's effective project role
 * @param {string|number} opts.projectId
 * @param {Map} opts.teamsById       id → team name
 * @param {Map} opts.usersById       id → username
 * @param {object} opts.lockCache    taskId → { locked, locked_by }
 * @param {object} opts.currentUser  the signed-in user object (for assignee check)
 */
/**
 * Query string for the canvas link: the task to open, plus the sort and
 * filters that define the sequence it should page through.
 *
 * Only non-default values are emitted, so the ordinary case stays a short URL.
 *
 * `page` rides along without affecting the canvas, which walks the whole
 * ordered set rather than a page of it. It is carried so the workspace's own
 * "back to tasks" link can return to the page the annotator left — the browser
 * Back button restores it from history, but that link builds a fresh URL.
 */
export function canvasQuery(projectId, taskId, view) {
  const params = new URLSearchParams({ projectId, taskId });
  if (view) {
    if (view.page > 1) params.set("page", String(view.page));
    if (view.sortKey && view.sortKey !== "description") params.set("sort", view.sortKey);
    if (view.sortDesc) params.set("order", "desc");
    if (view.query) params.set("q", view.query);
    for (const key of ["status", "team", "assignee"]) {
      const value = view.filters?.[key];
      if (value && value !== "All") params.set(key, value);
    }
  }
  return params.toString();
}

/**
 * @param {() => object} [viewState]  returns the table's current
 *        page/sort/filter state, used to build the canvas link. A function
 *        rather than a value because columns are built once at mount while the
 *        state changes on every sort, filter and page.
 */
export function buildColumns({ role, projectId, teamsById, usersById, lockCache, currentUser, viewState }) {
  const isReviewer = canReview(role);
  const isAnnotatorOnly = canAnnotate(role) && !isReviewer;

  return [
    {
      key: "image_path",
      label: "",
      sortable: false,
      width: "56px",
      render: (r) => r.image_path
        ? `<img class="task-thumb" src="/${escapeHTML(String(r.image_path).replace(/\\/g, "/"))}" alt="">`
        : "",
    },
    {
      key: "description",
      label: "Filename",
      // The link carries the table's sort and filters. The canvas rebuilds its
      // image sequence from GET /api/tasks/order using exactly these, so
      // prev/next walks the list the user was looking at — filtered to
      // "Rejected", say — instead of the whole project.
      render: (r) =>
        `<a class="task-filename" href="app.html?${canvasQuery(projectId, r.id, viewState?.())}" title="${escapeHTML(r.description || "")}">${escapeHTML(r.description || "")}</a>`,
    },
    { key: "assigned_team_id", label: "Team", render: (r) => teamCell(r, teamsById) },
    { key: "assignee", label: "Assignee", render: (r) => assigneeCell(r, usersById) },
    {
      // Show a "busy" badge when another annotator has the task open.
      key: "_lock",
      label: "",
      sortable: false,
      width: "56px",
      render: (r) => {
        const lock = lockCache[r.id];
        if (!lock || !lock.locked) return "";
        return `<span class="lock-chip" title="In use by another annotator">● busy</span>`;
      },
    },
    {
      key: "status",
      label: "Status",
      render: (r) => {
        // Reviewers+ see a select with all status options so they can Approve /
        // Reject directly from the table without opening each task.
        if (isReviewer) {
          const opts = TASK_STATUSES
            .map((s) => `<option value="${s}"${r.status === s ? ' selected' : ''}>${s}</option>`)
            .join('');
          return `<select class="status-select ${statusSelectClass(r.status)}" data-task-id="${r.id}" data-original="${escapeHTML(r.status || '')}" aria-label="Task status">${opts}</select>`;
        }

        // Annotators assigned to this task can move it between their own states
        // (New / In Progress / Completed) and re-open it from a reviewer state
        // (any approved-group status / Rejected → In Progress). The select
        // always shows the real current status so the displayed value is never
        // wrong.
        const assignedToMe = currentUser && r.assignee_user_id === currentUser.id;
        if (isAnnotatorOnly && assignedToMe) {
          // Base options the annotator can set. Deliberately excludes every
          // approved-group status: approving is the reviewer's verdict, and the
          // server refuses it here regardless of what this select offers.
          const writableStatuses = ['In Progress', 'Completed'];
          // If the task is currently in a reviewer state, add it as a
          // display-only option (so the select shows the right label) and
          // allow In Progress to re-open it.
          const inReviewState = isReviewStatus(r.status);
          const displayStatuses = inReviewState
            ? [r.status, ...writableStatuses]
            : ['New', ...writableStatuses];
          const annotatorOpts = displayStatuses
            .map((s) => {
              // The current reviewer-set status is shown but not selectable
              // — the annotator can only move away from it, not re-approve.
              const disabled = inReviewState && s === r.status ? ' disabled' : '';
              return `<option value="${s}"${r.status === s ? ' selected' : ''}${disabled}>${s}</option>`;
            })
            .join('');
          return `<select class="status-select ${statusSelectClass(r.status)}" data-task-id="${r.id}" data-original="${escapeHTML(r.status || '')}" aria-label="Task status">${annotatorOpts}</select>`;
        }

        return statusPill(r.status);
      },
    },
    {
      key: "time_spent",
      label: "Time",
      render: (r) => r.time_spent
        ? `<span class="mono-cell">${formatTime(r.time_spent)}</span>`
        : `<span class="muted">—</span>`,
    },
    {
      key: "updated_at",
      label: "Updated",
      render: (r) => {
        if (!r.updated_at) return `<span class="muted">—</span>`;
        const d = new Date(r.updated_at.endsWith("Z") ? r.updated_at : r.updated_at + "Z");
        return `<span class="stamp-cell">${isNaN(d) ? escapeHTML(r.updated_at) : d.toLocaleString()}</span>`;
      },
    },
    { key: "classes", label: "Classes", sortable: false, align: "center", render: (r) => String(countAnnotations(r).classes) },
    { key: "comments", label: "Comments", sortable: false, align: "center", render: (r) => `💬 ${countAnnotations(r).comments}` },
    {
      key: "actions",
      label: "",
      sortable: false,
      align: "center",
      render: (r) => actionsCell(r, role),
    },
  ];
}

/** Whether the bulk bar should exist at all for this role. */
export function showsBulkBar(role) {
  return canManage(role) || canReview(role);
}

/** Whether upload controls and the drop zone should render. */
export function showsUpload(role) {
  return canManage(role);
}

/** Whether the caller can select rows (selection only drives bulk actions). */
export function showsSelection(role) {
  return showsBulkBar(role);
}

/** Whether this role can open a task for editing at all. */
export function canOpenCanvas(role) {
  return canAnnotate(role);
}

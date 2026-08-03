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

const ICON_EDIT = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/></svg>`;
const ICON_DELETE = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>`;
// A person with a "+" — the same visual language as the Assignee column.
const ICON_ASSIGN = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><line x1="19" y1="8" x2="19" y2="14"/><line x1="22" y1="11" x2="16" y2="11"/></svg>`;

/** Task statuses shown in the filter, mirroring `schemas.TASK_STATUSES`. */
export const STATUSES = ["New", "In Progress", "Completed", "Approved", "Rejected"];

export function statusPill(status) {
  const s = status || "New";
  const cls =
    s === "Completed" ? "is-completed"
      : s === "In Progress" ? "is-progress"
      : s === "Approved" ? "is-approved"
      // Rejected is a warning colour, deliberately distinct from Approved:
      // "reviewed and sent back" must not read as "reviewed and accepted".
      : s === "Rejected" ? "is-rejected" : "";
  return `<span class="pill ${cls}">${escapeHTML(s)}</span>`;
}

export function countAnnotations(task) {
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
 * Prefers the linked account (`assignee_user_id`) and falls back to the legacy
 * free-text `assignee`, shown in italics with a tooltip. The legacy string was
 * typed into a prompt and matches no account, so presenting the two identically
 * would imply an accountability that is not there (04_UI_UX.md § 6.3).
 */
function assigneeCell(row, usersById) {
  if (row.assignee_user_id) {
    const name = usersById.get(row.assignee_user_id) || `User ${row.assignee_user_id}`;
    return escapeHTML(name);
  }
  if (row.assignee) {
    return `<span class="legacy-assignee" title="Legacy name, not a linked account">${escapeHTML(row.assignee)}</span>`;
  }
  return `<span class="muted">—</span>`;
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
    if (row.status === "Completed" || row.status === "Rejected") {
      buttons.push(`<button type="button" data-action="approve" title="Approve">✓</button>`);
    }
    if (row.status === "Completed" || row.status === "Approved") {
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
 */
export function buildColumns({ role, projectId, teamsById, usersById, lockCache }) {
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
      render: (r) =>
        `<a class="task-filename" href="app.html?projectId=${encodeURIComponent(projectId)}&taskId=${encodeURIComponent(r.id)}" title="${escapeHTML(r.description || "")}">${escapeHTML(r.description || "")}</a>`,
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
    { key: "status", label: "Status", render: (r) => statusPill(r.status) },
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

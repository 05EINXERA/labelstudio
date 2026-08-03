/**
 * Tasks: image upload + task table (tracker P3.2).
 *
 * Ported from `project_details.js` onto `js/components/data-table.js`. Two
 * things intentionally changed on the way over:
 *  - Assignment is advisory free-text only. All annotators share one account,
 *    so assignment is a coordination convention displayed in the table, not a
 *    server-enforced boundary. The previous <select> was populated from
 *    /api/team (a list of registered team members) which is meaningless in a
 *    single-shared-account deployment (L2/T2.3 trade-off decision).
 *  - The upload endpoint now reports per-file success/failure (P3.1), so the
 *    UI shows a summary instead of a single "Upload failed" alert.
 */
import { apiFetch } from "../../api.js?v=1";
import { escapeHTML, formatTime } from "../../utils.js?v=1";
import { createDataTable } from "../../components/data-table.js?v=1";
import { fillTeamSelect } from "../../components/team-picker.js?v=1";
import { canManage, canReview } from "../../permissions.js?v=1";
import { openAssignDialog } from "./assign-modal.js?v=1";
import {
  STATUSES,
  buildColumns,
  showsBulkBar,
  showsSelection,
  showsUpload,
  statusPill,
} from "./task-columns.js?v=2";

let root = null;
let ctx = null;
let table = null;

// T2.2 — lock status cache: {taskId: {locked: bool, locked_by: str}}
// Populated asynchronously after the task list renders.
const _lockCache = {};

async function _refreshLockCache(tasks) {
  // Fetch lock status for every task in parallel (fire-and-forget batches).
  // Errors are silently swallowed — lock display is best-effort.
  await Promise.allSettled(tasks.map(async (t) => {
    try {
      const res = await apiFetch(`/api/tasks/${t.id}/lock-status`);
      if (res && res.ok) _lockCache[t.id] = await res.json();
    } catch { /* best-effort */ }
  }));
}

function template() {
  return `
    <div class="mgmt-title-row">
      <div>
        <p class="mgmt-eyebrow">Project</p>
        <h2>Tasks</h2>
      </div>
      <div style="display:flex; gap:10px;">
        <button type="button" class="primary" id="uploadBtn" style="padding:9px 16px;border-radius:8px;font-weight:600;">+ Upload images</button>
        <input type="file" id="uploadInput" accept="image/png,image/jpeg,image/gif,image/webp" multiple style="display:none;">
      </div>
    </div>

    <div id="dropZone" class="mgmt-empty" style="border:2px dashed var(--line); border-radius:10px; margin-bottom:16px; cursor:pointer;">
      <p>Drag &amp; drop images here, or click "Upload images"</p>
    </div>

    <div id="uploadSummary"></div>
    <div id="errorBanner" class="mgmt-error" style="display:none;"></div>

    <div class="bulk-bar" id="bulkBar">
      <span class="count" id="bulkCount"></span>
      <!-- Two assign buttons, deliberately. "Assign" writes the real
           team/user FKs and is what everyone should use. "Set name (legacy)"
           writes the free-text `assignee` column that predates Teams; it is
           kept only because existing projects still carry those strings and
           removing the editor would strand them. Phase 5 F2 deletes it. -->
      <button type="button" class="tool-button" id="bulkTeamBtn">Assign…</button>
      <button type="button" class="tool-button" id="bulkAssignBtn" title="Legacy free-text name, not a linked account">Set name (legacy)</button>
      <button type="button" class="tool-button" id="bulkDeleteBtn" style="color:#e05260;border-color:rgba(224,82,96,.3);">Bulk delete</button>
    </div>

    <div class="mgmt-toolbar">
      <input type="search" id="searchInput" placeholder="Search filename…" aria-label="Search tasks">
      <select id="statusFilter" aria-label="Filter by status">
        <option value="All">All statuses</option>
        ${STATUSES.map((s) => `<option value="${s}">${s}</option>`).join("")}
      </select>
      <select id="teamFilter" aria-label="Filter by team"></select>
      <select id="assigneeFilter" aria-label="Filter by assignee">
        <option value="All">Everyone</option>
        <option value="mine">My tasks</option>
        <option value="unassigned">Nobody specific</option>
      </select>
    </div>

    <div id="tableMount"></div>

    <div class="modal-overlay" id="editModal">
      <div class="modal-content">
        <div class="modal-header">
          <h2>Edit task</h2>
          <button class="modal-close" id="editClose" type="button">&times;</button>
        </div>
        <form id="editForm">
          <input type="hidden" id="editId">
          <div class="modal-body" style="display:grid; gap:14px;">
            <img id="editPreview" src="" style="max-width:100%;max-height:180px;border-radius:6px;border:1px solid var(--line);display:none;">
            <label style="display:grid;gap:6px;">
              <span style="font-size:.85rem;color:var(--muted);">Filename</span>
              <input type="text" id="editDescription" required style="padding:9px;border-radius:6px;border:1px solid var(--line);background:var(--panel);color:var(--ink);">
            </label>
            <label style="display:grid;gap:6px;">
              <span style="font-size:.85rem;color:var(--muted);">Assignee <span style="font-weight:400;font-style:italic;">(optional, advisory only)</span></span>
              <input type="text" id="editAssignee" placeholder="Enter name…" autocomplete="off" style="padding:9px;border-radius:6px;border:1px solid var(--line);background:var(--panel);color:var(--ink);">
            </label>
            <label style="display:grid;gap:6px;">
              <span style="font-size:.85rem;color:var(--muted);">Status</span>
              <select id="editStatus" style="padding:9px;border-radius:6px;border:1px solid var(--line);background:var(--panel);color:var(--ink);">
                ${STATUSES.map((s) => `<option value="${s}">${s}</option>`).join("")}
              </select>
            </label>
          </div>
          <div class="modal-footer">
            <button type="button" class="tool-button" id="editCancel">Cancel</button>
            <button type="submit" class="primary">Save</button>
          </div>
        </form>
      </div>
    </div>

    <div class="modal-overlay" id="assignModal">
      <div class="modal-content">
        <div class="modal-header">
          <h2>Bulk assign</h2>
          <button class="modal-close" id="assignClose" type="button">&times;</button>
        </div>
        <form id="assignForm">
          <div class="modal-body">
            <label style="display:grid;gap:6px;">
              <span style="font-size:.85rem;color:var(--muted);">Assignee <span style="font-weight:400;font-style:italic;">(optional, advisory only)</span></span>
              <input type="text" id="assignInput" placeholder="Enter name, or leave blank to unassign…" autocomplete="off" style="padding:9px;border-radius:6px;border:1px solid var(--line);background:var(--panel);color:var(--ink);">
            </label>
          </div>
          <div style="display:flex;gap:10px;justify-content:flex-end;padding:16px;">
            <button type="button" class="tool-button" id="assignCancel">Cancel</button>
            <button type="submit" class="primary" style="padding:9px 18px;border-radius:6px;">Apply</button>
          </div>
        </form>
      </div>
    </div>
  `;
}

function el(id) { return root.querySelector(`#${id}`); }

function showError(message) {
  const banner = el("errorBanner");
  banner.textContent = message;
  banner.style.display = "block";
}

function clearError() {
  const banner = el("errorBanner");
  if (banner) banner.style.display = "none";
}

// --- data --------------------------------------------------------------

async function loadTasks() {
  const res = await apiFetch(`/api/tasks?projectId=${encodeURIComponent(ctx.projectId)}`);
  if (!res) return;
  if (!res.ok) {
    showError(`Could not load tasks (${res.status}).`);
    return;
  }
  clearError();
  const tasks = await res.json();
  table.setRows(tasks);
  // T2.2 — refresh lock badges after the table renders (async, non-blocking).
  _refreshLockCache(tasks).then(() => table.setRows(tasks));
}

// --- upload --------------------------------------------------------------

function renderUploadSummary(body) {
  const summary = el("uploadSummary");
  if (!body) { summary.innerHTML = ""; return; }
  const parts = [];
  if (body.uploaded?.length) {
    parts.push(`<div class="mgmt-empty" style="text-align:left;padding:10px 14px;color:var(--accent-dark);">
        ✓ Uploaded ${body.uploaded.length} image${body.uploaded.length === 1 ? "" : "s"}.</div>`);
  }
  if (body.failed?.length) {
    parts.push(`<div class="mgmt-error">
        ${body.failed.length} file${body.failed.length === 1 ? "" : "s"} could not be uploaded:
        <ul style="margin:6px 0 0 18px;">
          ${body.failed.map((f) => `<li>${escapeHTML(f.filename || "unknown")} — ${escapeHTML(f.error)}</li>`).join("")}
        </ul>
      </div>`);
  }
  summary.innerHTML = parts.join("");
}

async function uploadFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;

  const formData = new FormData();
  files.forEach((f) => formData.append("file", f));

  const assignee = localStorage.getItem("dataset_username") || "";
  try {
    const res = await apiFetch(
      `/api/projects/${encodeURIComponent(ctx.projectId)}/upload?assignee=${encodeURIComponent(assignee)}`,
      { method: "POST", body: formData }
    );
    if (!res) return;
    if (!res.ok) {
      showError(`Upload failed (${res.status}).`);
      return;
    }
    clearError();
    renderUploadSummary(await res.json());
    await loadTasks();
  } catch (err) {
    console.error("Upload failed", err);
    showError("Upload failed. Check your connection and try again.");
  }
}

function bindUpload() {
  const btn = el("uploadBtn");
  const input = el("uploadInput");
  const zone = el("dropZone");

  btn.addEventListener("click", () => input.click());
  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", (e) => {
    uploadFiles(e.target.files);
    input.value = "";
  });

  ["dragenter", "dragover"].forEach((evt) =>
    zone.addEventListener(evt, (e) => { e.preventDefault(); zone.style.borderColor = "var(--accent)"; })
  );
  ["dragleave", "drop"].forEach((evt) =>
    zone.addEventListener(evt, (e) => { e.preventDefault(); zone.style.borderColor = "var(--line)"; })
  );
  zone.addEventListener("drop", (e) => {
    e.preventDefault();
    if (e.dataTransfer?.files?.length) uploadFiles(e.dataTransfer.files);
  });
}

// --- edit modal ------------------------------------------------------------

function openEditModal(task) {
  el("editId").value = task.id;
  el("editDescription").value = task.description || "";
  el("editAssignee").value = task.assignee || "";
  el("editStatus").value = task.status || "New";
  const preview = el("editPreview");
  if (task.image_path) {
    preview.src = "/" + String(task.image_path).replace(/\\/g, "/");
    preview.style.display = "inline-block";
  } else {
    preview.style.display = "none";
  }
  el("editModal").classList.add("is-active");
}

function closeEditModal() {
  el("editModal").classList.remove("is-active");
}

function bindEditModal() {
  el("editClose").addEventListener("click", closeEditModal);
  el("editCancel").addEventListener("click", closeEditModal);
  el("editModal").addEventListener("click", (e) => { if (e.target === el("editModal")) closeEditModal(); });

  el("editForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = el("editId").value;
    try {
      const res = await apiFetch(`/api/tasks/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          description: el("editDescription").value,
          assignee: el("editAssignee").value,
          status: el("editStatus").value,
        }),
      });
      if (!res) return;
      if (!res.ok) {
        showError(`Could not save the task (${res.status}).`);
        return;
      }
      closeEditModal();
      await loadTasks();
    } catch (err) {
      console.error("Failed to save task", err);
      showError("Could not save the task.");
    }
  });
}

// --- bulk actions ------------------------------------------------------------

function updateBulkBar(selection) {
  const bar = el("bulkBar");
  bar.classList.toggle("is-active", selection.size > 0);
  el("bulkCount").textContent = `${selection.size} selected`;
}

function bindBulkActions() {
  // A reviewer gets the bulk bar (for review actions) but not the
  // administrative buttons, so those are removed rather than left to 403.
  if (!canManage(ctx.myRole)) {
    el("bulkAssignBtn")?.remove();
    el("bulkTeamBtn")?.remove();
    el("bulkDeleteBtn")?.remove();
    return;
  }

  el("bulkTeamBtn").addEventListener("click", () => assignTasks(null));

  el("bulkDeleteBtn").addEventListener("click", async () => {
    const ids = [...table.getSelection()];
    if (!ids.length) return;
    if (!confirm(`Delete ${ids.length} task${ids.length === 1 ? "" : "s"}? This cannot be undone.`)) return;
    try {
      const res = await apiFetch("/api/tasks/bulk-delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }),
      });
      if (!res) return;
      if (!res.ok) {
        showError(`Could not delete the selected tasks (${res.status}).`);
        return;
      }
      table.clearSelection();
      await loadTasks();
    } catch (err) {
      console.error("Bulk delete failed", err);
      showError("Could not delete the selected tasks.");
    }
  });

  el("bulkAssignBtn").addEventListener("click", () => {
    if (table.getSelection().size === 0) return;
    el("assignInput").value = "";
    el("assignModal").classList.add("is-active");
  });
  el("assignClose").addEventListener("click", () => el("assignModal").classList.remove("is-active"));
  el("assignCancel").addEventListener("click", () => el("assignModal").classList.remove("is-active"));
  el("assignModal").addEventListener("click", (e) => {
    if (e.target === el("assignModal")) el("assignModal").classList.remove("is-active");
  });

  el("assignForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const ids = [...table.getSelection()];
    if (!ids.length) return;
    try {
      const res = await apiFetch("/api/tasks/bulk-update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids, assignee: el("assignInput").value.trim() }),
      });
      if (!res) return;
      if (!res.ok) {
        showError(`Could not assign the selected tasks (${res.status}).`);
        return;
      }
      el("assignModal").classList.remove("is-active");
      table.clearSelection();
      await loadTasks();
    } catch (err) {
      console.error("Bulk assign failed", err);
      showError("Could not assign the selected tasks.");
    }
  });
}

// --- teams, reviewers and filters ------------------------------------------

// id → display name, so the Team and Assignee columns can render a name
// without a request per row. Both are small and change rarely.
const teamsById = new Map();
const usersById = new Map();
// Full roster of everyone assignable on this project, carrying the team each
// was reached through so the dialog can narrow the list once a team is picked.
let assignableMembers = [];

async function loadLookups() {
  // Teams granted on *this project*, not the caller's own teams: a task can be
  // assigned to a team the caller does not belong to, and its name must still
  // render. Grants are readable by any project member.
  const res = await apiFetch(`/api/projects/${encodeURIComponent(ctx.projectId)}/grants`);
  if (res?.ok) {
    for (const grant of await res.json()) {
      teamsById.set(grant.team_id, grant.team_name);
    }
  }

  // One project-scoped request instead of walking every team's roster: the
  // caller assigning work is often not a member of the teams they distribute
  // to, and /api/teams/{id}/members deliberately 404s for non-members (E-16).
  const membersRes = await apiFetch(
    `/api/projects/${encodeURIComponent(ctx.projectId)}/assignable-members`
  );
  if (membersRes?.ok) {
    assignableMembers = await membersRes.json();
    for (const m of assignableMembers) usersById.set(m.user_id, m.username);
  }

  const filter = el("teamFilter");
  if (filter) {
    fillTeamSelect(filter, [...teamsById].map(([id, name]) => ({ id, name })), {
      extraOptions: [
        { value: "All", label: "All teams" },
        { value: "unassigned", label: "Unassigned" },
      ],
    });
    filter.value = "All";
  }
}

/**
 * The team filter is applied through `setFilter` on a derived key rather than
 * on `assigned_team_id` directly, because "unassigned" is a null match and the
 * table's filter compares by equality.
 */
/**
 * Filter by who a task is reserved for.
 *
 * "My tasks" is the one annotators live in: once work is distributed, the
 * default view is everything, and they need one click to see only what is
 * theirs. This is presentation — the *enforcement* is server-side, so switching
 * back to "Everyone" shows other people's tasks but does not make them
 * writable.
 */
function applyAssigneeFilter(value) {
  const myId = ctx.currentUser?.id;
  for (const row of table.getRows()) {
    row._assigneeFilter =
      row.assignee_user_id == null
        ? "unassigned"
        : (row.assignee_user_id === myId ? "mine" : `user-${row.assignee_user_id}`);
  }
  table.setFilter("_assigneeFilter", value === "All" ? "All" : value);
}

function applyTeamFilter(value) {
  const rows = table.getRows();
  for (const row of rows) {
    row._teamFilter = row.assigned_team_id == null ? "unassigned" : String(row.assigned_team_id);
  }
  table.setFilter("_teamFilter", value === "All" ? "All" : value);
}

/**
 * Bulk assign a team to the selected tasks.
 *
 * Uses a prompt rather than a fourth modal: the choice is one value from a
 * short list the user has just seen in the filter, and a modal here would be
 * more chrome than decision.
 */
/**
 * Assign one task, or every selected task, to a team and optionally a person.
 *
 * Both entry points share this function: the question is identical and only the
 * id list differs. `row` is null for the bulk case.
 */
async function assignTasks(row) {
  const ids = row ? [row.id] : [...table.getSelection()];
  if (!ids.length) return;

  const teams = [...teamsById].map(([id, name]) => ({ id, name }));
  if (!teams.length) {
    showError("No teams have access to this project yet. Grant one on the Access tab.");
    return;
  }

  const choice = await openAssignDialog({
    title: row ? `Assign "${row.description || "task"}"` : `Assign ${ids.length} task(s)`,
    teams,
    members: assignableMembers,
    // Pre-select what the row already has, so opening the dialog on an assigned
    // task shows its current state rather than resetting it.
    current: row ? { teamId: row.assigned_team_id, userId: row.assignee_user_id } : {},
  });
  if (!choice) return; // cancelled

  // One task goes through the per-task endpoint so its warnings (E-10) surface
  // verbatim; many go through bulk-assign, which reports updated/skipped.
  const res = row
    ? await apiFetch(`/api/tasks/${encodeURIComponent(row.id)}/assignment`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(choice),
      })
    : await apiFetch("/api/tasks/bulk-assign", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids, ...choice }),
      });
  if (!res) return;

  if (!res.ok) {
    const body = await res.json().catch(() => null);
    showError(body?.detail || `Could not assign those tasks (${res.status}).`);
    return;
  }

  const result = await res.json();
  clearError();
  const notes = [];
  if (result.skipped) notes.push(`${result.skipped} skipped — you cannot manage them.`);
  if (result.warnings?.length) notes.push(result.warnings.join(" "));
  if (notes.length) showError(notes.join(" "));

  if (!row) table.clearSelection();
  await loadTasks();
}

async function review(row, action) {
  // A rejection carries a reason: "sent back" with no note is the thing
  // annotators complain about most.
  let note = null;
  if (action === "rejected") {
    note = prompt(`Why is "${row.description}" being sent back?`);
    if (note == null) return; // cancelled
  }

  const res = await apiFetch(`/api/tasks/${encodeURIComponent(row.id)}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, note: note || null }),
  });
  if (!res) return;
  if (res.status === 403) {
    const body = await res.json().catch(() => null);
    showError(body?.detail || "You do not have permission to review this task.");
    return;
  }
  if (!res.ok) {
    showError(`Could not record that review (${res.status}).`);
    return;
  }
  clearError();
  await loadTasks();
}

// --- mount -------------------------------------------------------------

export async function mount(hostRoot, hostCtx) {
  root = hostRoot;
  ctx = hostCtx;
  root.innerHTML = template();

  const role = ctx.myRole;

  table = createDataTable({
    mount: el("tableMount"),
    rowId: (r) => r.id,
    // Selection exists only to drive the bulk bar, so a role without one gets
    // no checkbox column either.
    selectable: showsSelection(role),
    sortKey: "updated_at",
    sortDesc: true,
    emptyMessage: "No tasks yet. Upload images to get started.",
    onSelectionChange: updateBulkBar,
    matches: (row, q) => String(row.description || "").toLowerCase().includes(q),
    columns: buildColumns({
      role,
      projectId: ctx.projectId,
      teamsById,
      usersById,
      lockCache: _lockCache,
    }),
  });

  // Upload and bulk controls are removed from the DOM rather than disabled:
  // a greyed-out button the caller can never use is just clutter, and their
  // handlers assume permissions the server would refuse anyway.
  if (!showsUpload(role)) {
    el("uploadBtn")?.remove();
    el("dropZone")?.remove();
  } else {
    bindUpload();
  }

  if (canManage(role)) bindEditModal();

  if (showsBulkBar(role)) bindBulkActions();
  else el("bulkBar")?.remove();

  el("searchInput").addEventListener("input", (e) => table.setQuery(e.target.value));
  el("statusFilter").addEventListener("change", (e) => table.setFilter("status", e.target.value));
  el("teamFilter")?.addEventListener("change", (e) => applyTeamFilter(e.target.value));
  el("assigneeFilter")?.addEventListener("change", (e) => applyAssigneeFilter(e.target.value));

  table.onAction("edit", (row) => openEditModal(row));
  table.onAction("delete", async (row) => {
    if (!confirm(`Delete "${row.description}"? This cannot be undone.`)) return;
    try {
      const res = await apiFetch(`/api/tasks/${row.id}`, { method: "DELETE" });
      if (!res) return;
      if (!res.ok) {
        showError(`Could not delete the task (${res.status}).`);
        return;
      }
      await loadTasks();
    } catch (err) {
      console.error("Failed to delete task", err);
      showError("Could not delete the task.");
    }
  });

  table.onAction("assign", (row) => assignTasks(row));
  table.onAction("approve", (row) => review(row, "approved"));
  table.onAction("reject", (row) => review(row, "rejected"));

  await loadLookups();
  await loadTasks();
}

export function unmount() {
  root = null;
  ctx = null;
  table = null;
  teamsById.clear();
  usersById.clear();
}

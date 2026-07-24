/**
 * Tasks: image upload + task table (tracker P3.2).
 *
 * Ported from `project_details.js` onto `js/components/data-table.js`. Two
 * things intentionally changed on the way over:
 *  - `prompt()` for bulk-assign is now a small modal, consistent with the rest
 *    of the shell (rule 12: modals toggle `.is-active`, not `style.display`).
 *  - The upload endpoint now reports per-file success/failure (P3.1), so the
 *    UI shows a summary instead of a single "Upload failed" alert.
 */
import { apiFetch } from "../../api.js?v=1";
import { escapeHTML, formatTime } from "../../utils.js?v=1";
import { createDataTable } from "../../components/data-table.js?v=1";

let root = null;
let ctx = null;
let table = null;
let teamMembers = [];

const ICON_EDIT = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/></svg>`;
const ICON_DELETE = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>`;

const STATUSES = ["New", "In Progress", "Completed", "Approved"];

function statusPill(status) {
  const s = status || "New";
  const cls = s === "Completed" ? "is-completed" : s === "In Progress" ? "is-progress" : s === "Approved" ? "is-approved" : "";
  return `<span class="pill ${cls}">${escapeHTML(s)}</span>`;
}

function countAnnotations(task) {
  let anns = task.annotations;
  if (typeof anns === "string") {
    try { anns = JSON.parse(anns); } catch { anns = []; }
  }
  if (!Array.isArray(anns)) return { comments: 0, classes: 0 };
  const comments = anns.filter((a) => a.type === "comment").length;
  const classes = new Set(anns.filter((a) => a.labelId).map((a) => a.labelId)).size;
  return { comments, classes };
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
      <button type="button" class="tool-button" id="bulkAssignBtn">Bulk assign</button>
      <button type="button" class="tool-button" id="bulkDeleteBtn" style="color:#e05260;border-color:rgba(224,82,96,.3);">Bulk delete</button>
    </div>

    <div class="mgmt-toolbar">
      <input type="search" id="searchInput" placeholder="Search filename…" aria-label="Search tasks">
      <select id="statusFilter" aria-label="Filter by status">
        <option value="All">All statuses</option>
        ${STATUSES.map((s) => `<option value="${s}">${s}</option>`).join("")}
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
              <span style="font-size:.85rem;color:var(--muted);">Assignee</span>
              <select id="editAssignee" style="padding:9px;border-radius:6px;border:1px solid var(--line);background:var(--panel);color:var(--ink);">
                <option value="">Unassigned</option>
              </select>
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
              <span style="font-size:.85rem;color:var(--muted);">Assignee</span>
              <select id="assignSelect" style="padding:9px;border-radius:6px;border:1px solid var(--line);background:var(--panel);color:var(--ink);">
                <option value="">Unassigned</option>
              </select>
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
  table.setRows(await res.json());
}

async function loadTeam() {
  try {
    const res = await apiFetch("/api/team");
    if (!res || !res.ok) return;
    teamMembers = await res.json();
    [el("editAssignee"), el("assignSelect")].forEach((select) => {
      teamMembers.forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m.name;
        opt.textContent = m.name;
        select.appendChild(opt);
      });
    });
  } catch (err) {
    console.error("Failed to load team", err);
  }
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
        body: JSON.stringify({ ids, assignee: el("assignSelect").value }),
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

// --- mount -------------------------------------------------------------

export async function mount(hostRoot, hostCtx) {
  root = hostRoot;
  ctx = hostCtx;
  root.innerHTML = template();

  table = createDataTable({
    mount: el("tableMount"),
    rowId: (r) => r.id,
    selectable: true,
    sortKey: "updated_at",
    sortDesc: true,
    emptyMessage: "No tasks yet. Upload images to get started.",
    onSelectionChange: updateBulkBar,
    matches: (row, q) => String(row.description || "").toLowerCase().includes(q),
    columns: [
      {
        key: "image_path",
        label: "",
        sortable: false,
        width: "56px",
        render: (r) => r.image_path
          ? `<img src="/${escapeHTML(String(r.image_path).replace(/\\/g, "/"))}" style="height:40px;border-radius:4px;border:1px solid var(--line);">`
          : "",
      },
      { key: "description", label: "Filename", render: (r) => `<a href="app.html?projectId=${encodeURIComponent(ctx.projectId)}&taskId=${encodeURIComponent(r.id)}" style="max-width:320px;display:inline-block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle;color:var(--accent);text-decoration:none;cursor:pointer;transition:color 0.2s ease;" onmouseover="this.style.color='var(--accent-dark)';this.style.textDecoration='underline'" onmouseout="this.style.color='var(--accent)';this.style.textDecoration='none'" title="${escapeHTML(r.description || '')}">${escapeHTML(r.description || "")}</a>` },
      { key: "assignee", label: "Assignee", render: (r) => r.assignee ? escapeHTML(r.assignee) : `<span style="color:var(--muted);">—</span>` },
      { key: "status", label: "Status", render: (r) => statusPill(r.status) },
      { key: "time_spent", label: "Time", render: (r) => r.time_spent ? `<span style="font-family:monospace;font-size:.85rem;">${formatTime(r.time_spent)}</span>` : `<span style="color:var(--muted);">—</span>` },
      {
        key: "updated_at", label: "Updated",
        render: (r) => {
          if (!r.updated_at) return `<span style="color:var(--muted);">—</span>`;
          const d = new Date(r.updated_at.endsWith("Z") ? r.updated_at : r.updated_at + "Z");
          return `<span style="font-size:.82rem;color:var(--muted);">${isNaN(d) ? escapeHTML(r.updated_at) : d.toLocaleString()}</span>`;
        },
      },
      { key: "classes", label: "Classes", sortable: false, align: "center", render: (r) => String(countAnnotations(r).classes) },
      { key: "comments", label: "Comments", sortable: false, align: "center", render: (r) => `💬 ${countAnnotations(r).comments}` },
      {
        key: "actions", label: "", sortable: false, align: "center",
        render: () => `<div class="row-actions">
            <button type="button" data-action="edit" title="Edit task">${ICON_EDIT}</button>
            <button type="button" data-action="delete" class="danger" title="Delete task">${ICON_DELETE}</button>
          </div>`,
      },
    ],
  });

  bindUpload();
  bindEditModal();
  bindBulkActions();

  el("searchInput").addEventListener("input", (e) => table.setQuery(e.target.value));
  el("statusFilter").addEventListener("change", (e) => table.setFilter("status", e.target.value));

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

  await loadTeam();
  await loadTasks();
}

export function unmount() {
  root = null;
  ctx = null;
  table = null;
}

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

let root = null;
let ctx = null;
let table = null;
let _pollTimer = null;

// How often to silently re-fetch the task list so that assignee/status changes
// made on another machine on the LAN appear without a manual refresh.
const POLL_INTERVAL_MS = 30_000;

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

function template(isCreator) {
  return `
    <div class="mgmt-title-row">
      <div>
        <p class="mgmt-eyebrow">Project</p>
        <h2>Tasks</h2>
      </div>
      ${isCreator ? `
      <div style="display:flex; gap:10px;">
        <button type="button" class="primary" id="uploadBtn" style="padding:9px 16px;border-radius:8px;font-weight:600;">+ Upload images</button>
        <input type="file" id="uploadInput" accept="image/png,image/jpeg,image/gif,image/webp" multiple style="display:none;">
      </div>` : ""}
    </div>

    ${isCreator ? `
    <div id="dropZone" class="mgmt-empty" style="border:2px dashed var(--line); border-radius:10px; margin-bottom:16px; cursor:pointer;">
      <p>Drag &amp; drop images here, or click "Upload images"</p>
    </div>` : ""}

    <div id="uploadSummary"></div>
    <div id="errorBanner" class="mgmt-error" style="display:none;"></div>

    ${isCreator ? `
    <div class="bulk-bar" id="bulkBar">
      <span class="count" id="bulkCount"></span>
      <button type="button" class="tool-button" id="bulkAssignBtn">Bulk assign</button>
      <button type="button" class="tool-button" id="bulkDeleteBtn" style="color:#e05260;border-color:rgba(224,82,96,.3);">Bulk delete</button>
    </div>` : ""}

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
              <span style="font-size:.85rem;color:var(--muted);">Assignee <span style="font-weight:400;font-style:italic;">(optional, advisory only)</span></span>
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
              <span style="font-size:.85rem;color:var(--muted);">Assignee <span style="font-weight:400;font-style:italic;">(optional, advisory only)</span></span>
              <select id="assignInput" style="padding:9px;border-radius:6px;border:1px solid var(--line);background:var(--panel);color:var(--ink);">
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

    <div class="modal-overlay" id="duplicateModal">
      <div class="modal-content" style="max-width:520px;">
        <div class="modal-header">
          <h2 style="display:flex; align-items:center; gap:8px; color:#e05260; font-size:1.15rem;">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"></path>
              <line x1="12" y1="9" x2="12" y2="13"></line>
              <line x1="12" y1="17" x2="12.01" y2="17"></line>
            </svg>
            Duplicate Images Detected
          </h2>
          <button class="modal-close" id="duplicateClose" type="button">&times;</button>
        </div>
        <div class="modal-body" style="display:grid; gap:14px; padding:18px 22px;">
          <p id="duplicateDesc" style="margin:0; font-size:.92rem; line-height:1.45; color:var(--ink);">
            The following image(s) already exist in this project or are duplicated in your selection:
          </p>
          <div id="duplicateList" style="max-height:160px; overflow-y:auto; border:1px solid var(--line); border-radius:8px; padding:8px 12px; background:var(--panel-alt, rgba(0,0,0,0.03)); display:grid; gap:6px;">
          </div>
          <div id="duplicateRemainingMsg" style="font-size:.88rem; color:var(--muted); margin-top:2px;"></div>
        </div>
        <div class="modal-footer" style="display:flex; gap:10px; justify-content:flex-end; padding:14px 22px; border-top:1px solid var(--line); flex-wrap:wrap;">
          <button type="button" class="tool-button" id="duplicateCancelBtn">Cancel Upload</button>
          <button type="button" class="tool-button" id="duplicateProceedBtn">Upload All Anyway</button>
          <button type="button" class="primary" id="duplicateSkipBtn" style="padding:8px 16px; border-radius:6px; font-weight:600;">Skip Duplicates &amp; Upload Remaining</button>
        </div>
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

async function fetchServerTasks(state) {
  table.showLoading(state.pageSize);
  const params = new URLSearchParams({
    projectId: ctx.projectId,
    limit: state.pageSize,
    offset: (state.page - 1) * state.pageSize,
  });
  if (state.query) params.set("search", state.query);
  if (state.sortKey) {
    params.set("sort_by", state.sortKey);
    params.set("sort_desc", state.sortDesc);
  }
  if (state.filters.status && state.filters.status !== "All") {
    params.set("status", state.filters.status);
  }
  
  const res = await apiFetch(`/api/tasks?${params.toString()}`);
  if (!res) {
    table.setServerData([], 0);
    return;
  }
  if (!res.ok) {
    showError(`Could not load tasks (${res.status}).`);
    table.setServerData([], 0);
    return;
  }
  clearError();
  const data = await res.json();
  table.setServerData(data.items, data.total);
  _refreshLockCache(data.items).then(() => table.render());
}

async function loadTasks() {
  if (table) table.reload();
}

// --- upload & duplicate handling ----------------------------------------

let pendingUploadFiles = null;
let pendingUniqueFiles = null;

function checkDuplicates(fileList) {
  const files = [...fileList];
  if (!files.length) return { duplicates: [], unique: [] };

  const existing = new Set((table?.rows || []).map((r) => (r.description || "").trim().toLowerCase()));
  const seenInBatch = new Set();
  const duplicates = [];
  const unique = [];

  for (const f of files) {
    const norm = (f.name || "").trim().toLowerCase();
    if (existing.has(norm)) {
      duplicates.push({ file: f, name: f.name, reason: "Already exists in project" });
    } else if (seenInBatch.has(norm)) {
      duplicates.push({ file: f, name: f.name, reason: "Duplicate in selection" });
    } else {
      unique.push(f);
      seenInBatch.add(norm);
    }
  }
  return { duplicates, unique };
}

function showDuplicateModal(files, duplicates, unique) {
  pendingUploadFiles = files;
  pendingUniqueFiles = unique;

  const modal = el("duplicateModal");
  const listEl = el("duplicateList");
  const descEl = el("duplicateDesc");
  const remainingEl = el("duplicateRemainingMsg");
  const skipBtn = el("duplicateSkipBtn");

  if (!modal || !listEl) return;

  descEl.innerHTML = `Found <strong>${duplicates.length}</strong> image${duplicates.length === 1 ? "" : "s"} that already exist in this project:`;
  listEl.innerHTML = duplicates
    .map(
      (d) => `
      <div style="display:flex; justify-content:space-between; align-items:center; font-size:.85rem; padding:4px 0; border-bottom:1px solid var(--line-light, rgba(0,0,0,0.05));">
        <span style="font-weight:500; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; max-width:300px;" title="${escapeHTML(d.name)}">
          📄 ${escapeHTML(d.name)}
        </span>
        <span class="pill" style="font-size:.75rem; background:rgba(224,82,96,0.12); color:#e05260; padding:2px 8px; border-radius:10px;">
          ${escapeHTML(d.reason)}
        </span>
      </div>`
    )
    .join("");

  if (unique.length > 0) {
    remainingEl.innerHTML = `✨ <strong>${unique.length}</strong> new unique image${unique.length === 1 ? "" : "s"} will be uploaded if you skip duplicates.`;
    skipBtn.style.display = "";
    skipBtn.textContent = `Skip Duplicates & Upload Remaining (${unique.length})`;
  } else {
    remainingEl.innerHTML = `⚠️ All selected images already exist in this project.`;
    skipBtn.style.display = "none";
  }

  modal.classList.add("is-active");
}

function hideDuplicateModal() {
  const modal = el("duplicateModal");
  if (modal) modal.classList.remove("is-active");
  pendingUploadFiles = null;
  pendingUniqueFiles = null;
}

function renderUploadSummary(body) {
  const summary = el("uploadSummary");
  if (!body) { summary.innerHTML = ""; return; }
  const parts = [];
  if (body.uploaded?.length) {
    parts.push(`<div class="mgmt-empty" style="text-align:left;padding:10px 14px;color:var(--accent-dark);">
        ✓ Uploaded ${body.uploaded.length} image${body.uploaded.length === 1 ? "" : "s"}.</div>`);
  }
  if (body.skipped?.length) {
    parts.push(`<div class="mgmt-empty" style="text-align:left;padding:10px 14px;color:var(--muted);">
        ℹ Skipped ${body.skipped.length} duplicate image${body.skipped.length === 1 ? "" : "s"}.</div>`);
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
  if (!btn || !input || !zone) return;

  const handleIncomingFiles = (fileList) => {
    if (!fileList || !fileList.length) return;
    const { duplicates, unique } = checkDuplicates(fileList);
    if (duplicates.length > 0) {
      showDuplicateModal(fileList, duplicates, unique);
    } else {
      uploadFiles(fileList);
    }
  };

  btn.addEventListener("click", () => input.click());
  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", (e) => {
    handleIncomingFiles(e.target.files);
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
    if (e.dataTransfer?.files?.length) handleIncomingFiles(e.dataTransfer.files);
  });

  // Duplicate modal actions
  const dupModal = el("duplicateModal");
  const dupClose = el("duplicateClose");
  const dupCancel = el("duplicateCancelBtn");
  const dupSkip = el("duplicateSkipBtn");
  const dupProceed = el("duplicateProceedBtn");

  if (dupClose) dupClose.addEventListener("click", hideDuplicateModal);
  if (dupCancel) dupCancel.addEventListener("click", hideDuplicateModal);
  if (dupSkip) {
    dupSkip.addEventListener("click", () => {
      const toUpload = pendingUniqueFiles;
      hideDuplicateModal();
      if (toUpload?.length) uploadFiles(toUpload);
    });
  }
  if (dupProceed) {
    dupProceed.addEventListener("click", () => {
      const toUpload = pendingUploadFiles;
      hideDuplicateModal();
      if (toUpload?.length) uploadFiles(toUpload);
    });
  }
  if (dupModal) {
    dupModal.addEventListener("click", (e) => {
      if (e.target === dupModal) hideDuplicateModal();
    });
  }
}

// --- edit modal ------------------------------------------------------------

async function loadTeamForTasks() {
  try {
    const res = await apiFetch("/api/team");
    if (!res || !res.ok) return;
    const team = await res.json();
    
    const byTeam = {};
    const unassigned = [];
    const projectTeamId = ctx?.project?.team_id;

    team.forEach((m) => {
      // If project belongs to a team, only allow members of that team
      if (projectTeamId != null) {
        const belongsToProjectTeam = m.teams && m.teams.some(t => t.id === projectTeamId);
        if (!belongsToProjectTeam) return;
      }
      
      if (m.teams && m.teams.length > 0) {
        // Group by the first team for the dropdown, to avoid duplicates
        const primaryTeamName = m.teams[0].name;
        if (!byTeam[primaryTeamName]) byTeam[primaryTeamName] = [];
        byTeam[primaryTeamName].push(m);
      } else {
        unassigned.push(m);
      }
    });
    
    const populate = (selectEl) => {
      selectEl.innerHTML = '<option value="">Unassigned</option>';
      for (const [teamName, members] of Object.entries(byTeam)) {
        const group = document.createElement("optgroup");
        group.label = teamName;
        members.forEach((m) => {
          const opt = document.createElement("option");
          opt.value = m.name;
          opt.textContent = m.name;
          group.appendChild(opt);
        });
        selectEl.appendChild(group);
      }
      if (unassigned.length > 0) {
        const group = document.createElement("optgroup");
        group.label = "Unassigned";
        unassigned.forEach((m) => {
          const opt = document.createElement("option");
          opt.value = m.name;
          opt.textContent = m.name;
          group.appendChild(opt);
        });
        selectEl.appendChild(group);
      }
    };
    
    populate(el("editAssignee"));
    populate(el("assignInput"));
  } catch (err) {
    console.error("Failed to load team", err);
  }
}

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
  if (!bar) return;
  bar.classList.toggle("is-active", selection.size > 0);
  const countEl = el("bulkCount");
  if (countEl) countEl.textContent = `${selection.size} selected`;
}

function bindBulkActions() {
  const deleteBtn = el("bulkDeleteBtn");
  const assignBtn = el("bulkAssignBtn");
  if (!deleteBtn || !assignBtn) return;

  deleteBtn.addEventListener("click", async () => {
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

  assignBtn.addEventListener("click", () => {
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

// --- mount -------------------------------------------------------------

export async function mount(hostRoot, hostCtx) {
  root = hostRoot;
  ctx = hostCtx;

  const datasetUsername = localStorage.getItem("dataset_username") || "";
  const isCreator = Boolean(ctx?.project?.creator && ctx.project.creator === datasetUsername);

  const urlParams = new URLSearchParams(window.location.search);
  const activeTaskId = urlParams.get("activeTaskId") ||
    sessionStorage.getItem(`last_active_task_${ctx.projectId}`) ||
    localStorage.getItem(`last_active_task_${ctx.projectId}`) ||
    null;

  root.innerHTML = template(isCreator);

  table = createDataTable({
    mount: el("tableMount"),
    rowId: (r) => r.id,
    selectable: isCreator,
    sortKey: "updated_at",
    sortDesc: true,
    priorityRowId: activeTaskId,
    rowClass: (r) => (activeTaskId && String(r.id) === String(activeTaskId) ? "row-recent-task" : ""),
    emptyMessage: "No tasks yet. Upload images to get started.",
    onSelectionChange: updateBulkBar,
    onFetchData: fetchServerTasks,
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
      {
        // T2.2 — show a "busy" badge when another annotator has the task open.
        key: "_lock", label: "", sortable: false, width: "56px",
        render: (r) => {
          const lock = _lockCache[r.id];
          if (!lock || !lock.locked) return "";
          return `<span title="In use by another annotator" style="font-size:.75rem;padding:2px 6px;border-radius:10px;background:rgba(239,68,68,.15);color:#ef4444;white-space:nowrap;">● busy</span>`;
        },
      },
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
        render: () => {
          if (!isCreator) return "";
          return `<div class="row-actions">
            <button type="button" data-action="edit" title="Edit task">${ICON_EDIT}</button>
            <button type="button" data-action="delete" class="danger" title="Delete task">${ICON_DELETE}</button>
          </div>`;
        },
      },
    ],
  });

    bindUpload();
    bindEditModal();
    bindBulkActions();

    el("searchInput").addEventListener("input", (e) => table.setQuery(e.target.value));
    el("statusFilter").addEventListener("change", (e) => table.setFilter("status", e.target.value));

    table.onAction("edit", (row) => {
      if (!isCreator) return;
      openEditModal(row);
    });
    table.onAction("delete", async (row) => {
      if (!isCreator) return;
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

    await Promise.all([loadTeamForTasks(), loadTasks()]);

  // Poll every 30 s so LAN peers see assignee/status changes promptly.
  _pollTimer = setInterval(async () => {
    try { await loadTasks(); } catch { /* best-effort */ }
  }, POLL_INTERVAL_MS);
}

export function unmount() {
  if (_pollTimer !== null) {
    clearInterval(_pollTimer);
    _pollTimer = null;
  }
  root = null;
  ctx = null;
  table = null;
}

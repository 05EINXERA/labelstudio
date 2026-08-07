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
 *
 * Upload is batched on the client (BATCH_SIZE files per request) so the task
 * table populates incrementally — new rows appear after each batch rather than
 * all at once at the end. An XHR progress event drives the progress bar and
 * the "X / N uploaded" counter. An abort button cancels mid-flight.
 */
import { apiFetch } from "../../api.js?v=1";
import { escapeHTML, formatTime } from "../../utils.js?v=1";
import { createDataTable } from "../../components/data-table.js?v=2";
import { fillTeamSelect } from "../../components/team-picker.js?v=1";
import { canManage, canReview } from "../../permissions.js?v=1";
import { openAssignDialog } from "./assign-modal.js?v=1";
import {
  STATUSES,
  buildColumns,
  statusSelectClass,
  showsBulkBar,
  showsSelection,
  showsUpload,
  statusPill,
} from "./task-columns.js?v=7";

let root = null;
let ctx = null;
let table = null;

// Active filter values — kept here so loadTasks() can re-apply derived-key
// filters (_assigneeFilter, _teamFilter) after setRows() replaces all rows.
const _activeFilters = {
  assignee: "All",
  team: "All",
  status: "All",
};

// T2.2 — lock status cache: {taskId: {locked: bool, locked_by: str}}
// Populated asynchronously after the task list renders.
//
// Mutated in place rather than reassigned: buildColumns() captured this exact
// object when the table was created, so replacing the binding would leave the
// renderer reading a detached snapshot and the badges would never update again.
const _lockCache = {};

async function _refreshLockCache() {
  // One request for the whole project instead of one per task. The old shape
  // issued 120 authenticated requests on a 120-task project — each a JWT
  // decode, a user lookup and a permission resolve — to read an in-process
  // dict. See .devnotes/server-optimization/03_TASKS_PAGE.md.
  //
  // Errors are silently swallowed: lock display is best-effort and must never
  // block the task list from rendering.
  try {
    const res = await apiFetch(
      `/api/tasks/lock-status?projectId=${encodeURIComponent(ctx.projectId)}`
    );
    if (!res || !res.ok) return;
    const locked = await res.json();

    // Clear before applying. The endpoint returns *only* locked tasks, so an
    // entry that has since been released or expired simply stops being sent —
    // merging would leave a stale "busy" badge on a task that is now free,
    // until a full page reload. The per-task endpoint used to overwrite every
    // id with {locked:false}, which hid this; the batch shape makes the clear
    // load-bearing.
    for (const key of Object.keys(_lockCache)) delete _lockCache[key];
    Object.assign(_lockCache, locked);
  } catch { /* best-effort */ }
}

// ---------------------------------------------------------------------------
// How many files to include in one multipart POST. Each batch becomes one
// server-side commit and one loadTasks() refresh, so the table fills
// incrementally. Keep this well below MAX_UPLOAD_FILES (server default 200).
// ---------------------------------------------------------------------------
const BATCH_SIZE = 15;

// CSRF cookie name — must match api/auth.py.
const CSRF_COOKIE = "csrf_token";

function _readCookie(name) {
  const m = document.cookie.match(new RegExp("(?:^|;\\s*)" + name + "=([^;]*)"));
  return m ? decodeURIComponent(m[1]) : null;
}

// ---------------------------------------------------------------------------
// Upload one batch via XHR so we can attach an upload-progress listener.
// Returns { uploaded, failed } on success, or throws on network/HTTP error.
// `onProgress(loaded, total)` is called as bytes are sent.
// `signal` is an AbortSignal; when aborted the XHR is cancelled.
// ---------------------------------------------------------------------------
function _uploadBatch(projectId, files, onProgress, signal) {
  return new Promise((resolve, reject) => {
    const formData = new FormData();
    files.forEach((f) => formData.append("file", f));

    const xhr = new XMLHttpRequest();

    // Wire the abort signal.
    const onAbort = () => xhr.abort();
    if (signal) signal.addEventListener("abort", onAbort, { once: true });

    xhr.upload.addEventListener("progress", (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded, e.total);
    });

    xhr.addEventListener("load", () => {
      if (signal) signal.removeEventListener("abort", onAbort);
      if (xhr.status === 401) {
        localStorage.removeItem("logged_in");
        window.location.href = "/";
        reject(new Error("Session expired"));
        return;
      }
      if (!xhr.responseText) { reject(new Error(`HTTP ${xhr.status}`)); return; }
      let body;
      try { body = JSON.parse(xhr.responseText); } catch (e) {
        reject(new Error("Invalid server response")); return;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body);
      } else {
        reject(new Error(body?.detail || `HTTP ${xhr.status}`));
      }
    });

    xhr.addEventListener("error", () => {
      if (signal) signal.removeEventListener("abort", onAbort);
      reject(new Error("Network error"));
    });
    xhr.addEventListener("abort", () => {
      if (signal) signal.removeEventListener("abort", onAbort);
      reject(new DOMException("Upload cancelled", "AbortError"));
    });

    xhr.open("POST", `/api/projects/${encodeURIComponent(projectId)}/upload`);
    const csrf = _readCookie(CSRF_COOKIE);
    if (csrf) xhr.setRequestHeader("X-CSRF-Token", csrf);
    xhr.send(formData);
  });
}

// ---------------------------------------------------------------------------
// Progress UI helpers
// ---------------------------------------------------------------------------

// Current AbortController for the running upload, null when idle.
let _uploadAbortCtrl = null;

function _showProgress() {
  el("uploadProgress").style.display = "";
  el("uploadBtn").disabled = true;
  el("uploadInput").disabled = true;
  const zone = el("dropZone");
  if (zone) zone.style.pointerEvents = "none";
}

function _hideProgress() {
  el("uploadProgress").style.display = "none";
  el("uploadBtn").disabled = false;
  el("uploadInput").disabled = false;
  const zone = el("dropZone");
  if (zone) zone.style.pointerEvents = "";
  _uploadAbortCtrl = null;
}

/**
 * Update the progress bar and counters.
 *
 * @param {number} filesUploaded  - how many files have fully landed on the server
 * @param {number} filesTotal     - total files selected
 * @param {number} bytesLoaded    - bytes sent in the current batch so far
 * @param {number} bytesInBatch   - total bytes of the current batch
 * @param {number} bytesDonePrev  - bytes from all completed batches
 * @param {number} bytesTotal     - sum of all file sizes
 */
function _updateProgress(filesUploaded, filesTotal, bytesLoaded, bytesInBatch, bytesDonePrev, bytesTotal) {
  const bar = el("uploadProgressFill");
  const label = el("uploadProgressLabel");
  const pct = el("uploadProgressPct");

  const bytesDone = bytesDonePrev + bytesLoaded;
  const fraction = bytesTotal > 0 ? Math.min(bytesDone / bytesTotal, 1) : 0;
  const pctVal = Math.round(fraction * 100);

  bar.style.width = `${pctVal}%`;
  label.textContent = `Uploading ${filesUploaded} / ${filesTotal} images…`;
  pct.textContent = `${pctVal}%`;
}

function _markDone(filesUploaded, filesTotal) {
  el("uploadProgressFill").style.width = "100%";
  el("uploadProgressLabel").textContent = `${filesUploaded} / ${filesTotal} uploaded`;
  el("uploadProgressPct").textContent = "100%";
}

function template() {
  return `
    <div class="mgmt-title-row">
      <div>
        <p class="mgmt-eyebrow">Project</p>
        <h2>Tasks</h2>
      </div>
      <div style="display:flex; gap:10px; align-items:center;">
        <button type="button" class="primary" id="uploadBtn" style="padding:9px 16px;border-radius:8px;font-weight:600;">+ Upload images</button>
        <input type="file" id="uploadInput" accept="image/png,image/jpeg,image/gif,image/webp" multiple style="display:none;">
      </div>
    </div>

    <div id="dropZone" class="mgmt-empty" style="border:2px dashed var(--line); border-radius:10px; margin-bottom:16px; cursor:pointer;">
      <p>Drag &amp; drop images here, or click "Upload images"</p>
    </div>

    <div id="uploadProgress" class="upload-progress-box" style="display:none;" aria-live="polite" aria-label="Upload progress">
      <div class="upload-progress-header">
        <span class="upload-progress-label" id="uploadProgressLabel">Preparing…</span>
        <span class="upload-progress-pct" id="uploadProgressPct">0%</span>
      </div>
      <div class="upload-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0" id="uploadProgressBar">
        <div class="upload-progress-fill" id="uploadProgressFill"></div>
      </div>
      <div style="display:flex; justify-content:flex-end; margin-top:4px;">
        <button type="button" class="upload-abort-btn" id="uploadAbortBtn">Cancel upload</button>
      </div>
    </div>

    <div id="uploadSummary"></div>
    <div id="errorBanner" class="mgmt-error" style="display:none;"></div>

    <div class="bulk-bar" id="bulkBar">
      <span class="count" id="bulkCount"></span>
      <button type="button" class="tool-button" id="bulkTeamBtn">Assign…</button>
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
  // include_annotations=false: this view never reads annotation *contents*, only
  // the Classes and Comments counts, which the server now returns as two
  // integers per row (class_count / comment_count). Fetching the blobs meant
  // ~8.9 MB on a 120-task project to render two columns — the same mistake the
  // canvas page already avoids (CLAUDE.md rule 17).
  // See .devnotes/server-optimization/03_TASKS_PAGE.md.
  const res = await apiFetch(
    `/api/tasks?projectId=${encodeURIComponent(ctx.projectId)}&include_annotations=false`
  );
  if (!res) return;
  if (!res.ok) {
    showError(`Could not load tasks (${res.status}).`);
    return;
  }
  clearError();
  const tasks = await res.json();
  table.setRows(tasks);

  // Re-apply derived-key filters. setRows() replaces all row objects, so the
  // _assigneeFilter and _teamFilter keys written onto rows by applyAssigneeFilter /
  // applyTeamFilter are gone. Without this, an active filter makes the table
  // appear empty after every reload.
  _reapplyFilters();

  // T2.2 — refresh lock badges after the table renders (async, non-blocking).
  _refreshLockCache().then(() => {
    table.setRows(tasks);
    _reapplyFilters();
  });
}

// --- upload --------------------------------------------------------------

// Above this many stacked names, show a count instead of listing every one —
// a batch of a few hundred duplicate filenames would otherwise render a
// scroll-length list.
const DUPLICATE_LIST_LIMIT = 8;
// How long the upload summary (success/failed/duplicate) stays on screen
// before it clears itself, so a stale banner doesn't linger over the table.
const UPLOAD_SUMMARY_DURATION_MS = 8000;

let _uploadSummaryTimer = null;

// Results are accumulated across every batch of a single upload (see
// uploadFiles), so this takes flat arrays rather than one server response
// body: a 200-file selection is 8 POSTs but must render as one summary.
function _renderUploadSummary(allUploaded, allFailed, allDuplicates, aborted) {
  const summary = el("uploadSummary");
  if (_uploadSummaryTimer) {
    clearTimeout(_uploadSummaryTimer);
    _uploadSummaryTimer = null;
  }
  const parts = [];

  if (aborted) {
    parts.push(`<div class="mgmt-error" style="margin-bottom:0;">
      Upload cancelled. ${allUploaded.length} image${allUploaded.length === 1 ? "" : "s"} uploaded before cancellation.
    </div>`);
  } else if (allUploaded.length) {
    parts.push(`<div class="mgmt-empty" style="text-align:left;padding:10px 14px;color:var(--accent-dark);">
      ✓ Uploaded ${allUploaded.length} image${allUploaded.length === 1 ? "" : "s"}.
    </div>`);
  }
  if (allDuplicates.length) {
    const n = allDuplicates.length;
    const listable = n <= DUPLICATE_LIST_LIMIT;
    parts.push(`<div class="mgmt-error">
        ${n} file${n === 1 ? "" : "s"} skipped — a task with that name already exists:
        ${listable
        ? `<ul style="margin:6px 0 0 18px;">
              ${allDuplicates.map((name) => `<li>${escapeHTML(name || "unknown")}</li>`).join("")}
            </ul>`
        : ""}
      </div>`);
  }

  if (allFailed.length) {
    parts.push(`<div class="mgmt-error">
      ${allFailed.length} file${allFailed.length === 1 ? "" : "s"} could not be uploaded:
      <ul style="margin:6px 0 0 18px;">
        ${allFailed.map((f) => `<li>${escapeHTML(f.filename || "unknown")} — ${escapeHTML(f.error)}</li>`).join("")}
      </ul>
    </div>`);
  }

  summary.innerHTML = parts.join("");
  if (parts.length) {
    _uploadSummaryTimer = setTimeout(() => {
      summary.innerHTML = "";
      _uploadSummaryTimer = null;
    }, UPLOAD_SUMMARY_DURATION_MS);
  }
}

/**
 * Batched upload with live progress.
 *
 * Files are split into BATCH_SIZE chunks. Each chunk is a separate multipart
 * POST so the server commits rows incrementally. After every batch we call
 * loadTasks() to update the table immediately — users see new rows appear
 * as each chunk lands, rather than waiting for all files to finish.
 *
 * Progress is driven by XHR upload events (fetch has no progress API).
 * The progress bar reflects both bytes-sent within the current batch and
 * bytes from all previously completed batches.
 */
async function uploadFiles(fileList) {
  const files = [...fileList];
  if (!files.length) return;

  // Pre-compute total bytes for the progress bar denominator.
  const bytesTotal = files.reduce((sum, f) => sum + f.size, 0);

  // Split into batches.
  const batches = [];
  for (let i = 0; i < files.length; i += BATCH_SIZE) {
    batches.push(files.slice(i, i + BATCH_SIZE));
  }

  // Set up the abort controller.
  _uploadAbortCtrl = new AbortController();
  const { signal } = _uploadAbortCtrl;

  // Show the progress UI and clear stale state.
  _showProgress();
  el("uploadSummary").innerHTML = "";
  clearError();
  _updateProgress(0, files.length, 0, 0, 0, bytesTotal);

  const allUploaded = [];
  const allFailed = [];
  const allDuplicates = [];
  let bytesDonePrev = 0;  // bytes from fully completed batches
  let filesUploaded = 0;
  let aborted = false;

  try {
    for (const batch of batches) {
      const batchBytes = batch.reduce((sum, f) => sum + f.size, 0);

      let body;
      try {
        body = await _uploadBatch(
          ctx.projectId,
          batch,
          (loaded, _total) => {
            _updateProgress(filesUploaded, files.length, loaded, batchBytes, bytesDonePrev, bytesTotal);
            // Keep the ARIA progressbar attribute in sync.
            const pctVal = bytesTotal > 0
              ? Math.round(Math.min((bytesDonePrev + loaded) / bytesTotal, 1) * 100)
              : 0;
            el("uploadProgressBar").setAttribute("aria-valuenow", pctVal);
          },
          signal,
        );
      } catch (err) {
        if (err.name === "AbortError") {
          aborted = true;
          break;
        }
        // A single batch HTTP error (e.g. 413 count limit) — report it and
        // continue; remaining batches might still succeed.
        allFailed.push(...batch.map((f) => ({ filename: f.name, error: err.message })));
        bytesDonePrev += batchBytes;
        continue;
      }

      // Merge per-file results.
      allUploaded.push(...(body.uploaded || []));
      allFailed.push(...(body.failed || []));
      allDuplicates.push(...(body.duplicates || []));
      filesUploaded += (body.uploaded || []).length;
      bytesDonePrev += batchBytes;

      // Refresh the table immediately so new rows appear.
      await loadTasks();

      // Update the counter to reflect confirmed server-side uploads.
      _updateProgress(filesUploaded, files.length, 0, batchBytes, bytesDonePrev, bytesTotal);
    }
  } finally {
    _markDone(filesUploaded, files.length);
    // Brief pause so the user sees "100%" before the bar disappears.
    await new Promise((r) => setTimeout(r, 600));
    _hideProgress();
    _renderUploadSummary(allUploaded, allFailed, allDuplicates, aborted);
    // Ensure the table is up to date after an abort (partial batches may have landed).
    if (aborted) await loadTasks();
  }
}

function bindUpload() {
  const btn = el("uploadBtn");
  const input = el("uploadInput");
  const zone = el("dropZone");
  const abort = el("uploadAbortBtn");

  btn.addEventListener("click", () => input.click());
  zone.addEventListener("click", () => input.click());
  input.addEventListener("change", (e) => {
    uploadFiles(e.target.files);
    input.value = "";
  });

  abort.addEventListener("click", () => {
    if (_uploadAbortCtrl) _uploadAbortCtrl.abort();
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
  if (!canManage(ctx.myRole)) {
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
}

// --- teams, reviewers and filters ------------------------------------------

const teamsById = new Map();
const usersById = new Map();
let assignableMembers = [];

async function loadLookups() {
  const res = await apiFetch(`/api/projects/${encodeURIComponent(ctx.projectId)}/grants`);
  if (res?.ok) {
    for (const grant of await res.json()) {
      teamsById.set(grant.team_id, grant.team_name);
    }
  }

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

function _reapplyFilters() {
  if (_activeFilters.assignee !== "All") applyAssigneeFilter(_activeFilters.assignee);
  if (_activeFilters.team !== "All") applyTeamFilter(_activeFilters.team);
}

function applyAssigneeFilter(value) {
  _activeFilters.assignee = value;
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
  _activeFilters.team = value;
  const rows = table.getRows();
  for (const row of rows) {
    row._teamFilter = row.assigned_team_id == null ? "unassigned" : String(row.assigned_team_id);
  }
  table.setFilter("_teamFilter", value === "All" ? "All" : value);
}

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
    current: row ? { teamId: row.assigned_team_id, userId: row.assignee_user_id } : {},
  });
  if (!choice) return;

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
  let note = null;
  if (action === "rejected") {
    note = prompt(`Why is "${row.description}" being sent back?`);
    if (note == null) return;
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
      currentUser: ctx.currentUser,
    }),
  });

  if (!showsUpload(role)) {
    el("uploadBtn")?.remove();
    el("dropZone")?.remove();
    el("uploadProgress")?.remove();
  } else {
    bindUpload();
  }

  if (canManage(role)) bindEditModal();

  if (showsBulkBar(role)) bindBulkActions();
  else el("bulkBar")?.remove();

  el("searchInput").addEventListener("input", (e) => table.setQuery(e.target.value));
  el("statusFilter").addEventListener("change", (e) => {
    _activeFilters.status = e.target.value;
    table.setFilter("status", e.target.value);
  });
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

  // Inline status selects — rendered by task-columns.js for assignees and
  // reviewers. Delegated on the table mount div so re-renders keep it alive.
  el("tableMount").addEventListener("change", async (e) => {
    const sel = e.target.closest("select.status-select");
    if (!sel) return;
    const taskId = sel.dataset.taskId;
    const newStatus = sel.value;
    const originalStatus = sel.dataset.original;
    if (!taskId || !newStatus || newStatus === originalStatus) return;

    // Optimistic colour update so the user sees instant feedback.
    sel.className = `status-select ${statusSelectClass(newStatus)}`;

    const isReviewStatus = newStatus === "Approved" || newStatus === "Rejected";

    let res;
    if (isReviewStatus) {
      let note = null;
      if (newStatus === "Rejected") {
        note = prompt(`Why is this task being sent back?`);
        if (note == null) { sel.value = originalStatus; return; }
      }
      const action = newStatus === "Approved" ? "approved" : "rejected";
      res = await apiFetch(`/api/tasks/${encodeURIComponent(taskId)}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, note: note || null }),
      });
    } else {
      res = await apiFetch(`/api/tasks/${encodeURIComponent(taskId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status: newStatus }),
      });
    }

    if (!res || !res.ok) {
      const body = await res?.json().catch(() => null);
      showError(body?.detail || `Could not update status (${res?.status}).`);
      sel.value = originalStatus;
      sel.className = `status-select ${statusSelectClass(originalStatus)}`;
      return;
    }

    clearError();
    sel.dataset.original = newStatus;
    sel.className = `status-select ${statusSelectClass(newStatus)}`;
    await loadTasks();
  });

  await loadLookups();
  await loadTasks();
}

export function unmount() {
  // If an upload is in flight when the user navigates away, abort it cleanly.
  if (_uploadAbortCtrl) {
    _uploadAbortCtrl.abort();
    _uploadAbortCtrl = null;
  }
  root = null;
  ctx = null;
  table = null;
  teamsById.clear();
  usersById.clear();
}

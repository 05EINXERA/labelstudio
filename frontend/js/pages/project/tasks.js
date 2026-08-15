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
import { apiFetch } from "../../api.js?v=3";
import { escapeHTML, formatTime, clientId } from "../../utils.js?v=1";
import { createDataTable } from "../../components/data-table.js?v=3";
import { fillTeamSelect } from "../../components/team-picker.js?v=1";
import { canManage, canReview } from "../../permissions.js?v=1";
import { openAssignDialog } from "./assign-modal.js?v=2";
import { matchAssignees, isSearchFilterValue } from "./assignee-search.js?v=1";
import { APPROVED_STATUSES, isReviewStatus } from "../../task-status.js?v=1";
import {
  STATUSES,
  buildColumns,
  statusSelectClass,
  showsBulkBar,
  showsSelection,
  showsUpload,
  statusPill,
} from "./task-columns.js?v=9";

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

// Rows per page. Must stay <= the server's MAX_PAGE_SIZE (100), which rejects
// anything larger rather than silently clamping.
const PAGE_SIZE = 10;

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
      <!-- Approving a week's work is the batch workflow's whole point, so it
           needs to be one action over a selection rather than N clicks. The
           select names the batch; the server re-checks the reviewer role on
           every id (E-11). -->
      <select class="tool-button" id="bulkApproveSelect" aria-label="Approve selected tasks as">
        <option value="">Approve as…</option>
        ${APPROVED_STATUSES.map((s) => `<option value="${escapeHTML(s)}">${escapeHTML(s)}</option>`).join("")}
      </select>
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
      <input type="search" id="assigneeSearch" class="assignee-search-input"
             placeholder="Assignee name…" aria-label="Search by assignee name"
             autocomplete="off">
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

// Column key -> server sort field. The server whitelists sort fields
// (api/routers/tasks.py `_SORT_COLUMNS`), so a column that is sortable in the
// UI but unknown there would 422. Anything absent here falls back to the
// filename, which is the default order.
const SERVER_SORT_KEYS = {
  description: "description",
  status: "status",
  updated_at: "updated_at",
  time_spent: "time_spent",
  id: "id",
};

/**
 * Fetch one page of tasks.
 *
 * Passed to the table as `server.fetchPage`; the table owns page/sort/filter
 * state and calls this whenever any of it changes.
 *
 * include_annotations=false: this view never reads annotation *contents*, only
 * the Classes and Comments counts, which the server returns as two integers per
 * row (class_count / comment_count). Fetching the blobs meant ~8.9 MB on a
 * 120-task project to render two columns — the same mistake the canvas page
 * already avoids (CLAUDE.md rule 17).
 * See .devnotes/server-optimization/03_TASKS_PAGE.md.
 */
async function fetchTaskPage({ page, pageSize, sortKey, sortDesc, query, filters }) {
  const params = new URLSearchParams({
    projectId: ctx.projectId,
    include_annotations: "false",
    page: String(page),
    page_size: String(pageSize),
    sort: SERVER_SORT_KEYS[sortKey] || "description",
    order: sortDesc ? "desc" : "asc",
  });
  // Filtering and search run in SQL now. With only one page in memory the
  // client could otherwise search 10 rows out of thousands and report "no
  // matches" for a task that exists on page 30.
  if (query) params.set("q", query);
  for (const [key, value] of [
    ["status", filters.status],
    ["team", filters.team],
    ["assignee", filters.assignee],
  ]) {
    if (value && value !== "All") params.set(key, value);
  }

  const res = await apiFetch(`/api/tasks?${params.toString()}`);
  if (!res) return null;                     // apiFetch handled a 401 redirect
  if (!res.ok) throw new Error(`Could not load tasks (${res.status}).`);
  clearError();
  return res.json();
}

/** Reload the current page in place (after an edit, delete or upload). */
async function loadTasks() {
  await table.load();

  // T2.2 — refresh lock badges after the table renders (async, non-blocking).
  // Only the rendered page's locks matter, and the badge renderer reads
  // `_lockCache` by task id, so a re-render is all that is needed.
  _refreshLockCache().then(() => table.render());
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
      // Same identity requirement as the inline status dropdown: a PATCH with
      // no client_id could not prove it was not a stranger's write, so every
      // edit came back 409.
      const editRow = table.getRows?.().find((r) => String(r.id) === String(id));
      const res = await apiFetch(`/api/tasks/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          description: el("editDescription").value,
          status: el("editStatus").value,
          client_id: clientId(),
          updated_at: editRow?.updated_at ?? null,
        }),
      });
      if (!res) return;
      if (res.status === 409) {
        showError(
          "Someone else changed this task while the form was open. " +
          "The list has been refreshed — please reopen it and try again."
        );
        closeEditModal();
        await loadTasks();
        return;
      }
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
  // Gated per control, not per bar: approving is the *reviewer's* job, while
  // assigning and deleting are administrative. A single canManage() gate over
  // the whole bar would have hidden bulk approval from exactly the role that
  // needs it.
  if (canReview(ctx.myRole)) {
    bindBulkApprove();
  } else {
    el("bulkApproveSelect")?.remove();
  }

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

/**
 * Bulk approval into a chosen batch.
 *
 * Uses POST /api/tasks/bulk-update rather than looping the review endpoint: one
 * request for a week's work instead of hundreds, and the server applies the
 * reviewer gate to the whole set (E-11 — bulk-update is *not* a way around the
 * per-task check).
 *
 * The trade-off is deliberate and worth naming: bulk-update writes the status
 * without writing a TaskReview row, so a bulk approval is not itemised in each
 * task's review history the way a single approve is. The batch status itself
 * records the verdict, and the alternative — N review requests through the
 * single-worker process (rule 9) — would tie up the box for minutes.
 */
function bindBulkApprove() {
  const select = el("bulkApproveSelect");
  if (!select) return;

  select.addEventListener("change", async () => {
    const status = select.value;
    // Always reset: the select is an action trigger, not a persistent setting,
    // and leaving it showing "Verified" would suggest the selection still is.
    select.value = "";
    if (!status) return;

    const ids = [...table.getSelection()];
    if (!ids.length) return;
    if (!confirm(`Mark ${ids.length} task${ids.length === 1 ? "" : "s"} as ${status}?`)) return;

    try {
      const res = await apiFetch("/api/tasks/bulk-update", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids, status }),
      });
      if (!res) return;
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        showError(body?.detail || `Could not approve the selected tasks (${res.status}).`);
        return;
      }
      // `skipped` counts ids the caller could not act on — surfaced rather than
      // swallowed, so "I approved 200" never quietly means 180.
      const result = await res.json().catch(() => null);
      if (result?.skipped) {
        showError(`${result.updated} task${result.updated === 1 ? "" : "s"} marked ${status}; ` +
                  `${result.skipped} skipped (you may not have permission on those).`);
      } else {
        clearError();
      }
      table.clearSelection();
      await loadTasks();
    } catch (err) {
      console.error("Bulk approve failed", err);
      showError("Could not approve the selected tasks.");
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

// --- assignee name search ---------------------------------------------------
//
// A search box sitting beside the assignee select, always visible — the same
// shape as the filename search at the head of the toolbar. Typing resolves the
// name against the roster already in `assignableMembers` (see
// assignee-search.js) and drives the *same* `assignee` filter the dropdown does
// — so search, the dropdown, the status/team filters and the sort all compose
// through one server query rather than fighting each other.
//
// Text wins over the dropdown while it is non-empty: two live assignee filters
// would be ambiguous, and the select is visibly disabled to say which one is in
// charge. Clearing the box hands control back.

// Matching is local (no request), so this only coalesces renders — the 300ms
// used for the filename search would be needless lag here.
const ASSIGNEE_SEARCH_DEBOUNCE_MS = 150;
let _assigneeSearchTimer = null;

/** Push the current assignee filter — from the search box if it has text,
 *  otherwise from the dropdown — into the table. */
function _applyAssigneeFilter() {
  const box = el("assigneeSearch");
  const select = el("assigneeFilter");
  if (!box || !select) return;

  const searched = matchAssignees(assignableMembers, box.value);
  const value = searched ?? select.value ?? "All";

  select.disabled = searched != null;

  _activeFilters.assignee = value;
  table.setFilter("assignee", value);
}

/** Enable the box once the roster it matches against has arrived.
 *
 *  Until then typing would match an empty list and send NO_MATCH — an empty
 *  table for a name that does exist. Disabled is the honest state: mount()
 *  binds this before `await loadLookups()`, and on a slow LAN that gap is
 *  visible. */
function _setAssigneeSearchReady(ready) {
  const box = el("assigneeSearch");
  if (box) box.disabled = !ready;
}

function bindAssigneeSearch() {
  const box = el("assigneeSearch");
  if (!box) return;

  _setAssigneeSearchReady(false);

  box.addEventListener("input", () => {
    if (_assigneeSearchTimer) clearTimeout(_assigneeSearchTimer);
    _assigneeSearchTimer = setTimeout(() => {
      _assigneeSearchTimer = null;
      _applyAssigneeFilter();
    }, ASSIGNEE_SEARCH_DEBOUNCE_MS);
  });

  // Escape clears rather than collapses. The box stays put; only the filter goes.
  box.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !box.value) return;
    box.value = "";
    _applyAssigneeFilter();
  });
}

// `applyAssigneeFilter` / `applyTeamFilter` / `_reapplyFilters` lived here.
// They stamped a derived `_assigneeFilter` / `_teamFilter` key onto every row
// so the client-side table could filter on it, and had to be re-applied after
// every setRows() because those keys did not survive a reload. All three are
// gone: the server filters now, and the sentinel values the selects emit
// ("unassigned", "mine", "user-<id>") are understood by it directly.

// --- view state in the URL ----------------------------------------------
//
// The page number lives in the hash (`#/tasks?page=4`) so the browser Back
// button returns an annotator to the page they opened an image from, rather
// than dumping them on page 1 — the single most-reported annoyance with the
// old table. Refresh and shared links get the same behaviour for free.

/** Serialise the table's state into the hash, without adding a history entry. */
function writeViewStateToHash(view) {
  const params = new URLSearchParams();
  if (view.page > 1) params.set("page", String(view.page));
  // Only non-default sort is recorded, so the common case stays a clean URL.
  if (view.sortKey && view.sortKey !== "description") params.set("sort", view.sortKey);
  if (view.sortDesc) params.set("order", "desc");
  if (view.query) params.set("q", view.query);
  for (const key of ["status", "team", "assignee"]) {
    const value = view.filters?.[key];
    if (value && value !== "All") params.set(key, value);
  }

  const qs = params.toString();
  const hash = `#/tasks${qs ? `?${qs}` : ""}`;
  if (window.location.hash === hash) return;
  // replaceState, not pushState: paging is not a navigation the user wants to
  // walk back through one page at a time. Back should leave the tasks view,
  // and returning from the canvas should restore whatever page was last shown.
  history.replaceState(history.state, "", window.location.pathname + window.location.search + hash);
}

// Key marking "the canvas was opened from this tab's tasks page", read by the
// workspace's back arrow. See CAME_FROM_TASKS_KEY in init.js — the two must
// agree, and there is no build step to share one constant.
//
// sessionStorage rather than history.state: the canvas is a separate document,
// and history.state does not travel across that navigation. Per-tab, so a
// canvas opened directly in a second tab is correctly treated as having no
// tasks page to return to.
const CAME_FROM_TASKS_KEY = "tasks_nav_origin";

/**
 * Mark that the annotator is leaving for the canvas via a task link.
 *
 * Lets the workspace's back arrow step *back* through history instead of
 * pushing a third entry. Without it the stack grows tasks → canvas → tasks, and
 * the browser Back button returns to the canvas — a loop the annotator cannot
 * escape without repeatedly pressing Back
 * (.devnotes/tasks-pagination/PLAN.md § 8).
 */
function markCanvasNavigation(e) {
  const link = e.target.closest?.("a.task-filename");
  if (!link) return;
  // Modifier-clicks and middle-clicks open a new tab; that tab has no history
  // to go back through, so it must not be told otherwise.
  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
  try {
    sessionStorage.setItem(CAME_FROM_TASKS_KEY, String(Date.now()));
  } catch { /* private mode: the back arrow just falls back to its href */ }
}

/** Read the view state the router parsed off the hash. */
function readViewStateFromHash(params) {
  const page = parseInt(params?.get("page") ?? "", 10);
  return {
    page: Number.isFinite(page) && page >= 1 ? page : 1,
    sortKey: params?.get("sort") || "description",
    sortDesc: params?.get("order") === "desc",
    query: params?.get("q") || "",
    status: params?.get("status") || "All",
    team: params?.get("team") || "All",
    assignee: params?.get("assignee") || "All",
  };
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

export async function mount(hostRoot, hostCtx, hashParams) {
  root = hostRoot;
  ctx = hostCtx;
  root.innerHTML = template();

  const role = ctx.myRole;
  const view = readViewStateFromHash(hashParams);
  _activeFilters.status = view.status;
  _activeFilters.team = view.team;
  _activeFilters.assignee = view.assignee;

  table = createDataTable({
    mount: el("tableMount"),
    rowId: (r) => r.id,
    selectable: showsSelection(role),
    // Filename ascending, by default and without the user clicking a header.
    // Was `updated_at` descending, which floated whichever task was last saved
    // to the front and made the canvas's prev/next wander
    // (.devnotes/tasks-pagination/PLAN.md § 2.1).
    sortKey: "description",
    sortDesc: false,
    pageSize: PAGE_SIZE,
    emptyMessage: "No tasks yet. Upload images to get started.",
    onSelectionChange: updateBulkBar,
    // No `matches`: search is a server query now, not a client predicate.
    server: { fetchPage: fetchTaskPage },
    onStateChange: writeViewStateToHash,
    columns: buildColumns({
      role,
      projectId: ctx.projectId,
      teamsById,
      usersById,
      lockCache: _lockCache,
      currentUser: ctx.currentUser,
      // Read lazily: columns are built once here, but the sort and filters
      // they encode into each canvas link change as the user works.
      viewState: () => (table ? table.getState() : null),
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

  // All four narrow the query server-side now. The team/assignee selects keep
  // emitting the same sentinel values they always did ("unassigned", "mine",
  // "user-<id>"); the server understands that vocabulary directly, so no
  // translation is needed here and the derived per-row `_teamFilter` /
  // `_assigneeFilter` keys are gone.
  el("searchInput").addEventListener("input", (e) => table.setQuery(e.target.value));
  el("statusFilter").addEventListener("change", (e) => {
    _activeFilters.status = e.target.value;
    table.setFilter("status", e.target.value);
  });
  el("teamFilter")?.addEventListener("change", (e) => {
    _activeFilters.team = e.target.value;
    table.setFilter("team", e.target.value);
  });
  // Goes through _applyAssigneeFilter() rather than setting the filter
  // directly, so the search box keeps precedence if it holds text.
  el("assigneeFilter")?.addEventListener("change", () => _applyAssigneeFilter());
  bindAssigneeSearch();

  // Delegated on the mount, which survives every re-render; the filename links
  // themselves are replaced on each page change.
  el("tableMount").addEventListener("click", markCanvasNavigation);

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
  // The one-click ✓ approves into the default batch. Approving into a specific
  // batch (Verified, Checked, …) is done from the row's status dropdown, or in
  // bulk from the toolbar — a per-row button per batch would crowd the row for
  // an action that is naturally taken over a whole week's work at once.
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

    // Every reviewer verdict — a rejection, or an approval under any batch
    // status — goes through the review endpoint rather than PATCH, so it is
    // recorded in the TaskReview audit log with the actor. Routing only
    // "Approved" there would have left a whole batch of approvals with no trail
    // of who signed them off.
    let res;
    if (isReviewStatus(newStatus)) {
      let note = null;
      if (newStatus === "Rejected") {
        note = prompt(`Why is this task being sent back?`);
        if (note == null) { sel.value = originalStatus; return; }
      }
      // The review verb is the status lowercased (see REVIEW_ACTION_STATUS).
      const action = newStatus.toLowerCase();
      res = await apiFetch(`/api/tasks/${encodeURIComponent(taskId)}/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action, note: note || null }),
      });
    } else {
      // `client_id` and `updated_at` are both required, not optional extras.
      // Without them the server cannot tell this write apart from a stranger's
      // and refuses every status change with a 409 that blames "another user"
      // — for a task nobody else had touched. `updated_at` comes from the row
      // the table already holds, so a genuine concurrent edit is still caught.
      const row = table.getRows?.().find((r) => String(r.id) === String(taskId));
      res = await apiFetch(`/api/tasks/${encodeURIComponent(taskId)}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          status: newStatus,
          client_id: clientId(),
          updated_at: row?.updated_at ?? null,
        }),
      });
    }

    if (res && res.status === 409) {
      // A real conflict: someone else wrote this task since the table loaded.
      // Reloading is the actionable response — it brings in their change and a
      // fresh token, so the user can look at the current status and decide
      // again, rather than being told to refresh and left to do it by hand.
      showError(
        "Someone else changed this task while the list was open. " +
        "The list has been refreshed — please try again."
      );
      sel.value = originalStatus;
      sel.className = `status-select ${statusSelectClass(originalStatus)}`;
      await loadTasks();
      return;
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

  // Restore the view the URL describes *before* the first fetch, so a return
  // from the canvas issues one request for page 4 rather than fetching page 1
  // and then correcting itself.
  table.setInitialState({
    page: view.page,
    sortKey: view.sortKey,
    sortDesc: view.sortDesc,
  });
  if (view.query) el("searchInput").value = view.query;

  // An assignee value produced by the name search ("user-3,user-7" or "none")
  // has no matching <option>; assigning it would blank the select instead. Such
  // a value is restored onto the search box below, once the roster has loaded.
  const assigneeFromSearch = isSearchFilterValue(view.assignee);
  for (const [id, value] of [
    ["statusFilter", view.status],
    ["teamFilter", view.team],
    ["assigneeFilter", assigneeFromSearch ? "All" : view.assignee],
  ]) {
    const node = el(id);
    if (node && value && value !== "All") node.value = value;
  }

  // loadLookups() populates the team/assignee selects, so it must finish before
  // the restored values above can stick — it rebuilds those <option> lists.
  await loadLookups();
  _setAssigneeSearchReady(true);
  for (const [id, value] of [
    ["teamFilter", view.team],
    ["assigneeFilter", assigneeFromSearch ? "All" : view.assignee],
  ]) {
    const node = el(id);
    if (node && value && value !== "All") node.value = value;
  }

  if (assigneeFromSearch) {
    // The URL carries ids, not the text that produced them, so recover a name
    // to show. Any matched member's username re-derives the same id set for a
    // full-name query; "none" has nobody to name, so the box is left empty and
    // the filter simply stands until the user types.
    const ids = new Set(
      view.assignee.split(",").map((part) => parseInt(part.replace(/^user-/, ""), 10))
    );
    const named = assignableMembers.find((m) => ids.has(m.user_id));
    el("assigneeSearch").value = named?.username ?? "";
    el("assigneeFilter").disabled = true;
  }

  await loadTasks();
}

/**
 * The hash changed while this view stayed mounted — e.g. the Back button
 * stepping from `#/tasks?page=5` to `#/tasks?page=4`.
 *
 * Only the page is honoured. Re-applying sort and filters here would fight the
 * user's own edits to those controls, whereas the page is exactly what Back is
 * expected to move.
 */
export function onParamsChange(hashParams) {
  if (!table) return;
  const view = readViewStateFromHash(hashParams);
  if (view.page === table.getState().page) return;
  table.setInitialState({ page: view.page });
  table.load();
}

export function unmount() {
  // If an upload is in flight when the user navigates away, abort it cleanly.
  if (_uploadAbortCtrl) {
    _uploadAbortCtrl.abort();
    _uploadAbortCtrl = null;
  }
  if (_assigneeSearchTimer) {
    clearTimeout(_assigneeSearchTimer);
    _assigneeSearchTimer = null;
  }

  root = null;
  ctx = null;
  table = null;
  teamsById.clear();
  usersById.clear();
  assignableMembers = [];
}

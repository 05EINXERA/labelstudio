/**
 * Exports view (tracker P4.5).
 *
 * Builder UI for creating an annotation export job with:
 *   - Status filter: all | New | In Progress | Completed | Approved
 *   - Format: JSON (COCO) | CSV
 *   - Include: annotations_only (implemented) | with_mask_* (TODO, disabled)
 *
 * The TODO-marked options (mask rendering, YOLO/Pascal VOC formats, image
 * bundling) are explicitly disabled with a tooltip explaining they're not
 * built yet, per REFACTOR_MANAGEMENT.md Phase 4 requirement. The backend
 * rejects them with a 422 rather than silently ignoring.
 *
 * Uses the job-queue pattern from detect.py — polls /api/exports/{job_id}
 * until complete, then offers /api/exports/{job_id}/download (one-shot).
 */
import { apiFetch } from "../../api.js?v=1";
import { escapeHTML } from "../../utils.js?v=1";

let root = null;
let ctx = null;
let abortController = null;
let pollInterval = null;

// Job state: null | { job_id, status, format, task_count }
let currentJob = null;

// ---------------------------------------------------------------------------
// Template
// ---------------------------------------------------------------------------

function template() {
  return `
    <div class="mgmt-title-row">
      <div>
        <p class="mgmt-eyebrow">Project</p>
        <h2>Exports</h2>
      </div>
    </div>

    <p style="color:var(--muted); font-size:.88rem; margin:-8px 0 24px;">
      Export annotations from this project with optional filters and format choices.
    </p>

    <div id="errorBanner" class="mgmt-error" style="display:none;"></div>

    <!-- Export builder (shows when no job is running) -->
    <div id="builderSection">
      <div class="metric-tile" style="margin-bottom:16px;">
        <p class="label">Status filter</p>
        <p style="font-size:.85rem; color:var(--muted); margin:6px 0 10px;">
          Choose which tasks to include based on their status. Leave all unchecked to export everything.
        </p>
        <div style="display:flex; flex-wrap:wrap; gap:12px;">
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;">
            <input type="checkbox" name="statusFilter" value="New">
            New
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;">
            <input type="checkbox" name="statusFilter" value="In Progress">
            In Progress
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;">
            <input type="checkbox" name="statusFilter" value="Completed">
            Completed
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;">
            <input type="checkbox" name="statusFilter" value="Approved">
            Approved
          </label>
        </div>
      </div>

      <div class="metric-tile" style="margin-bottom:16px;">
        <p class="label">Format</p>
        <div style="display:flex; flex-wrap:wrap; gap:16px; margin-top:10px;">
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;">
            <input type="radio" name="format" value="json" checked>
            JSON (COCO)
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;">
            <input type="radio" name="format" value="csv">
            CSV
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem; opacity:.4; cursor:not-allowed;" title="YOLO format not implemented yet (see REFACTOR_MANAGEMENT.md Phase 4)">
            <input type="radio" name="format" value="yolo" disabled>
            YOLO <span style="font-size:.75rem; color:var(--muted); font-style:italic;">(TODO)</span>
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem; opacity:.4; cursor:not-allowed;" title="Pascal VOC format not implemented yet (see REFACTOR_MANAGEMENT.md Phase 4)">
            <input type="radio" name="format" value="pascal_voc" disabled>
            Pascal VOC <span style="font-size:.75rem; color:var(--muted); font-style:italic;">(TODO)</span>
          </label>
        </div>
      </div>

      <div class="metric-tile" style="margin-bottom:24px;">
        <p class="label">Include</p>
        <div style="display:flex; flex-direction:column; gap:10px; margin-top:10px;">
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;">
            <input type="radio" name="include" value="annotations_only" checked>
            Annotations only
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem; opacity:.4; cursor:not-allowed;" title="Mask rendering not implemented yet (see REFACTOR_MANAGEMENT.md Phase 4 open question)">
            <input type="radio" name="include" value="with_images" disabled>
            With original images <span style="font-size:.75rem; color:var(--muted); font-style:italic;">(TODO)</span>
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem; opacity:.4; cursor:not-allowed;" title="Mask rendering not implemented yet (see REFACTOR_MANAGEMENT.md Phase 4 open question)">
            <input type="radio" name="include" value="with_mask_colors" disabled>
            With mask colors <span style="font-size:.75rem; color:var(--muted); font-style:italic;">(TODO)</span>
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem; opacity:.4; cursor:not-allowed;" title="Mask rendering not implemented yet (see REFACTOR_MANAGEMENT.md Phase 4 open question)">
            <input type="radio" name="include" value="with_mask_index" disabled>
            With mask index color <span style="font-size:.75rem; color:var(--muted); font-style:italic;">(TODO)</span>
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem; opacity:.4; cursor:not-allowed;" title="Mask rendering not implemented yet (see REFACTOR_MANAGEMENT.md Phase 4 open question)">
            <input type="radio" name="include" value="with_mask_binary" disabled>
            With binary masks <span style="font-size:.75rem; color:var(--muted); font-style:italic;">(TODO)</span>
          </label>
        </div>
        <p style="font-size:.8rem; color:var(--muted); margin-top:10px;">
          ℹ️ Mask rendering and image bundling are not implemented yet.
          Bounding-box and polygon annotations have no inherent raster mask;
          rendering one is a separate feature (see REFACTOR_MANAGEMENT.md Phase 4
          open question 4).
        </p>
      </div>

      <div style="display:flex; gap:10px; justify-content:flex-end;">
        <button type="button" class="primary" id="exportBtn"
          style="padding:9px 18px; border-radius:8px; font-weight:600;">
          Create export
        </button>
      </div>
    </div>

    <!-- Job status (shows when job is pending/completed) -->
    <div id="jobSection" style="display:none;">
      <div class="metric-tile" style="margin-bottom:16px;">
        <p class="label">Export job</p>
        <div id="jobStatus" style="margin-top:10px;"></div>
      </div>
      <div style="display:flex; gap:10px; justify-content:flex-end;">
        <button type="button" class="tool-button" id="cancelJobBtn">Cancel</button>
        <button type="button" class="primary" id="downloadBtn" style="padding:9px 18px; border-radius:8px; font-weight:600;" disabled>
          Download
        </button>
      </div>
    </div>
  `;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

function stopPolling() {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
}

function showBuilder() {
  el("builderSection").style.display = "";
  el("jobSection").style.display = "none";
  stopPolling();
  currentJob = null;
}

function showJobSection() {
  el("builderSection").style.display = "none";
  el("jobSection").style.display = "";
}

// ---------------------------------------------------------------------------
// Export builder
// ---------------------------------------------------------------------------

function getFormData() {
  // Status filter: only include checked values; empty array means "all"
  const statusChecked = [...root.querySelectorAll('input[name="statusFilter"]:checked')]
    .map((cb) => cb.value);

  const format = root.querySelector('input[name="format"]:checked')?.value || "json";
  const include = root.querySelector('input[name="include"]:checked')?.value || "annotations_only";

  return {
    projectId: ctx.projectId,
    format,
    include,
    statusFilter: statusChecked.length > 0 ? statusChecked : null,
  };
}

async function createExport() {
  clearError();
  const payload = getFormData();

  try {
    const res = await apiFetch("/api/exports", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res) return;
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      showError(body?.detail || `Could not create export (${res.status}).`);
      return;
    }
    const body = await res.json();
    currentJob = { job_id: body.job_id, status: "pending", format: payload.format };
    showJobSection();
    renderJobStatus();
    startPolling();
  } catch (err) {
    console.error("Export creation failed", err);
    showError("Could not create export.");
  }
}

function bindExportBuilder() {
  el("exportBtn").addEventListener("click", createExport);
}

// ---------------------------------------------------------------------------
// Job polling
// ---------------------------------------------------------------------------

function renderJobStatus() {
  if (!currentJob) return;

  const statusEl = el("jobStatus");
  const downloadBtn = el("downloadBtn");

  if (currentJob.status === "pending") {
    statusEl.innerHTML = `
      <p style="font-size:.9rem; color:var(--muted);">
        <strong>Status:</strong> Building export…
      </p>
      <div style="margin-top:8px; height:6px; background:var(--line); border-radius:4px; overflow:hidden;">
        <div style="height:100%; background:var(--accent); width:60%; animation:pulse 1.5s ease-in-out infinite;"></div>
      </div>
      <style>
        @keyframes pulse {
          0%, 100% { opacity: 0.6; }
          50% { opacity: 1; }
        }
      </style>`;
    downloadBtn.disabled = true;
  } else if (currentJob.status === "completed") {
    stopPolling();
    statusEl.innerHTML = `
      <p style="font-size:.9rem; color:var(--accent-dark);">
        ✓ <strong>Export ready</strong> —
        ${currentJob.task_count} task${currentJob.task_count === 1 ? "" : "s"},
        ${currentJob.format.toUpperCase()} format.
      </p>`;
    downloadBtn.disabled = false;
  } else if (currentJob.status === "failed") {
    stopPolling();
    statusEl.innerHTML = `
      <p style="font-size:.9rem; color:#e05260;">
        ✗ Export failed. ${escapeHTML(currentJob.error || "")}
      </p>`;
    downloadBtn.disabled = true;
  }
}

async function pollJobStatus() {
  if (!currentJob || currentJob.status !== "pending") return;

  try {
    const res = await apiFetch(`/api/exports/${currentJob.job_id}`, {
      signal: abortController.signal,
    });
    if (!res) return;
    if (res.status === 404) {
      stopPolling();
      showError("Export job not found or expired.");
      showBuilder();
      return;
    }
    if (!res.ok) {
      stopPolling();
      showError(`Polling failed (${res.status}).`);
      return;
    }
    const body = await res.json();
    currentJob.status = body.status;
    if (body.status === "completed") {
      currentJob.task_count = body.task_count;
      currentJob.format = body.format;
    } else if (body.status === "failed") {
      currentJob.error = body.error;
    }
    renderJobStatus();
  } catch (err) {
    if (err.name === "AbortError") return;
    console.error("Polling error", err);
  }
}

function startPolling() {
  stopPolling();
  pollInterval = setInterval(pollJobStatus, 1000);
  pollJobStatus(); // immediate first call
}

async function downloadExport() {
  if (!currentJob || currentJob.status !== "completed") return;

  try {
    const res = await apiFetch(`/api/exports/${currentJob.job_id}/download`);
    if (!res) return;
    if (!res.ok) {
      showError(`Download failed (${res.status}).`);
      return;
    }
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `export-${ctx.projectId}.${currentJob.format === "csv" ? "csv" : "json"}`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);

    // Job is one-shot; return to builder
    showBuilder();
  } catch (err) {
    console.error("Download failed", err);
    showError("Could not download the export.");
  }
}

function bindJobSection() {
  el("cancelJobBtn").addEventListener("click", () => {
    stopPolling();
    showBuilder();
  });

  el("downloadBtn").addEventListener("click", downloadExport);
}

// ---------------------------------------------------------------------------
// Mount / unmount
// ---------------------------------------------------------------------------

export async function mount(hostRoot, hostCtx) {
  root = hostRoot;
  ctx = hostCtx;
  abortController = new AbortController();

  root.innerHTML = template();
  bindExportBuilder();
  bindJobSection();
}

export function unmount() {
  stopPolling();
  abortController?.abort();
  abortController = null;
  currentJob = null;
  root = null;
  ctx = null;
}

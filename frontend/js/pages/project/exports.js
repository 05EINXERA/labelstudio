/**
 * Exports view (tracker P4.5).
 *
 * Builder UI for creating an annotation export job on two independent axes,
 * bundled into one project-named ZIP:
 *   - Status filter: all | New | In Progress | Completed | Approved
 *   - Format (pick one): COCO | Task JSON (single / per-task) | YOLO
 *   - Image output (pick one): none | original | annotated | mask (direct /
 *                              index / binary)
 *
 * Each axis lands in its own top-level folder in the zip. Image outputs are
 * rendered from the polygons and are export-only; a caveat note shows when a
 * mask output is selected. Some outputs cannot represent every task (YOLO and
 * masks need image dimensions), so the completed-job panel surfaces the
 * backend's `skipped` list rather than letting a short export be silent.
 *
 * Uses the job-queue pattern from detect.py — polls /api/exports/{job_id}
 * until complete, then offers /api/exports/{job_id}/download (one-shot).
 */
import { apiFetch } from "../../api.js?v=3";
import { escapeHTML } from "../../utils.js?v=1";

let root = null;
let ctx = null;
let abortController = null;
let pollInterval = null;

// Job state: null | { job_id, status, format, imageOutput, task_count }
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
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;">
            <input type="checkbox" name="statusFilter" value="Declined">
            Declined
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;">
            <input type="checkbox" name="statusFilter" value="Verified">
            Verified
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;">
            <input type="checkbox" name="statusFilter" value="Checked">
            Checked
          </label>
        </div>
      </div>

      <div class="metric-tile" style="margin-bottom:16px;">
        <p class="label">Annotation format</p>
        <p style="font-size:.85rem; color:var(--muted); margin:6px 0 10px;">
          The annotation data lands in its own folder inside the export.
        </p>
        <div style="display:flex; flex-direction:column; gap:10px;">
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;" title="COCO JSON: {images, categories, annotations} in one file">
            <input type="radio" name="format" value="coco" checked>
            COCO JSON
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;" title="A JSON array of task objects, one file for the whole project">
            <input type="radio" name="format" value="annotations_json">
            Task JSON — single file
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;" title="One JSON file per task, under jsons/, named after each image">
            <input type="radio" name="format" value="annotations_pertask">
            Task JSON — per-task
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;" title="classes.txt and one YOLOv8 segmentation label file per task">
            <input type="radio" name="format" value="yolo">
            YOLO segmentation
          </label>
        </div>
      </div>

      <div class="metric-tile" style="margin-bottom:24px;">
        <p class="label">Image output</p>
        <p style="font-size:.85rem; color:var(--muted); margin:6px 0 10px;">
          Optionally bundle images alongside the annotations, in their own folder.
        </p>
        <div style="display:flex; flex-direction:column; gap:10px;">
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;" title="No images — annotations only">
            <input type="radio" name="imageOutput" value="none" checked>
            None
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;" title="The uploaded source images, copied unchanged">
            <input type="radio" name="imageOutput" value="original">
            Original image
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;" title="Source image with the polygons drawn on top (PNG)">
            <input type="radio" name="imageOutput" value="annotated">
            With annotation
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;" title="RGB PNG masks: each pixel is the class or instance colour">
            <input type="radio" name="imageOutput" value="mask_direct">
            Mask — direct colour
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;" title="Palette PNG masks: each pixel is a class or instance index">
            <input type="radio" name="imageOutput" value="mask_index">
            Mask — index colour
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;" title="8-bit grayscale PNG: annotated pixels 255, background 0">
            <input type="radio" name="imageOutput" value="mask_binary">
            Mask — binary
          </label>
        </div>
        <p id="maskNote" style="font-size:.8rem; color:var(--muted); margin-top:10px; display:none;">
          ℹ️ Masks are rendered from the polygons and are export-only — they
          cannot be imported back. Direct-colour masks are written as PNG
          (not the lossy JPEG some tools emit) so a class stays readable from
          the pixel. An image with more than 255 shapes overflows the indexed
          instance palette; use direct colour for those.
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

// Human-readable name for the completed-job line; format codes like "PERTASK"
// are internal and shouldn't leak into the UI.
const FORMAT_LABELS = {
  coco: "COCO JSON",
  annotations_json: "Task JSON — single file",
  annotations_pertask: "Task JSON — per-task",
  yolo: "YOLO segmentation",
};

const IMAGE_OUTPUT_LABELS = {
  none: "no images",
  original: "original image",
  annotated: "annotated image",
  mask_direct: "direct-colour mask",
  mask_index: "index-colour mask",
  mask_binary: "binary mask",
};

// Image outputs that render a mask and warrant the caveat note.
const MASK_OUTPUTS = new Set(["mask_direct", "mask_index", "mask_binary"]);

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

  const format = root.querySelector('input[name="format"]:checked')?.value || "coco";
  const imageOutput = root.querySelector('input[name="imageOutput"]:checked')?.value || "none";

  return {
    projectId: ctx.projectId,
    format,
    imageOutput,
    // The backend still accepts an include field; annotations_only is the only
    // implemented value, and image outputs are their own axis now rather than
    // an include variant.
    include: "annotations_only",
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
    currentJob = {
      job_id: body.job_id,
      status: "pending",
      format: payload.format,
      imageOutput: payload.imageOutput,
    };
    showJobSection();
    renderJobStatus();
    startPolling();
  } catch (err) {
    console.error("Export creation failed", err);
    showError("Could not create export.");
  }
}

function updateMaskNote() {
  const imageOutput = root.querySelector('input[name="imageOutput"]:checked')?.value;
  const note = el("maskNote");
  if (note) note.style.display = MASK_OUTPUTS.has(imageOutput) ? "" : "none";
}

function bindExportBuilder() {
  el("exportBtn").addEventListener("click", createExport);
  // Show the mask caveat only when a mask image output is selected.
  root.querySelectorAll('input[name="imageOutput"]').forEach((radio) => {
    radio.addEventListener("change", updateMaskNote);
  });
  updateMaskNote();
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
    const skipped = currentJob.skipped || [];
    let skippedHtml = "";
    if (skipped.length > 0) {
      // A short export must be visible, not silent — this is the UI half of the
      // backend's `skipped` reporting (YOLO/mask tasks it could not represent).
      const items = skipped
        .map((s) => `<li>${escapeHTML(s.filename)} — ${escapeHTML(s.reason)}</li>`)
        .join("");
      skippedHtml = `
        <p style="font-size:.85rem; color:#b45309; margin-top:8px;">
          ⚠ ${skipped.length} item${skipped.length === 1 ? "" : "s"} could not be
          included:
        </p>
        <ul style="font-size:.82rem; color:var(--muted); margin:4px 0 0 18px;">${items}</ul>`;
    }
    const fmtLabel = FORMAT_LABELS[currentJob.format] || currentJob.format;
    const imgOut = currentJob.imageOutput || "none";
    const imgLabel = IMAGE_OUTPUT_LABELS[imgOut] || imgOut;
    const imgSuffix = imgOut === "none" ? "" : ` + ${escapeHTML(imgLabel)}`;
    statusEl.innerHTML = `
      <p style="font-size:.9rem; color:var(--accent-dark);">
        ✓ <strong>Export ready</strong> —
        ${currentJob.task_count} task${currentJob.task_count === 1 ? "" : "s"},
        ${escapeHTML(fmtLabel)}${imgSuffix}.
      </p>${skippedHtml}`;
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
      currentJob.imageOutput = body.image_output || currentJob.imageOutput || "none";
      currentJob.skipped = body.skipped || [];
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

/** The server names the file; prefer that over guessing so the two can't drift. */
function filenameFromResponse(res) {
  const header = res.headers.get("Content-Disposition");
  if (!header) return null;
  const match = /filename="?([^";]+)"?/i.exec(header);
  return match ? match[1].trim() : null;
}

/**
 * Fallback only — used if the response carries no Content-Disposition.
 * Mirrors the backend's carve-out: a single-file annotation format with no
 * image output is a bare .json; everything else is the project-named zip.
 */
function localFilename() {
  const singleFile = currentJob.format === "coco" || currentJob.format === "annotations_json";
  if (singleFile && (currentJob.imageOutput || "none") === "none") {
    return `export-${ctx.projectId}.json`;
  }
  return `export-${ctx.projectId}.zip`;
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
    a.download = filenameFromResponse(res) || localFilename();

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

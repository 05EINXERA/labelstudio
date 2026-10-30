/**
 * Imports view (tracker P4.3).
 *
 * Two tabs:
 *   Classes   — delegates to the label import endpoints (P3.3): same
 *               file/mode UI as in classes.js but surfaced here too so
 *               "import something for this project" has one obvious home.
 *   Annotations — annotation import via the P4.2 endpoints: upload a COCO
 *                 JSON, a LabelMe per-image JSON, or the app's own export, see
 *                 a preview of what will match / be created, then apply with
 *                 merge or replace.
 *
 * Both tabs reuse the apiFetch wrapper (rule 13) and modal CSS (rule 12).
 */
import { apiFetch } from "../../api.js?v=3";
import { escapeHTML } from "../../utils.js?v=3";

let root = null;
let ctx = null;
let abortController = null;

// Preview state for the annotation tab — held here so "Apply" can re-use the
// upload results without re-sending the files.
// Each entry: { file: File, preview: object }
let pendingFiles = [];

// ---------------------------------------------------------------------------
// Template
// ---------------------------------------------------------------------------

function template() {
  return `
    <div class="mgmt-title-row">
      <div>
        <p class="mgmt-eyebrow">Project</p>
        <h2>Imports</h2>
      </div>
    </div>

    <p style="color:var(--muted); font-size:.88rem; margin:-8px 0 20px;">
      Import classes or existing annotation files into this project.
    </p>

    <!-- Tab bar -->
    <div class="import-tabs" role="tablist" style="display:flex; gap:0; margin-bottom:24px; border-bottom:2px solid var(--line);">
      <button
        type="button" role="tab" aria-selected="true" id="tabClasses"
        class="import-tab-btn is-active"
        style="padding:8px 20px; border:none; background:none; cursor:pointer;
               font-weight:600; font-size:.95rem; border-bottom:2px solid var(--accent);
               margin-bottom:-2px; color:var(--accent);">
        Classes
      </button>
      <button
        type="button" role="tab" aria-selected="false" id="tabAnnotations"
        class="import-tab-btn"
        style="padding:8px 20px; border:none; background:none; cursor:pointer;
               font-size:.95rem; border-bottom:2px solid transparent;
               margin-bottom:-2px; color:var(--muted);">
        Annotations
      </button>
    </div>

    <div id="errorBanner" class="mgmt-error" style="display:none;"></div>

    <!-- ===== Classes tab ===== -->
    <div id="panelClasses">
      <p style="font-size:.88rem; color:var(--muted); margin-bottom:18px;">
        Accepts a <strong>.json</strong> class set (the app's own export),
        a <strong>.csv</strong> with <em>name,color</em> columns, or a plain
        <strong>.txt</strong> with one class name per line.
      </p>

      <div class="metric-grid" style="grid-template-columns:repeat(auto-fill,minmax(220px,1fr)); margin-bottom:24px;">
        <div class="metric-tile">
          <p class="label">Merge</p>
          <p style="font-size:.85rem; color:var(--muted); margin:6px 0 0;">
            Adds new classes; updates the color of classes that already exist.
            Re-importing the same file is a no-op.
          </p>
        </div>
        <div class="metric-tile">
          <p class="label" style="color:#e05260;">Replace</p>
          <p style="font-size:.85rem; color:var(--muted); margin:6px 0 0;">
            Deletes all existing classes for this project first, then imports.
            Existing annotations keep their label ids but the labels will no
            longer appear in the canvas.
          </p>
        </div>
      </div>

      <div style="display:flex; gap:10px; align-items:center; flex-wrap:wrap; margin-bottom:20px;">
        <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;">
          <input type="radio" name="classImportMode" value="merge" checked>
          Merge
        </label>
        <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;">
          <input type="radio" name="classImportMode" value="replace" style="accent-color:#e05260;">
          <span style="color:#e05260;">Replace</span>
        </label>
        <button type="button" class="primary" id="classImportBtn"
          style="margin-left:16px; padding:9px 18px; border-radius:8px; font-weight:600;">
          Choose file…
        </button>
        <input type="file" id="classImportInput" accept=".json,.csv,.txt" style="display:none;">
      </div>

      <div id="classImportResult"></div>
    </div>

    <!-- ===== Annotations tab ===== -->
    <div id="panelAnnotations" style="display:none;">
      <p style="font-size:.88rem; color:var(--muted); margin-bottom:18px;">
        Accepts a <strong>COCO JSON</strong> (images + categories + annotations),
        a <strong>LabelMe JSON</strong> (one file per image), the app's own
        per-task <strong>JSON export</strong>, or a <strong>.zip</strong> of any
        of them — including a folder of LabelMe files, or the per-task export
        archive straight from the Exports tab.
        Images are matched to existing tasks by filename — upload images first
        via the Tasks tab, then import annotations here.
      </p>

      <!-- Step 1: choose file + mode -->
      <div class="metric-tile" style="margin-bottom:16px;">
        <p class="label">Step 1 — Choose file and mode</p>
        <div style="display:flex; gap:16px; align-items:center; flex-wrap:wrap; margin-top:12px;">
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;">
            <input type="radio" name="annImportMode" value="merge" checked>
            Merge — append to existing annotations
          </label>
          <label style="display:flex; align-items:center; gap:8px; font-size:.9rem;">
            <input type="radio" name="annImportMode" value="replace" style="accent-color:#e05260;">
            <span style="color:#e05260;">Replace — overwrite existing annotations</span>
          </label>
        </div>
        <div style="margin-top:14px;">
          <button type="button" class="primary" id="annChooseBtn"
            style="padding:9px 18px; border-radius:8px; font-weight:600;">
            Choose file…
          </button>
          <input type="file" id="annFileInput" accept=".json,.zip" multiple style="display:none;">
          <span id="annFileName" style="font-size:.85rem; color:var(--muted); margin-left:12px;"></span>
        </div>
      </div>

      <!-- Step 2: preview -->
      <div id="annPreviewSection" style="display:none;">
        <div class="metric-tile" style="margin-bottom:16px;">
          <p class="label">Step 2 — Review matches</p>
          <div id="annPreviewBody" style="margin-top:10px;"></div>
        </div>
        <div style="display:flex; gap:10px; justify-content:flex-end;">
          <button type="button" class="tool-button" id="annCancelBtn">Cancel</button>
          <button type="button" class="primary" id="annApplyBtn"
            style="padding:9px 18px; border-radius:8px; font-weight:600;">
            Apply import
          </button>
        </div>
      </div>

      <div id="annResultSection" style="display:none;">
        <div id="annResultBody"></div>
        <div style="margin-top:14px;">
          <button type="button" class="tool-button" id="annImportAnotherBtn">Import another file</button>
        </div>
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

// ---------------------------------------------------------------------------
// Tab switching
// ---------------------------------------------------------------------------

function switchTab(active) {
  // active is "classes" or "annotations"
  const panels = { classes: el("panelClasses"), annotations: el("panelAnnotations") };
  const tabs = { classes: el("tabClasses"), annotations: el("tabAnnotations") };

  for (const [key, panel] of Object.entries(panels)) {
    panel.style.display = key === active ? "" : "none";
  }
  for (const [key, btn] of Object.entries(tabs)) {
    const on = key === active;
    btn.setAttribute("aria-selected", String(on));
    btn.classList.toggle("is-active", on);
    btn.style.borderBottomColor = on ? "var(--accent)" : "transparent";
    btn.style.color = on ? "var(--accent)" : "var(--muted)";
    btn.style.fontWeight = on ? "600" : "400";
  }
  clearError();
}

function bindTabs() {
  el("tabClasses").addEventListener("click", () => switchTab("classes"));
  el("tabAnnotations").addEventListener("click", () => switchTab("annotations"));
}

// ---------------------------------------------------------------------------
// Classes tab
// ---------------------------------------------------------------------------

function bindClassImport() {
  el("classImportBtn").addEventListener("click", () => el("classImportInput").click());

  el("classImportInput").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;

    clearError();
    el("classImportResult").innerHTML = `<p class="mgmt-empty" style="padding:10px;">Importing…</p>`;

    const mode = root.querySelector('input[name="classImportMode"]:checked')?.value || "merge";
    const formData = new FormData();
    formData.append("file", file);

    try {
      const res = await apiFetch(
        `/api/labels/import?projectId=${encodeURIComponent(ctx.projectId)}&mode=${mode}`,
        { method: "POST", body: formData }
      );
      if (!res) return;
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        showError(body?.detail || `Import failed (${res.status}).`);
        el("classImportResult").innerHTML = "";
        return;
      }
      const body = await res.json();
      el("classImportResult").innerHTML = `
        <div class="mgmt-empty" style="text-align:left; padding:10px 14px;
             background:var(--panel-2); border-radius:8px; color:var(--accent-dark);
             border:1px solid var(--line);">
          ✓ ${escapeHTML(file.name)} — ${body.created} added,
          ${body.updated} updated${body.skipped ? `, ${body.skipped} skipped` : ""}.
        </div>`;
    } catch (err) {
      console.error("Class import failed", err);
      showError("Could not import classes.");
      el("classImportResult").innerHTML = "";
    }
  });
}

// ---------------------------------------------------------------------------
// Annotations tab — preview
// ---------------------------------------------------------------------------

function renderPreview(preview, fileCount) {
  const matched = preview.matched || [];
  const unmatched = preview.unmatched || [];
  const newLabels = preview.new_labels || [];
  const fileLabel = fileCount > 1 ? `${fileCount} files` : "file";

  let html = `<p style="font-size:.88rem; margin-bottom:12px;">
    <strong>${preview.total_annotations}</strong> annotation${preview.total_annotations === 1 ? "" : "s"}
    across <strong>${matched.length + unmatched.length}</strong> image${(matched.length + unmatched.length) === 1 ? "" : "s"} in ${escapeHTML(fileLabel)}.
  </p>`;

  if (matched.length > 0) {
    html += `<p style="font-weight:600; font-size:.88rem; margin-bottom:6px; color:var(--accent-dark);">
      ✓ ${matched.length} image${matched.length === 1 ? "" : "s"} matched to tasks
    </p>
    <table style="width:100%; font-size:.85rem; border-collapse:collapse; margin-bottom:14px;">
      <thead><tr style="border-bottom:1px solid var(--line);">
        <th style="text-align:left; padding:4px 8px; color:var(--muted); font-weight:600;">Filename</th>
        <th style="text-align:right; padding:4px 8px; color:var(--muted); font-weight:600;">Annotations</th>
      </tr></thead>
      <tbody>`;
    for (const m of matched) {
      html += `<tr style="border-bottom:1px solid var(--line);">
        <td style="padding:5px 8px;">${escapeHTML(m.filename)}</td>
        <td style="padding:5px 8px; text-align:right;">${m.annotation_count}</td>
      </tr>`;
    }
    html += `</tbody></table>`;
  }

  if (unmatched.length > 0) {
    html += `<p style="font-weight:600; font-size:.88rem; margin-bottom:6px; color:#e05260;">
      ✗ ${unmatched.length} image${unmatched.length === 1 ? "" : "s"} not matched (no task with that filename)
    </p>
    <ul style="font-size:.82rem; color:var(--muted); margin:0 0 14px; padding-left:18px;">`;
    for (const u of unmatched) {
      html += `<li>${escapeHTML(u.filename)} (${u.annotation_count} annotation${u.annotation_count === 1 ? "" : "s"})</li>`;
    }
    html += `</ul>`;
  }

  if (newLabels.length > 0) {
    html += `<p style="font-size:.85rem; color:var(--muted);">
      <strong>New classes that will be created:</strong>
      ${newLabels.map((n) => `<code style="background:var(--panel-2); border-radius:4px; padding:1px 5px;">${escapeHTML(n)}</code>`).join(" ")}
    </p>`;
  }

  if (matched.length === 0) {
    html += `<p style="font-size:.88rem; color:#e05260;">
      No annotations can be imported — none of the filenames in the file match
      any task in this project. Upload the images first via the Tasks tab.
    </p>`;
  }

  return html;
}

function resetAnnotationTab() {
  pendingFiles = [];
  el("annPreviewSection").style.display = "none";
  el("annResultSection").style.display = "none";
  el("annFileName").textContent = "";
  clearError();
}

function bindAnnotationImport() {
  el("annChooseBtn").addEventListener("click", () => el("annFileInput").click());

  el("annFileInput").addEventListener("change", async (e) => {
    const files = [...e.target.files];
    e.target.value = "";
    if (files.length === 0) return;

    clearError();
    pendingFiles = [];
    const names = files.map((f) => f.name);
    el("annFileName").textContent = names.length === 1 ? names[0] : `${names.length} files`;
    el("annPreviewSection").style.display = "none";
    el("annResultSection").style.display = "none";
    el("annPreviewBody").innerHTML = `<p style="color:var(--muted); font-size:.88rem;">Loading preview${files.length > 1 ? ` (1 / ${files.length})` : ""}…</p>`;
    el("annPreviewSection").style.display = "";

    // Preview each file sequentially, then merge the results.
    const previews = [];
    let failed = false;
    for (let i = 0; i < files.length; i++) {
      if (files.length > 1) {
        el("annPreviewBody").innerHTML = `<p style="color:var(--muted); font-size:.88rem;">Loading preview (${i + 1} / ${files.length})…</p>`;
      }
      const formData = new FormData();
      formData.append("file", files[i]);
      try {
        const res = await apiFetch(
          `/api/imports/annotations/preview?projectId=${encodeURIComponent(ctx.projectId)}`,
          { method: "POST", body: formData }
        );
        if (!res) return;
        if (!res.ok) {
          const body = await res.json().catch(() => null);
          showError(body?.detail || `Preview failed for ${escapeHTML(files[i].name)} (${res.status}).`);
          failed = true;
          break;
        }
        const preview = await res.json();
        previews.push({ file: files[i], preview });
      } catch (err) {
        console.error("Annotation preview failed", err);
        showError(`Could not preview ${escapeHTML(files[i].name)}.`);
        failed = true;
        break;
      }
    }

    if (failed || previews.length === 0) {
      el("annPreviewSection").style.display = "none";
      return;
    }

    pendingFiles = previews;

    // Merge previews into a combined view.
    const combined = {
      matched: previews.flatMap((p) => p.preview.matched || []),
      unmatched: previews.flatMap((p) => p.preview.unmatched || []),
      new_labels: [...new Set(previews.flatMap((p) => p.preview.new_labels || []))],
      total_annotations: previews.reduce((sum, p) => sum + (p.preview.total_annotations || 0), 0),
    };
    el("annPreviewBody").innerHTML = renderPreview(combined, files.length);
    el("annApplyBtn").disabled = combined.matched.length === 0;
  });

  el("annCancelBtn").addEventListener("click", () => resetAnnotationTab());

  el("annApplyBtn").addEventListener("click", async () => {
    if (pendingFiles.length === 0) return;
    const anyMatched = pendingFiles.some((p) => (p.preview.matched || []).length > 0);
    if (!anyMatched) return;

    const mode = root.querySelector('input[name="annImportMode"]:checked')?.value || "merge";

    el("annApplyBtn").disabled = true;
    el("annApplyBtn").textContent = "Importing…";

    let totalTasks = 0;
    let totalAnnotations = 0;
    let totalSkipped = 0;
    let failed = false;

    for (let i = 0; i < pendingFiles.length; i++) {
      const entry = pendingFiles[i];
      if ((entry.preview.matched || []).length === 0) continue;

      if (pendingFiles.length > 1) {
        el("annApplyBtn").textContent = `Importing (${i + 1} / ${pendingFiles.length})…`;
      }

      const formData = new FormData();
      formData.append("file", entry.file);

      try {
        const res = await apiFetch(
          `/api/imports/annotations?projectId=${encodeURIComponent(ctx.projectId)}&mode=${mode}`,
          { method: "POST", body: formData }
        );
        if (!res) return;
        if (!res.ok) {
          const body = await res.json().catch(() => null);
          showError(body?.detail || `Import failed for ${escapeHTML(entry.file.name)} (${res.status}).`);
          failed = true;
          break;
        }
        const body = await res.json();
        totalTasks += body.tasks_updated || 0;
        totalAnnotations += body.annotations_imported || 0;
        totalSkipped += (body.unmatched || []).length;
      } catch (err) {
        console.error("Annotation import failed", err);
        showError(`Could not import ${escapeHTML(entry.file.name)}.`);
        failed = true;
        break;
      }
    }

    if (failed) {
      el("annApplyBtn").disabled = false;
      el("annApplyBtn").textContent = "Apply import";
      return;
    }

    el("annPreviewSection").style.display = "none";
    el("annResultSection").style.display = "";

    el("annResultBody").innerHTML = `
      <div class="mgmt-empty" style="text-align:left; padding:14px 18px;
           background:var(--panel-2); border-radius:8px; color:var(--accent-dark);
           border:1px solid var(--line);">
        ✓ Import complete —
        <strong>${totalTasks}</strong> task${totalTasks === 1 ? "" : "s"} updated,
        <strong>${totalAnnotations}</strong> annotation${totalAnnotations === 1 ? "" : "s"} imported.
        ${totalSkipped ? `<br><span style="color:var(--muted);">${totalSkipped} image${totalSkipped === 1 ? "" : "s"} not matched (skipped).</span>` : ""}
      </div>`;
    pendingFiles = [];
    el("annFileName").textContent = "";
  });

  el("annImportAnotherBtn").addEventListener("click", () => resetAnnotationTab());
}

// ---------------------------------------------------------------------------
// Mount / unmount
// ---------------------------------------------------------------------------

export async function mount(hostRoot, hostCtx) {
  root = hostRoot;
  ctx = hostCtx;
  abortController = new AbortController();

  root.innerHTML = template();
  bindTabs();
  bindClassImport();
  bindAnnotationImport();
}

export function unmount() {
  abortController?.abort();
  abortController = null;
  pendingFiles = [];
  root = null;
  ctx = null;
}

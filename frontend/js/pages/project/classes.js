/**
 * Classes: label class management for this project (tracker P3.4).
 *
 * Moved out of the class-CRUD IIFE that used to live inline in
 * `dashboard.html` with no nav entry pointing at it. Backed by the bulk /
 * import / export endpoints added in P3.3.
 *
 * "Usage count" (how many tasks reference each label) requires loading task
 * annotations, which can be large; it is fetched once on mount and is not
 * kept live if a task changes elsewhere — acceptable for a management view
 * that is not the annotation canvas itself.
 */
import { apiFetch } from "../../api.js?v=1";
import { escapeHTML, generateUUID } from "../../utils.js?v=1";
import { createDataTable } from "../../components/data-table.js?v=1";

let root = null;
let ctx = null;
let table = null;
let usageByLabelId = {};

const ICON_DELETE = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>`;

function template() {
  return `
    <div class="mgmt-title-row">
      <div>
        <p class="mgmt-eyebrow">Project</p>
        <h2>Classes</h2>
      </div>
      <div style="display:flex; gap:10px; flex-wrap:wrap; align-items:center;">
        <div style="display:flex; align-items:center; gap:8px;">
          <label for="exportFormat" style="font-size:.88rem; color:var(--muted);">Export format:</label>
          <select id="exportFormat" style="padding:6px 10px; border-radius:6px; border:1px solid var(--line); background:var(--panel); color:var(--ink); font-size:.88rem;">
            <option value="fastlabel" selected>FastLabel</option>
            <option value="json">JSON</option>
            <option value="csv">CSV</option>
            <option value="txt">TXT</option>
          </select>
        </div>
        <button type="button" class="tool-button" id="exportBtn">Export set</button>
        <button type="button" class="tool-button" id="importBtn">Import set</button>
        <input type="file" id="importInput" accept=".json,.csv,.txt" style="display:none;">
        <button type="button" class="primary" id="addBtn" style="padding:9px 16px;border-radius:8px;font-weight:600;">+ Add class</button>
      </div>
    </div>

    <p style="color:var(--muted); font-size:.88rem; margin:-8px 0 16px;">
      Classes are shared by every task in this project — add one here and it
      appears in the annotation canvas immediately.
    </p>

    <div id="errorBanner" class="mgmt-error" style="display:none;"></div>
    <div id="importSummary"></div>

    <div class="bulk-bar" id="bulkBar">
      <span class="count" id="bulkCount"></span>
      <input type="color" id="bulkColor" value="#3b82f6" title="Bulk color" style="width:30px;height:30px;padding:0;border:none;border-radius:6px;cursor:pointer;">
      <button type="button" class="tool-button" id="bulkColorBtn">Apply color</button>
      <button type="button" class="tool-button" id="bulkDeleteBtn" style="color:#e05260;border-color:rgba(224,82,96,.3);">Delete selected</button>
    </div>

    <div class="mgmt-toolbar">
      <input type="search" id="searchInput" placeholder="Search classes…" aria-label="Search classes">
    </div>

    <div id="tableMount"></div>

    <div class="modal-overlay" id="classModal">
      <div class="modal-content">
        <div class="modal-header">
          <h2 id="classModalTitle">Add class</h2>
          <button class="modal-close" id="classModalClose" type="button">&times;</button>
        </div>
        <form id="classForm">
          <input type="hidden" id="classId">
          <div class="modal-body" style="display:grid; gap:14px;">
            <label style="display:grid;gap:6px;">
              <span style="font-size:.85rem;color:var(--muted);">Name</span>
              <input type="text" id="className" required maxlength="80"
                style="padding:9px;border-radius:6px;border:1px solid var(--line);background:var(--panel);color:var(--ink);">
            </label>
            <label style="display:grid;gap:6px;">
              <span style="font-size:.85rem;color:var(--muted);">Color</span>
              <input type="color" id="classColor" value="#3b82f6" style="width:48px;height:36px;padding:0;border:none;border-radius:6px;cursor:pointer;">
            </label>
          </div>
          <div style="display:flex;gap:10px;justify-content:flex-end;padding:16px;">
            <button type="button" class="tool-button" id="classCancel">Cancel</button>
            <button type="submit" class="primary" style="padding:9px 18px;border-radius:6px;">Save</button>
          </div>
        </form>
      </div>
    </div>

    <div class="modal-overlay" id="importModal">
      <div class="modal-content">
        <div class="modal-header">
          <h2>Import classes</h2>
          <button class="modal-close" id="importModalClose" type="button">&times;</button>
        </div>
        <div class="modal-body" style="display:grid; gap:14px;">
          <p style="font-size:.88rem;color:var(--muted);">
            Accepts .json, .csv, or a newline-delimited .txt list of names.
          </p>
          <label style="display:flex; align-items:center; gap:8px;">
            <input type="radio" name="importMode" value="merge" checked>
            <span>Merge — add new classes, update matching names by color</span>
          </label>
          <label style="display:flex; align-items:center; gap:8px;">
            <input type="radio" name="importMode" value="replace">
            <span style="color:#e05260;">Replace — delete existing classes first</span>
          </label>
        </div>
        <div style="display:flex;gap:10px;justify-content:flex-end;padding:16px;">
          <button type="button" class="tool-button" id="importCancel">Cancel</button>
          <button type="button" class="primary" id="importConfirm" style="padding:9px 18px;border-radius:6px;">Choose file…</button>
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

async function loadUsage() {
  // Best-effort: usage counts are a nice-to-have, so a failure here should
  // not block the class list from rendering.
  usageByLabelId = {};
  try {
    const res = await apiFetch(`/api/tasks?projectId=${encodeURIComponent(ctx.projectId)}`);
    if (!res || !res.ok) return;
    const tasks = await res.json();
    for (const task of tasks) {
      let anns = task.annotations;
      if (typeof anns === "string") {
        try { anns = JSON.parse(anns); } catch { anns = []; }
      }
      if (!Array.isArray(anns)) continue;
      for (const a of anns) {
        if (a.labelId) usageByLabelId[a.labelId] = (usageByLabelId[a.labelId] || 0) + 1;
      }
    }
  } catch (err) {
    console.error("Failed to compute class usage", err);
  }
}

async function loadLabels() {
  const res = await apiFetch(`/api/labels?projectId=${encodeURIComponent(ctx.projectId)}`);
  if (!res) return;
  if (!res.ok) {
    showError(`Could not load classes (${res.status}).`);
    return;
  }
  clearError();
  const labels = await res.json();
  table.setRows(labels.map((l) => ({ ...l, usage: usageByLabelId[l.id] || 0 })));
}

// --- add / edit modal --------------------------------------------------

function openClassModal(label) {
  const isEdit = Boolean(label);
  el("classModalTitle").textContent = isEdit ? "Edit class" : "Add class";
  el("classId").value = isEdit ? label.id : "";
  el("className").value = isEdit ? label.name : "";
  el("classColor").value = isEdit ? label.color : "#3b82f6";
  el("classModal").classList.add("is-active");
  el("className").focus();
}

function closeClassModal() {
  el("classModal").classList.remove("is-active");
}

function bindClassModal() {
  el("addBtn").addEventListener("click", () => openClassModal(null));
  el("classModalClose").addEventListener("click", closeClassModal);
  el("classCancel").addEventListener("click", closeClassModal);
  el("classModal").addEventListener("click", (e) => { if (e.target === el("classModal")) closeClassModal(); });

  el("classForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const id = el("classId").value || generateUUID();
    const name = el("className").value.trim();
    if (!name) return;
    try {
      const res = await apiFetch("/api/labels", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id, name, color: el("classColor").value, projectId: ctx.projectId }),
      });
      if (!res) return;
      if (!res.ok) {
        showError(`Could not save the class (${res.status}).`);
        return;
      }
      closeClassModal();
      await loadLabels();
    } catch (err) {
      console.error("Failed to save class", err);
      showError("Could not save the class.");
    }
  });
}

// --- bulk actions --------------------------------------------------------

function updateBulkBar(selection) {
  const bar = el("bulkBar");
  bar.classList.toggle("is-active", selection.size > 0);
  el("bulkCount").textContent = `${selection.size} selected`;
}

function bindBulkActions() {
  el("bulkColorBtn").addEventListener("click", async () => {
    const ids = new Set(table.getSelection());
    if (!ids.size) return;
    const color = el("bulkColor").value;
    const labels = table.getRows()
      .filter((r) => ids.has(r.id))
      .map((r) => ({ id: r.id, name: r.name, color, projectId: ctx.projectId }));
    try {
      const res = await apiFetch("/api/labels/bulk", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ projectId: ctx.projectId, labels }),
      });
      if (!res) return;
      if (!res.ok) {
        showError(`Could not update the selected classes (${res.status}).`);
        return;
      }
      await loadLabels();
    } catch (err) {
      console.error("Bulk color update failed", err);
      showError("Could not update the selected classes.");
    }
  });

  el("bulkDeleteBtn").addEventListener("click", async () => {
    const ids = [...table.getSelection()];
    if (!ids.length) return;
    if (!confirm(`Delete ${ids.length} class${ids.length === 1 ? "" : "es"}? Existing annotations keep the old label id but it will no longer appear in the canvas.`)) return;
    try {
      const res = await apiFetch("/api/labels/bulk-delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ projectId: ctx.projectId, ids }),
      });
      if (!res) return;
      if (!res.ok) {
        showError(`Could not delete the selected classes (${res.status}).`);
        return;
      }
      table.clearSelection();
      await loadLabels();
    } catch (err) {
      console.error("Bulk delete failed", err);
      showError("Could not delete the selected classes.");
    }
  });
}

// --- import / export ------------------------------------------------------

function bindImportExport() {
  el("exportBtn").addEventListener("click", async () => {
    try {
      // T5.3-5.4: Get selected format and pass to API
      const format = el("exportFormat").value || "fastlabel";
      const res = await apiFetch(`/api/labels/export?projectId=${encodeURIComponent(ctx.projectId)}&format=${encodeURIComponent(format)}`);
      if (!res) return;
      if (!res.ok) {
        showError(`Could not export classes (${res.status}).`);
        return;
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      
      // Set appropriate file extension based on format
      let extension;
      if (format === "csv") extension = "csv";
      else if (format === "txt") extension = "txt";
      else extension = "json"; // fastlabel and json both use .json
      
      a.download = `classes-${ctx.projectId}.${extension}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err) {
      console.error("Export failed", err);
      showError("Could not export classes.");
    }
  });

  el("importBtn").addEventListener("click", () => el("importModal").classList.add("is-active"));
  el("importModalClose").addEventListener("click", () => el("importModal").classList.remove("is-active"));
  el("importCancel").addEventListener("click", () => el("importModal").classList.remove("is-active"));
  el("importModal").addEventListener("click", (e) => {
    if (e.target === el("importModal")) el("importModal").classList.remove("is-active");
  });

  el("importConfirm").addEventListener("click", () => el("importInput").click());

  el("importInput").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;

    const mode = root.querySelector('input[name="importMode"]:checked')?.value || "merge";
    el("importModal").classList.remove("is-active");

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
        return;
      }
      clearError();
      const body = await res.json();
      el("importSummary").innerHTML = `<div class="mgmt-empty" style="text-align:left;padding:10px 14px;color:var(--accent-dark);">
          ✓ Imported: ${body.created} added, ${body.updated} updated${body.skipped ? `, ${body.skipped} skipped` : ""}.
        </div>`;
      await loadLabels();
    } catch (err) {
      console.error("Import failed", err);
      showError("Could not import classes.");
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
    sortKey: "name",
    emptyMessage: "No classes yet. Add one, or import a class set.",
    onSelectionChange: updateBulkBar,
    matches: (row, q) => String(row.name || "").toLowerCase().includes(q),
    columns: [
      {
        key: "color", label: "", sortable: false, width: "44px",
        render: (r) => `<span style="display:inline-block;width:18px;height:18px;border-radius:5px;background:${escapeHTML(r.color || "#999")};border:1px solid var(--line);"></span>`,
      },
      { key: "name", label: "Name" },
      { key: "usage", label: "Used in", align: "center", render: (r) => `${r.usage} task${r.usage === 1 ? "" : "s"}` },
      {
        key: "actions", label: "", sortable: false, align: "center",
        render: () => `<div class="row-actions"><button type="button" data-action="delete" class="danger" title="Delete class">${ICON_DELETE}</button></div>`,
      },
    ],
  });

  // Row click (outside the delete button) opens edit — the color swatch and
  // name are not individually clickable targets, so this keeps interaction
  // simple without adding a dedicated edit button per row.
  el("tableMount").addEventListener("click", (e) => {
    if (e.target.closest("[data-action]") || e.target.closest("[data-role]")) return;
    const tr = e.target.closest("tr[data-id]");
    if (!tr) return;
    const row = table.getRows().find((r) => String(r.id) === tr.dataset.id);
    if (row) openClassModal(row);
  });

  bindClassModal();
  bindBulkActions();
  bindImportExport();

  el("searchInput").addEventListener("input", (e) => table.setQuery(e.target.value));

  table.onAction("delete", async (row) => {
    if (!confirm(`Delete "${row.name}"?`)) return;
    try {
      const res = await apiFetch(`/api/labels/${encodeURIComponent(row.id)}?projectId=${encodeURIComponent(ctx.projectId)}`, { method: "DELETE" });
      if (!res) return;
      if (!res.ok) {
        showError(`Could not delete the class (${res.status}).`);
        return;
      }
      await loadLabels();
    } catch (err) {
      console.error("Failed to delete class", err);
      showError("Could not delete the class.");
    }
  });

  await loadUsage();
  await loadLabels();
}

export function unmount() {
  root = null;
  ctx = null;
  table = null;
  usageByLabelId = {};
}

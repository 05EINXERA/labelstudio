/**
 * Level 1: the projects list (tracker P1.4).
 *
 * Replaces the projects table that was inline in `dashboard.html`. Scope is
 * server-side now — `GET /api/projects` returns only the caller's projects with
 * their metrics embedded, so this page makes one request instead of pairing
 * /api/projects with /api/projects/metrics/batch.
 */
import { apiFetch } from "../api.js?v=1";
import { escapeHTML, formatTime } from "../utils.js?v=1";
import { createDataTable } from "../components/data-table.js?v=1";

const els = {
  user: document.getElementById("currentUser"),
  logout: document.getElementById("logoutBtn"),
  error: document.getElementById("errorBanner"),
  search: document.getElementById("searchInput"),
  status: document.getElementById("statusFilter"),
  pageSize: document.getElementById("pageSizeSelect"),
  mount: document.getElementById("tableMount"),
  newBtn: document.getElementById("newProjectBtn"),
  modal: document.getElementById("projectModal"),
  modalTitle: document.getElementById("projectModalTitle"),
  form: document.getElementById("projectForm"),
  fId: document.getElementById("projectFormId"),
  fName: document.getElementById("projectFormName"),
  fType: document.getElementById("projectFormType"),
  fAssignee: document.getElementById("projectFormAssignee"),
  fStatus: document.getElementById("projectFormStatus"),
  statusField: document.getElementById("projectStatusField"),
};

const ICON_EDIT = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/></svg>`;
const ICON_DELETE = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/><path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/><line x1="10" x2="10" y1="11" y2="17"/><line x1="14" x2="14" y1="11" y2="17"/></svg>`;

function statusPill(status) {
  const s = status || "New";
  const cls = s === "Completed" ? "is-completed"
    : s === "In Progress" ? "is-progress"
    : s === "Approved" ? "is-approved" : "";
  return `<span class="pill ${cls}">${escapeHTML(s)}</span>`;
}

function showError(message) {
  els.error.textContent = message;
  els.error.style.display = "block";
}

function clearError() {
  els.error.style.display = "none";
}

const table = createDataTable({
  mount: els.mount,
  rowId: (row) => row.id,
  sortKey: "created_at",
  sortDesc: true,
  emptyMessage: "No projects yet. Create one to get started.",
  matches: (row, q) =>
    String(row.name || "").toLowerCase().includes(q) ||
    String(row.assignee || "").toLowerCase().includes(q),
  columns: [
    {
      key: "name",
      label: "Project",
      render: (r) => `<a class="cell-link" href="project.html?id=${encodeURIComponent(r.id)}">${escapeHTML(r.name || "Untitled")}</a>`,
    },
    { key: "type", label: "Type", render: (r) => `<span style="color:var(--muted);">${escapeHTML(r.type || "—")}</span>` },
    {
      key: "assignee",
      label: "Assignee",
      render: (r) => r.assignee ? escapeHTML(r.assignee) : `<span style="color:var(--muted);">Unassigned</span>`,
    },
    { key: "status", label: "Status", render: (r) => statusPill(r.status) },
    {
      key: "progress",
      label: "Progress",
      render: (r) => `<div class="progress-cell">
          <div class="progress-track"><div class="progress-fill" style="width:${r.progress || 0}%"></div></div>
          <span style="font-weight:600;font-size:.82rem;">${r.progress || 0}%</span>
        </div>`,
    },
    { key: "total", label: "Tasks", align: "center", render: (r) => `${r.completed || 0} / ${r.total || 0}` },
    { key: "classes", label: "Classes", align: "center" },
    {
      key: "total_time",
      label: "Time",
      render: (r) => r.total_time
        ? `<span style="font-family:monospace;font-size:.85rem;">${formatTime(r.total_time)}</span>`
        : `<span style="color:var(--muted);">—</span>`,
    },
    {
      key: "actions",
      label: "",
      sortable: false,
      align: "center",
      render: () => `<div class="row-actions">
          <button type="button" data-action="edit" title="Edit project">${ICON_EDIT}</button>
          <button type="button" data-action="delete" class="danger" title="Delete project">${ICON_DELETE}</button>
        </div>`,
    },
  ],
});

// --- data ------------------------------------------------------------------

async function loadProjects() {
  try {
    const res = await apiFetch("/api/projects");
    if (!res) return; // apiFetch redirected to login
    if (!res.ok) {
      showError(`Could not load projects (${res.status}).`);
      return;
    }
    clearError();
    table.setRows(await res.json());
  } catch (err) {
    console.error("Failed to load projects", err);
    showError("Could not reach the server. Check your connection and reload.");
  }
}

async function loadTeam() {
  try {
    const res = await apiFetch("/api/team");
    if (!res || !res.ok) return;
    const team = await res.json();
    team.forEach((m) => {
      const opt = document.createElement("option");
      opt.value = m.name;
      opt.textContent = m.name;
      els.fAssignee.appendChild(opt);
    });
  } catch (err) {
    // A missing team list only limits the assignee dropdown; the page is usable.
    console.error("Failed to load team", err);
  }
}

// --- modal -----------------------------------------------------------------

function openModal(project) {
  const isEdit = Boolean(project);
  els.modalTitle.textContent = isEdit ? "Edit project" : "New project";
  els.fId.value = isEdit ? project.id : "";
  els.fName.value = isEdit ? (project.name || "") : "";
  els.fType.value = isEdit ? (project.type || "Image - Polygon") : "Image - Polygon";
  els.fAssignee.value = isEdit ? (project.assignee || "") : "";
  els.fStatus.value = isEdit ? (project.status || "Preparing") : "Preparing";
  // Status is derived from task completion on create, so only expose it on edit.
  els.statusField.style.display = isEdit ? "grid" : "none";
  els.modal.classList.add("is-active");
  els.fName.focus();
}

function closeModal() {
  els.modal.classList.remove("is-active");
}

els.form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const id = els.fId.value;
  const name = els.fName.value.trim();
  if (!name) return;

  try {
    let res;
    if (id) {
      res = await apiFetch(`/api/projects/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          assignee: els.fAssignee.value,
          status: els.fStatus.value,
        }),
      });
    } else {
      res = await apiFetch("/api/projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name,
          slug: name.toLowerCase().replace(/\s+/g, "-"),
          type: els.fType.value,
          // Ignored by the server, which takes the owner from the token.
          creator: localStorage.getItem("dataset_username") || "",
          assignee: els.fAssignee.value,
        }),
      });
    }
    if (!res) return;
    if (!res.ok) {
      showError(`Could not save the project (${res.status}).`);
      return;
    }
    closeModal();
    await loadProjects();
  } catch (err) {
    console.error("Failed to save project", err);
    showError("Could not save the project.");
  }
});

// --- events ----------------------------------------------------------------

els.newBtn.addEventListener("click", () => openModal(null));
document.getElementById("projectModalClose").addEventListener("click", closeModal);
document.getElementById("projectFormCancel").addEventListener("click", closeModal);
els.modal.addEventListener("click", (e) => {
  if (e.target === els.modal) closeModal();
});

els.search.addEventListener("input", (e) => table.setQuery(e.target.value));
els.status.addEventListener("change", (e) => table.setFilter("status", e.target.value));
els.pageSize.addEventListener("change", (e) => table.setPageSize(e.target.value));

table.onAction("edit", (row) => openModal(row));
table.onAction("delete", async (row) => {
  if (!confirm(`Delete "${row.name}" and all of its tasks? This cannot be undone.`)) return;
  try {
    const res = await apiFetch(`/api/projects/${row.id}`, { method: "DELETE" });
    if (!res) return;
    if (!res.ok) {
      showError(`Could not delete the project (${res.status}).`);
      return;
    }
    await loadProjects();
  } catch (err) {
    console.error("Failed to delete project", err);
    showError("Could not delete the project.");
  }
});

els.logout.addEventListener("click", async () => {
  try {
    await apiFetch("/api/auth/logout", { method: "POST" });
  } catch (err) {
    console.error("Logout request failed", err);
  }
  localStorage.removeItem("logged_in");
  localStorage.removeItem("dataset_username");
  window.location.href = "/";
});

// --- init ------------------------------------------------------------------

els.user.textContent = localStorage.getItem("dataset_username") || "";
loadTeam();
loadProjects();

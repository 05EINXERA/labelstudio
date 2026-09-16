/**
 * Workspace-wide task search — the "Find tasks" view on the projects page.
 *
 * Answers "which project is that image in?", which previously meant opening
 * each project's Tasks tab in turn. `GET /api/tasks` without a `projectId`
 * already scopes itself to every project the caller can reach, so this needs
 * no new endpoint — only `project_id`/`project_name` on the rows, which the
 * cross-project branch of that endpoint now returns.
 *
 * Server-paged (`onFetchData`), not client-filtered: the result set is every
 * task in the workspace, far too large to ship to the browser and filter
 * there. Searching, filtering, sorting and paging all round-trip.
 *
 * Read-only by design. Editing a task needs the project context that the
 * owner/reviewer checks are keyed on, so a row links into its project's
 * workspace rather than offering inline actions here.
 */
import { apiFetch } from "../api.js?v=3";
import { escapeHTML, formatTime, statusPillClass, TASK_STATUSES } from "../utils.js?v=3";
import { createDataTable } from "../components/data-table.js?v=3";

// Matches the projects list's own poll, so both views age at the same rate.
const POLL_INTERVAL_MS = 30_000;

// Typing in the search box hits the server, so coalesce keystrokes.
const SEARCH_DEBOUNCE_MS = 300;

function statusPill(status) {
  const s = status || "New";
  return `<span class="pill ${statusPillClass(s)}">${escapeHTML(s)}</span>`;
}

function relativeTime(value) {
  if (!value) return "—";
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "—";
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

/**
 * @param {object} opts
 * @param {HTMLElement} opts.mount            container for the results table
 * @param {HTMLInputElement} opts.searchInput
 * @param {HTMLSelectElement} opts.projectFilter
 * @param {HTMLSelectElement} opts.statusFilter
 * @param {HTMLSelectElement} opts.assigneeFilter
 * @param {HTMLInputElement} opts.myTasksFilter   "My Tasks" checkbox
 * @param {HTMLSelectElement} opts.pageSizeSelect
 * @param {(message:string)=>void} [opts.onError]
 * @param {()=>void} [opts.onClearError]
 */
export function createTaskSearch(opts) {
  const {
    mount, searchInput, projectFilter, statusFilter, assigneeFilter,
    myTasksFilter, pageSizeSelect, onError, onClearError,
  } = opts;

  // The name tasks are assigned under. Assignment is by team-member name, not
  // by login — all annotators share one account — so this is the same value
  // the project Tasks tab's "My Tasks" box uses.
  const myName = () => localStorage.getItem("dataset_username") || "";

  let active = false;
  let pollTimer = null;
  let debounceTimer = null;
  // Monotonic request id: a slow response for an abandoned query must not
  // overwrite the results of the query the user is actually looking at.
  let requestSeq = 0;

  async function fetchTasks(state) {
    const seq = ++requestSeq;
    const params = new URLSearchParams();
    params.set("limit", String(state.pageSize));
    params.set("offset", String((state.page - 1) * state.pageSize));
    if (state.query && state.query.trim()) params.set("search", state.query.trim());
    if (state.filters.status && state.filters.status !== "All") {
      params.set("status", state.filters.status);
    }
    if (state.filters.assignee && state.filters.assignee !== "All") {
      params.set("assignee", state.filters.assignee);
    }
    if (state.filters.project_id && state.filters.project_id !== "All") {
      params.set("projectIds", String(state.filters.project_id));
    }
    if (state.sortKey) {
      params.set("sort_by", state.sortKey);
      params.set("sort_desc", String(Boolean(state.sortDesc)));
    }

    try {
      const res = await apiFetch(`/api/tasks?${params.toString()}`);
      if (!res) return; // apiFetch redirected to login
      if (seq !== requestSeq) return; // a newer query has already been issued
      if (!res.ok) {
        onError?.(`Could not search tasks (${res.status}).`);
        return;
      }
      const data = await res.json();
      if (seq !== requestSeq) return;
      onClearError?.();
      table.setServerData(data.items || [], data.total || 0);
    } catch (err) {
      console.error("Task search failed", err);
      if (seq === requestSeq) onError?.("Could not reach the server while searching tasks.");
    }
  }

  const table = createDataTable({
    mount,
    rowId: (row) => row.id,
    sortKey: "updated_at",
    sortDesc: true,
    pageSize: 25,
    onFetchData: fetchTasks,
    emptyMessage: "No tasks match your search.",
    columns: [
      {
        key: "image_path",
        label: "",
        sortable: false,
        width: "56px",
        render: (r) => r.image_path
          ? `<img src="/${escapeHTML(String(r.image_path).replace(/\\/g, "/"))}" alt="" style="height:40px;border-radius:4px;border:1px solid var(--line);">`
          : "",
      },
      {
        key: "description",
        label: "Task",
        render: (r) => `<a class="cell-link" href="app.html?projectId=${encodeURIComponent(r.project_id)}&taskId=${encodeURIComponent(r.id)}" title="${escapeHTML(r.description || "")}" style="max-width:300px;display:inline-block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;vertical-align:middle;">${escapeHTML(r.description || "Untitled")}</a>`,
      },
      {
        // The column this whole view exists for.
        key: "project_id",
        label: "Project",
        render: (r) => r.project_id
          ? `<a class="cell-link" href="project.html?id=${encodeURIComponent(r.project_id)}">${escapeHTML(r.project_name || `Project ${r.project_id}`)}</a>`
          : `<span style="color:var(--muted);">—</span>`,
      },
      {
        key: "assignee",
        label: "Assignee",
        render: (r) => r.assignee ? escapeHTML(r.assignee) : `<span style="color:var(--muted);">—</span>`,
      },
      { key: "status", label: "Status", render: (r) => statusPill(r.status) },
      {
        key: "time_spent",
        label: "Time",
        render: (r) => r.time_spent
          ? `<span style="font-family:monospace;font-size:.85rem;">${formatTime(r.time_spent)}</span>`
          : `<span style="color:var(--muted);">—</span>`,
      },
      {
        key: "updated_at",
        label: "Updated",
        render: (r) => `<span style="color:var(--muted);">${escapeHTML(relativeTime(r.updated_at))}</span>`,
      },
    ],
  });

  // --- filter population ---------------------------------------------------

  statusFilter.innerHTML =
    `<option value="All">All statuses</option>` +
    TASK_STATUSES.map((s) => `<option value="${escapeHTML(s)}">${escapeHTML(s)}</option>`).join("");

  /** Fill the project dropdown from rows the projects list already fetched. */
  function setProjects(projects) {
    const previous = projectFilter.value;
    projectFilter.innerHTML =
      `<option value="All">All projects</option>` +
      (projects || [])
        .map((p) => `<option value="${escapeHTML(String(p.id))}">${escapeHTML(p.name || `Project ${p.id}`)}</option>`)
        .join("");
    // Keep the chosen project selected across the 30 s refresh; if it has since
    // been deleted the option is gone and the filter falls back to "All".
    if (previous && projectFilter.querySelector(`option[value="${CSS.escape(previous)}"]`)) {
      projectFilter.value = previous;
    }
  }

  /** Add `name` to the assignee dropdown if it is not already listed. */
  function ensureAssigneeOption(name) {
    if (!name) return;
    if (assigneeFilter.querySelector(`option[value="${CSS.escape(name)}"]`)) return;
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    // Directly under "All assignees", where your own name is easiest to find.
    assigneeFilter.insertBefore(opt, assigneeFilter.options[1] || null);
  }

  /** Fill the assignee dropdown from the team roster. */
  function setAssignees(members) {
    const previous = assigneeFilter.value;
    assigneeFilter.innerHTML =
      `<option value="All">All assignees</option>` +
      (members || [])
        .map((m) => `<option value="${escapeHTML(m.name)}">${escapeHTML(m.name)}</option>`)
        .join("");
    // Your own name always belongs here, even when the roster does not list it:
    // GET /api/team is scoped to teams you belong to and comes back empty for
    // someone on no team, which would otherwise leave you unable to filter to
    // your own work.
    ensureAssigneeOption(myName());
    if (previous && assigneeFilter.querySelector(`option[value="${CSS.escape(previous)}"]`)) {
      assigneeFilter.value = previous;
    }
    // The roster arrives asynchronously and this rebuild drops the selection,
    // so re-apply what "My Tasks" is filtering by — otherwise the box stays
    // ticked above a dropdown reading "All assignees": two controls on one
    // filter, visibly disagreeing.
    if (myTasksFilter.checked && myName()) {
      assigneeFilter.value = myName();
    }
  }

  // --- events --------------------------------------------------------------

  searchInput.addEventListener("input", (e) => {
    const value = e.target.value;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => table.setQuery(value), SEARCH_DEBOUNCE_MS);
  });
  projectFilter.addEventListener("change", (e) => table.setFilter("project_id", e.target.value));
  statusFilter.addEventListener("change", (e) => table.setFilter("status", e.target.value));
  pageSizeSelect.addEventListener("change", (e) => table.setPageSize(e.target.value));

  // "My Tasks" and the assignee dropdown are two ways to set one filter, so
  // they mirror each other rather than silently disagreeing: ticking the box
  // moves the dropdown to your own name, and picking someone else unticks it.
  assigneeFilter.addEventListener("change", (e) => {
    const value = e.target.value;
    myTasksFilter.checked = Boolean(myName()) && value === myName();
    table.setFilter("assignee", value);
  });

  myTasksFilter.addEventListener("change", (e) => {
    const name = myName();
    if (e.target.checked && name) {
      // The checkbox filters by the name we already hold, so it must not depend
      // on the roster: GET /api/team returns only members of teams the caller
      // belongs to, and is empty for someone on no team. Ensuring the option
      // exists keeps the dropdown showing the name it is filtering by instead
      // of sitting on "All assignees" while the results are clearly narrowed.
      ensureAssigneeOption(name);
      assigneeFilter.value = name;
      table.setFilter("assignee", name);
    } else {
      assigneeFilter.value = "All";
      table.setFilter("assignee", "All");
    }
  });

  // --- lifecycle -----------------------------------------------------------

  return {
    setProjects,
    setAssignees,
    /** Show the view and load its first page. Idempotent. */
    activate() {
      if (active) return;
      active = true;
      table.showLoading(6);
      table.reload();
      // Only poll while the view is on screen — the projects list keeps its own
      // poll running, and two background pollers on one page is one too many.
      pollTimer = setInterval(() => {
        if (document.visibilityState === "visible") table.reload();
      }, POLL_INTERVAL_MS);
    },
    /** Hide the view and stop its polling. Idempotent. */
    deactivate() {
      if (!active) return;
      active = false;
      clearInterval(pollTimer);
      pollTimer = null;
      clearTimeout(debounceTimer);
    },
    isActive: () => active,
    refresh() { if (active) table.reload(); },
  };
}

/**
 * The "Find tasks" panel on the projects page — search across every project.
 *
 * A *finder*, deliberately not a second Tasks view: no inline status select, no
 * assignment, no delete, no bulk actions, no checkbox column. Every mutation
 * already has a home in the project's own Tasks view, and doing them here would
 * need a role check against a *different* project per row
 * (.devnotes/task-project-search/02_DESIGN.md § 1.1).
 *
 * Imported by `projects-list.js` on first activation of its tab, so a user who
 * only ever looks at projects never downloads this module or issues its
 * requests. `mount()` runs once; switching tabs afterwards only toggles the
 * panel, which is what keeps the table's page, sort and search across a switch.
 *
 * Backed by `GET /api/tasks/search`, which resolves permission scope once per
 * request and returns a fixed number of queries' worth of work regardless of
 * how many tasks matched.
 */
import { apiFetch } from "../api.js?v=5";
import { escapeHTML, formatTime } from "../utils.js?v=2";
import { statusClass, TASK_STATUSES } from "../task-status.js?v=3";
import { createDataTable } from "../components/data-table.js?v=6";

const PAGE_SIZE = 25;

/**
 * Column key → the server's sort key.
 *
 * Only the sortable columns appear. Team and Assignee are omitted on purpose:
 * the server sorts those by *id*, which orders rows by nothing a user can see,
 * so offering the header would promise an ordering the data does not have.
 */
const SERVER_SORT_KEYS = {
  description: "description",
  project_name: "project",
  status: "status",
  updated_at: "updated_at",
  time_spent: "time_spent",
};

let table = null;
let root = null;

/** The teams the caller belongs to, for the Team filter. Fetched once. */
let teams = [];

function statusPill(status) {
  const s = status || "New";
  return `<span class="pill ${statusClass(s)}">${escapeHTML(s)}</span>`;
}

function showError(message) {
  const banner = root?.querySelector("#findTasksError");
  if (!banner) return;
  banner.textContent = message;
  banner.style.display = "block";
}

function clearError() {
  const banner = root?.querySelector("#findTasksError");
  if (banner) banner.style.display = "none";
}

/**
 * Fetch one page of search results.
 *
 * The table owns page/sort/filter state and calls this whenever any of it
 * changes. It also owns the two protections this view depends on and must not
 * re-implement: a 300 ms debounce on `setQuery`, so typing a filename is one
 * request rather than one per keystroke, and a sequence guard that drops a
 * superseded response — without which a fast typist's older result can paint
 * over a newer one.
 */
async function fetchPage({ page, pageSize, sortKey, sortDesc, query, filters }) {
  const params = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
    sort: SERVER_SORT_KEYS[sortKey] || "description",
    order: sortDesc ? "desc" : "asc",
  });
  // Searching and filtering run in SQL. With one page in memory, filtering here
  // would search 25 rows out of thousands and report "no matches" for a task
  // that plainly exists.
  if (query) params.set("q", query);
  for (const key of ["status", "team", "assignee"]) {
    const value = filters?.[key];
    if (value && value !== "All") params.set(key, value);
  }

  const res = await apiFetch(`/api/tasks/search?${params.toString()}`);
  if (!res) return null;                  // apiFetch handled a 401 redirect
  if (!res.ok) throw new Error(`Could not search tasks (${res.status}).`);
  clearError();
  return res.json();
}

const COLUMNS = [
  {
    key: "description",
    label: "Task",
    sortable: true,
    render: (r) =>
      // No view state on the canvas link, deliberately: the canvas's prev/next
      // should walk the project's own task order. A cross-project result is not
      // a list the server can reconstruct for one project.
      `<a class="cell-link" href="app.html?projectId=${encodeURIComponent(r.project_id)}&taskId=${encodeURIComponent(r.id)}"
          title="${escapeHTML(r.description || "")}">${escapeHTML(r.description || "(untitled)")}</a>`,
  },
  {
    key: "project_name",
    label: "Project",
    sortable: true,
    render: (r) =>
      `<a class="cell-link" href="project.html?id=${encodeURIComponent(r.project_id)}">${escapeHTML(r.project_name || "Untitled")}</a>`,
  },
  {
    key: "status",
    label: "Status",
    sortable: true,
    render: (r) => statusPill(r.status),
  },
  {
    key: "assigned_team_name",
    label: "Team",
    sortable: false,
    render: (r) =>
      r.assigned_team_name
        ? escapeHTML(r.assigned_team_name)
        : '<span class="muted">Unassigned</span>',
  },
  {
    key: "assignee_name",
    label: "Assignee",
    sortable: false,
    render: (r) =>
      r.assignee_name
        ? escapeHTML(r.assignee_name)
        : '<span class="muted">Unassigned</span>',
  },
  {
    key: "time_spent",
    label: "Time",
    sortable: true,
    align: "right",
    render: (r) => formatTime(r.time_spent || 0),
  },
];

/**
 * The Team filter's options.
 *
 * `All` and `Unassigned` are sentinels and always work. A specific team has to
 * come from somewhere, and cross-project there is no single grant list to read
 * — so the caller's own teams (`GET /api/teams`) are the honest source. One
 * request at mount, never per keystroke.
 */
async function loadTeams() {
  try {
    const res = await apiFetch("/api/teams");
    if (!res || !res.ok) return;
    teams = await res.json();
  } catch (err) {
    // Not fatal: the search works without the team filter, so a failure here
    // degrades one select rather than the view. Logged, never swallowed.
    console.error("Could not load teams for the filter", err);
  }
}

function toolbarHTML() {
  const statusOptions = ["All", ...TASK_STATUSES]
    .map(
      (s) =>
        `<option value="${escapeHTML(s)}">${escapeHTML(s === "All" ? "All statuses" : s)}</option>`,
    )
    .join("");

  const teamOptions = [
    '<option value="All">All teams</option>',
    '<option value="unassigned">Unassigned</option>',
    ...teams.map(
      (t) => `<option value="${encodeURIComponent(t.id)}">${escapeHTML(t.name)}</option>`,
    ),
  ].join("");

  // Assignee is All / Unassigned / Mine only. `mine` is resolved server-side
  // from the session, so it needs no roster — and there is no cross-project
  // roster endpoint to build a name search from (design § 5.4, follow-up F2).
  return `
    <div id="findTasksError" class="mgmt-error" style="display:none;"></div>

    <div class="mgmt-toolbar">
      <input type="search" id="taskSearchInput" placeholder="Search tasks by name…"
             aria-label="Search tasks by name">
      <select id="taskStatusFilter" aria-label="Filter by status">${statusOptions}</select>
      <select id="taskTeamFilter" aria-label="Filter by team">${teamOptions}</select>
      <select id="taskAssigneeFilter" aria-label="Filter by assignee">
        <option value="All">Anyone</option>
        <option value="mine">Assigned to me</option>
        <option value="unassigned">Unassigned</option>
      </select>
      <select id="taskPageSize" aria-label="Rows per page">
        <option value="25">25 per page</option>
        <option value="50">50 per page</option>
        <option value="100">100 per page</option>
      </select>
    </div>

    <div id="taskTableMount"></div>
  `;
}

/**
 * Render the view into `panel`. Called once, on first tab activation.
 *
 * @param {HTMLElement} panel
 */
export async function mount(panel) {
  root = panel;
  // Before the markup, so the Team select is populated on its first render
  // rather than needing a second pass to fill in.
  await loadTeams();
  panel.innerHTML = toolbarHTML();

  table = createDataTable({
    mount: panel.querySelector("#taskTableMount"),
    rowId: (row) => row.id,
    columns: COLUMNS,
    // A finder, not an editor: nothing here is selectable or actionable.
    selectable: false,
    sortKey: "description",
    sortDesc: false,
    pageSize: PAGE_SIZE,
    server: { fetchPage },
    emptyMessage: "No tasks match that search.",
  });

  panel
    .querySelector("#taskSearchInput")
    // setQuery is debounced by the table in server mode; debouncing again here
    // would only stack two delays.
    .addEventListener("input", (e) => table.setQuery(e.target.value.trim()));

  for (const [id, key] of [
    ["#taskStatusFilter", "status"],
    ["#taskTeamFilter", "team"],
    ["#taskAssigneeFilter", "assignee"],
  ]) {
    panel
      .querySelector(id)
      .addEventListener("change", (e) => table.setFilter(key, e.target.value));
  }

  panel
    .querySelector("#taskPageSize")
    .addEventListener("change", (e) => table.setPageSize(e.target.value));

  try {
    await table.load();
  } catch (err) {
    console.error("Could not load tasks", err);
    showError("Could not load tasks.");
  }
}

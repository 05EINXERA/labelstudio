/**
 * Workspace-wide stats on the projects list: the project Home tiles, summed
 * across every project the caller can see.
 *
 * Built purely from the rows GET /api/projects already returns (each carries
 * its own metrics, including `status_counts`), so it costs no extra request and
 * refreshes whenever the list does. The one exception is the task table a
 * status tile opens, which pages through GET /api/tasks on demand.
 */
import { apiFetch } from "../api.js?v=3";
import { escapeHTML, formatTime, statusPillClass, TASK_STATUSES } from "../utils.js?v=3";
import { createDataTable } from "../components/data-table.js?v=3";
import { TASK_COLUMNS, taskQueryParams } from "./task-search.js?v=3";

/**
 * Status of every task across all projects: count, share and a share bar.
 * Each tile is a button that lists the tasks in that status beneath the grid.
 */
function statusBreakdown(statuses, total, hasCounts, selected) {
  // A server older than this panel omits `status_counts`; say so rather than
  // render a row of zeros that reads as "no tasks in any status".
  if (!hasCounts) {
    return `<div class="mgmt-error">
        Task status counts are unavailable — the server is running an older
        version. Restart it to see the breakdown.
      </div>`;
  }
  const cards = statuses.map(([status, count]) => {
    const pct = total ? Math.round((count / total) * 100) : 0;
    const on = status === selected;
    return `<button type="button" class="metric-tile${on ? " is-selected" : ""}" data-status="${escapeHTML(status)}"
          aria-pressed="${on}" title="Show ${escapeHTML(status)} tasks"${count ? "" : ' style="opacity:.55;"'}>
        <span class="pill ${statusPillClass(status)}">${escapeHTML(status)}</span>
        <p class="value" style="margin-top:8px;">${count}</p>
        <div class="progress-cell" style="min-width:0; margin-top:6px;">
          <div class="progress-track" style="height:5px;"><div class="progress-fill" style="width:${pct}%"></div></div>
          <span class="sub" style="margin:0;">${pct}%</span>
        </div>
      </button>`;
  });
  return `<div class="metric-grid" style="grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));">
      ${cards.join("")}
    </div>`;
}

function tile({ label, value, sub }) {
  return `<div class="metric-tile">
      <p class="label">${escapeHTML(label)}</p>
      <p class="value">${escapeHTML(value)}</p>
      ${sub ? `<p class="sub">${escapeHTML(sub)}</p>` : ""}
    </div>`;
}

function aggregate(projects) {
  const sum = {
    projects: projects.length,
    total: 0,
    completed: 0,
    classes: 0,
    comments: 0,
    total_time: 0,
    status_counts: {},
  };
  for (const p of projects) {
    sum.total += p.total || 0;
    sum.completed += p.completed || 0;
    sum.classes += p.classes || 0;
    sum.comments += p.comments || 0;
    sum.total_time += p.total_time || 0;
    for (const [status, count] of Object.entries(p.status_counts || {})) {
      sum.status_counts[status] = (sum.status_counts[status] || 0) + count;
    }
  }
  sum.progress = sum.total ? Math.floor((sum.completed / sum.total) * 100) : 0;
  sum.avg_time_per_task = sum.total ? Math.floor(sum.total_time / sum.total) : 0;
  return sum;
}

// Every known status in workflow order (the shared vocabulary), zeros included
// so the breakdown always has the same shape; any other status follows A–Z.
function orderedStatuses(counts) {
  counts = { ...Object.fromEntries(TASK_STATUSES.map((st) => [st, 0])), ...counts };
  const rank = (s) => {
    const i = TASK_STATUSES.indexOf(s);
    return i === -1 ? TASK_STATUSES.length : i;
  };
  return Object.entries(counts).sort(([a], [b]) => rank(a) - rank(b) || a.localeCompare(b));
}

// Open/closed state is a per-viewer convenience, so localStorage (not the
// server) is its home. The whole panel starts collapsed — it is tall and the
// table is what most visits are for — while sections inside start open.
const PANEL_KEY = "projects_overview_open";
const SECTIONS_KEY = "projects_overview_sections";

function readPref(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch (err) {
    console.warn(`Could not read ${key}`, err);
    return fallback;
  }
}

function writePref(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (err) {
    console.warn(`Could not save ${key}`, err);
  }
}

const CHEVRON = `<svg class="stats-toggle-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
  stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>`;

export function createProjectsOverview(mount) {
  // The shell (toggle + body) is built once; render() only refills the summary
  // and body, so the 30 s poll never resets the open/closed state.
  mount.innerHTML = `
    <button type="button" class="stats-toggle" aria-expanded="false" aria-controls="projectStatsBody">
      ${CHEVRON}
      <span class="stats-toggle-title">Overview · all projects</span>
      <span class="stats-toggle-summary"></span>
    </button>
    <div class="stats-body" id="projectStatsBody" hidden></div>`;

  const toggle = mount.querySelector(".stats-toggle");
  const summary = mount.querySelector(".stats-toggle-summary");
  const body = mount.querySelector(".stats-body");

  function setOpen(open) {
    toggle.setAttribute("aria-expanded", String(open));
    toggle.classList.toggle("is-open", open);
    body.hidden = !open;
  }

  // `1` is how earlier versions stored "open"; accept it so nobody's choice resets.
  const savedOpen = readPref(PANEL_KEY, false);
  setOpen(savedOpen === true || savedOpen === 1);
  toggle.addEventListener("click", () => {
    const open = body.hidden;
    setOpen(open);
    writePref(PANEL_KEY, open);
  });

  // Sections are re-rendered on every poll, so their state lives here and is
  // written back into each <details open>. `toggle` does not bubble, hence
  // the capture listener on the stable body element.
  const sectionOpen = readPref(SECTIONS_KEY, {});
  body.addEventListener("toggle", (e) => {
    const key = e.target?.dataset?.section;
    if (!key) return;
    sectionOpen[key] = e.target.open;
    writePref(SECTIONS_KEY, sectionOpen);
  }, true);

  // --- tasks of the clicked status tile -------------------------------------

  // Built once and re-attached after every render: the section it sits in is
  // rebuilt on each poll, and a fresh table would drop the page and sort the
  // viewer had chosen.
  const panel = document.createElement("div");
  panel.className = "status-tasks";
  panel.innerHTML = `
    <div class="status-tasks-head">
      <span class="status-tasks-title"></span>
      <button type="button" class="tool-button" data-role="close-status-tasks">Close</button>
    </div>
    <div class="status-tasks-table"></div>`;
  const panelTitle = panel.querySelector(".status-tasks-title");
  const tableMount = panel.querySelector(".status-tasks-table");

  let selectedStatus = null;
  // Monotonic request id: a slow response for a tile the viewer has since left
  // must not overwrite the tasks of the tile they are looking at.
  let requestSeq = 0;

  function showTasksError(message) {
    tableMount.innerHTML = `<div class="mgmt-error">${escapeHTML(message)}</div>`;
  }

  async function fetchTasks(state) {
    const seq = ++requestSeq;
    try {
      const res = await apiFetch(`/api/tasks?${taskQueryParams(state)}`);
      if (!res) return; // apiFetch redirected to login
      if (seq !== requestSeq) return;
      if (!res.ok) {
        showTasksError(`Could not load tasks (${res.status}).`);
        return;
      }
      const data = await res.json();
      if (seq !== requestSeq) return;
      taskTable.setServerData(data.items || [], data.total || 0);
    } catch (err) {
      console.error("Failed to load tasks by status", err);
      if (seq === requestSeq) showTasksError("Could not reach the server while loading tasks.");
    }
  }

  const taskTable = createDataTable({
    mount: tableMount,
    rowId: (row) => row.id,
    sortKey: "updated_at",
    sortDesc: true,
    pageSize: 10,
    onFetchData: fetchTasks,
    emptyMessage: "No tasks in this status.",
    columns: TASK_COLUMNS,
  });

  function attachPanel() {
    body.querySelector('[data-section="status"] .stats-section-body')?.appendChild(panel);
  }

  function selectStatus(status) {
    selectedStatus = status;
    body.querySelectorAll("[data-status]").forEach((tile) => {
      const on = tile.dataset.status === status;
      tile.classList.toggle("is-selected", on);
      tile.setAttribute("aria-pressed", String(on));
    });
    if (!status) {
      requestSeq++; // abandon any in-flight load
      panel.remove();
      return;
    }
    panelTitle.textContent = `${status} tasks`;
    attachPanel();
    taskTable.showLoading(5);
    taskTable.setFilter("status", status);
    panel.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  body.addEventListener("click", (e) => {
    if (e.target.closest('[data-role="close-status-tasks"]')) {
      selectStatus(null);
      return;
    }
    const tile = e.target.closest("[data-status]");
    if (!tile) return;
    // Clicking the open tile again closes its table.
    selectStatus(tile.dataset.status === selectedStatus ? null : tile.dataset.status);
  });

  function section(key, title, hint, content) {
    const open = sectionOpen[key] !== false;
    return `<details class="stats-section" data-section="${key}"${open ? " open" : ""}>
        <summary class="stats-section-head">
          ${CHEVRON}
          <span class="stats-toggle-title">${escapeHTML(title)}</span>
          <span class="stats-section-hint">${escapeHTML(hint)}</span>
        </summary>
        <div class="stats-section-body">${content}</div>
      </details>`;
  }

  function render(projects) {
    const m = aggregate(projects);
    const remaining = Math.max(0, m.total - m.completed);
    const statuses = orderedStatuses(m.status_counts);
    const hasCounts = projects.every((p) => p.status_counts);
    const busiest = statuses.filter(([, n]) => n > 0).slice(0, 3)
      .map(([st, n]) => `${n} ${st}`).join(" · ");

    summary.textContent = `${m.progress}% · ${m.completed} / ${m.total} tasks · ${m.projects} project${m.projects === 1 ? "" : "s"}`;

    body.innerHTML = [
      section("completion", "Completion", `${m.progress}% · ${remaining} remaining`, `
        <div class="metric-tile">
          <div class="progress-cell">
            <div class="progress-track" style="height: 10px;">
              <div class="progress-fill" style="width:${m.progress}%"></div>
            </div>
            <span style="font-weight: 800; font-size: 1.1rem;">${m.progress}%</span>
          </div>
          <p class="sub">${m.completed} of ${m.total} task${m.total === 1 ? "" : "s"} completed${remaining ? ` · ${remaining} remaining` : ""}</p>
        </div>`),

      section("status", "Task status", hasCounts ? (busiest || "No tasks yet") : "Unavailable",
        statusBreakdown(statuses, m.total, hasCounts, selectedStatus)),

      section("workspace", "Workspace",
        `${m.projects} project${m.projects === 1 ? "" : "s"} · ${formatTime(m.total_time)} logged`, `
        <div class="metric-grid">
          ${tile({ label: "Projects", value: m.projects })}
          ${tile({ label: "Total tasks", value: m.total, sub: "Images across all projects" })}
          ${tile({ label: "Total classes", value: m.classes })}
          ${tile({ label: "Comments", value: m.comments })}
          ${tile({ label: "Time logged", value: formatTime(m.total_time), sub: "Across all tasks" })}
          ${tile({ label: "Avg per task", value: formatTime(m.avg_time_per_task) })}
        </div>`),
    ].join("");

    // The rebuild above detached the open task table; put it back and refresh
    // it so its rows age at the same rate as the counts on the tiles.
    if (selectedStatus) {
      attachPanel();
      taskTable.reload();
    }
  }

  function showLoading() {
    summary.textContent = "Loading…";
    body.innerHTML = `
      <div class="metric-tile skeleton skeleton-card" style="margin-bottom: 18px; height: 100px;"></div>
      <div class="metric-grid">
        ${Array.from({ length: 8 }).map(() => '<div class="metric-tile skeleton skeleton-card" style="height: 90px;"></div>').join("")}
      </div>`;
  }

  return { render, showLoading };
}

/**
 * Workspace-wide stats on the projects list: the project Home tiles, summed
 * across every project the caller can see.
 *
 * Built purely from the rows GET /api/projects already returns (each carries
 * its own metrics, including `status_counts`), so it costs no extra request and
 * refreshes whenever the list does.
 */
import { escapeHTML, formatTime, statusPillClass, TASK_STATUSES } from "../utils.js?v=3";

/** Status of every task across all projects: count, share and a share bar. */
function statusBreakdown(statuses, total) {
  const cards = statuses.map(([status, count]) => {
    const pct = total ? Math.round((count / total) * 100) : 0;
    return `<div class="metric-tile"${count ? "" : ' style="opacity:.55;"'}>
        <span class="pill ${statusPillClass(status)}">${escapeHTML(status)}</span>
        <p class="value" style="margin-top:8px;">${count}</p>
        <div class="progress-cell" style="min-width:0; margin-top:6px;">
          <div class="progress-track" style="height:5px;"><div class="progress-fill" style="width:${pct}%"></div></div>
          <span class="sub" style="margin:0;">${pct}%</span>
        </div>
      </div>`;
  });
  return `
    <p class="mgmt-eyebrow" style="margin: 4px 0 8px;">Task status · all projects</p>
    <div class="metric-grid" style="grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));">
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

// Per-viewer preference only, so localStorage (not the server) is the right
// home. Collapsed unless the viewer has opened it: the panel is tall and the
// table is what most visits are for.
const COLLAPSE_KEY = "projects_overview_open";

function readOpen() {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch (err) {
    console.warn("Could not read the stats panel state", err);
    return false;
  }
}

function writeOpen(open) {
  try {
    localStorage.setItem(COLLAPSE_KEY, open ? "1" : "0");
  } catch (err) {
    console.warn("Could not save the stats panel state", err);
  }
}

export function createProjectsOverview(mount) {
  // The shell (toggle + body) is built once; render() only refills the summary
  // and body, so the 30 s poll never resets the open/closed state.
  mount.innerHTML = `
    <button type="button" class="stats-toggle" aria-expanded="false" aria-controls="projectStatsBody">
      <svg class="stats-toggle-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
        stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m6 9 6 6 6-6"/></svg>
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

  setOpen(readOpen());
  toggle.addEventListener("click", () => {
    const open = body.hidden;
    setOpen(open);
    writeOpen(open);
  });

  function render(projects) {
    const m = aggregate(projects);
    const remaining = Math.max(0, m.total - m.completed);
    const statuses = orderedStatuses(m.status_counts);

    summary.textContent = `${m.progress}% · ${m.completed} / ${m.total} tasks · ${m.projects} project${m.projects === 1 ? "" : "s"}`;

    body.innerHTML = `
      <div class="metric-tile" style="margin-bottom: 18px;">
        <p class="label">Completion</p>
        <div class="progress-cell" style="margin-top: 6px;">
          <div class="progress-track" style="height: 10px;">
            <div class="progress-fill" style="width:${m.progress}%"></div>
          </div>
          <span style="font-weight: 800; font-size: 1.1rem;">${m.progress}%</span>
        </div>
        <p class="sub">${m.completed} of ${m.total} task${m.total === 1 ? "" : "s"} completed${remaining ? ` · ${remaining} remaining` : ""}</p>
      </div>

      ${statusBreakdown(statuses, m.total)}

      <p class="mgmt-eyebrow" style="margin: 4px 0 8px;">Workspace</p>
      <div class="metric-grid">
        ${tile({ label: "Projects", value: m.projects })}
        ${tile({ label: "Total tasks", value: m.total, sub: "Images across all projects" })}
        ${tile({ label: "Total classes", value: m.classes })}
        ${tile({ label: "Comments", value: m.comments })}
        ${tile({ label: "Time logged", value: formatTime(m.total_time), sub: "Across all tasks" })}
        ${tile({ label: "Avg per task", value: formatTime(m.avg_time_per_task) })}
      </div>`;
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

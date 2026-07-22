/**
 * Home: the project metrics page (tracker P2.4).
 *
 * Everything here comes from GET /api/projects/{id}/metrics. The old
 * dashboard.html read "Loaded Images", "Total Annotations" and "Classes
 * Created" out of localStorage['image-annotation-mvp-v1'], so the numbers
 * described whatever was last open in the canvas rather than the project.
 * These are server-side counts.
 */
import { apiFetch } from "../../api.js?v=1";
import { escapeHTML, formatTime } from "../../utils.js?v=1";

let abortController = null;

function tile({ label, value, sub, href }) {
  const inner = `
    <p class="label">${escapeHTML(label)}</p>
    <p class="value">${escapeHTML(value)}</p>
    ${sub ? `<p class="sub">${escapeHTML(sub)}</p>` : ""}`;
  return href
    ? `<a class="metric-tile" href="${href}">${inner}</a>`
    : `<div class="metric-tile">${inner}</div>`;
}

function render(root, project, m) {
  const total = m.total || 0;
  const completed = m.completed || 0;
  const remaining = Math.max(0, total - completed);

  root.innerHTML = `
    <div class="mgmt-title-row">
      <div>
        <p class="mgmt-eyebrow">Overview</p>
        <h2>${escapeHTML(project?.name || "Project")}</h2>
      </div>
    </div>

    <div class="metric-tile" style="margin-bottom: 18px;">
      <p class="label">Completion</p>
      <div class="progress-cell" style="margin-top: 6px;">
        <div class="progress-track" style="height: 10px;">
          <div class="progress-fill" style="width:${m.progress || 0}%"></div>
        </div>
        <span style="font-weight: 800; font-size: 1.1rem;">${m.progress || 0}%</span>
      </div>
      <p class="sub">${completed} of ${total} task${total === 1 ? "" : "s"} completed${remaining ? ` · ${remaining} remaining` : ""}</p>
    </div>

    <div class="metric-grid">
      ${tile({ label: "Total tasks", value: total, sub: "Images in this project", href: "#/tasks" })}
      ${tile({ label: "Completed", value: completed, href: "#/tasks" })}
      ${tile({ label: "In progress", value: m.in_progress || 0, href: "#/tasks" })}
      ${tile({ label: "Total classes", value: m.classes || 0, sub: "Labels available to every task", href: "#/classes" })}
      ${tile({ label: "Comments", value: m.comments || 0 })}
      ${tile({ label: "Time logged", value: formatTime(m.total_time || 0), sub: "Across all tasks" })}
      ${tile({ label: "Avg per task", value: formatTime(m.avg_time_per_task || 0) })}
      ${tile({ label: "Status", value: m.status || project?.status || "New" })}
    </div>

    ${total === 0 ? `
      <div class="mgmt-empty">
        <p>This project has no tasks yet.</p>
        <p><a class="cell-link" href="#/tasks">Upload images to get started →</a></p>
      </div>` : ""}
  `;
}

export async function mount(root, ctx) {
  abortController = new AbortController();
  root.innerHTML = `<div class="mgmt-empty">Loading metrics…</div>`;

  try {
    const res = await apiFetch(`/api/projects/${encodeURIComponent(ctx.projectId)}/metrics`, {
      signal: abortController.signal,
    });
    if (!res) return;
    if (!res.ok) {
      root.innerHTML = `<div class="mgmt-error">Could not load metrics (${res.status}).</div>`;
      return;
    }
    render(root, ctx.project, await res.json());
  } catch (err) {
    if (err.name === "AbortError") return; // navigated away mid-request
    console.error("Failed to load metrics", err);
    root.innerHTML = `<div class="mgmt-error">Could not load metrics.</div>`;
  }
}

export function unmount() {
  // Stop an in-flight fetch so a slow response cannot paint over the next view.
  abortController?.abort();
  abortController = null;
}

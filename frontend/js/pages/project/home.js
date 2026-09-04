/**
 * Home: the project metrics page (tracker P2.4).
 *
 * Everything here comes from GET /api/projects/{id}/metrics. The old
 * dashboard.html read "Loaded Images", "Total Annotations" and "Classes
 * Created" out of localStorage['image-annotation-mvp-v1'], so the numbers
 * described whatever was last open in the canvas rather than the project.
 * These are server-side counts.
 */
import { apiFetch } from "../../api.js?v=5";
import { escapeHTML, formatTime } from "../../utils.js?v=1";
import { APPROVED_STATUSES, statusClass } from "../../task-status.js?v=3";

let abortController = null;

/**
 * The tasks-view link for one status filter.
 *
 * Built through URLSearchParams rather than string concatenation so a status
 * containing a space ('In Progress') or an ampersand cannot break the hash. The
 * receiving end is tasks.js::readViewStateFromHash, which already reads
 * `status` and pre-selects #statusFilter from it.
 */
export function tasksHref(status) {
  if (!status) return "#/tasks";
  return `#/tasks?${new URLSearchParams({ status })}`;
}

/**
 * The status tiles, in display order — pure, so tests/js/dashboard_tiles_spec.mjs
 * can assert the counts and links without a DOM.
 *
 * The approval tiles are *derived* from APPROVED_STATUSES rather than listed by
 * name. 'Approved', 'Verified', 'Checked' and 'Passed' are one group of
 * synonyms differing only in which export batch a sign-off belongs to, and the
 * dashboard used to collapse them into a single count — hiding exactly the
 * distinction the batch names exist to make. Deriving the list also keeps
 * rule 11a's promise: adding a batch status stays one line in schemas.py plus
 * one in task-status.js, and a tile appears here for free. Hard-coding four
 * names would make this a third place to edit, and a silently wrong one.
 */
export function statusTiles(metrics) {
  const by = metrics?.by_status || {};
  // A missing key means an older server (or a status with no tasks); either way
  // the tile shows 0, never `undefined`.
  const count = (status) => by[status] ?? 0;
  // Named `entry`, not `tile`: `tile()` below is the HTML renderer, and these
  // are the data it renders.
  const entry = (status, label, sub) => ({
    status,
    label: label || status,
    value: count(status),
    sub,
    href: tasksHref(status),
    // The tile wears the same colour as the status pill everywhere else, via
    // the one modifier function (task-status.js::statusClass) rather than a
    // second mapping that could drift. Rendering only — the tile's meaning is
    // its `status`, never its class.
    // 'New' has no pill modifier of its own; the dashboard still gives it a
    // neutral slate accent so no tile in the grid reads as unstyled.
    cls: statusClass(status) || "is-new",
  });

  return [
    ...APPROVED_STATUSES.map((s) => entry(s, s, "Signed off by a reviewer")),
    entry("Completed", "Awaiting review", "Marked complete, not yet approved"),
    entry("In Progress", "In progress"),
    entry("New", "New", "Not started"),
    entry("Rejected", "Rejected", "Sent back for rework"),
  ];
}

function tile({ label, value, sub, href, cls }) {
  // `cls` comes from statusClass, so it is one of a fixed set of literals and
  // never user text; the tile's own strings are still escaped.
  const klass = `metric-tile${cls ? ` status-tile ${cls}` : ""}`;
  const inner = `
    <p class="label">${escapeHTML(label)}</p>
    <p class="value">${escapeHTML(value)}</p>
    ${sub ? `<p class="sub">${escapeHTML(sub)}</p>` : ""}`;
  return href
    ? `<a class="${klass}" href="${href}">${inner}</a>`
    : `<div class="${klass}">${inner}</div>`;
}

function render(root, project, m) {
  const total = m.total || 0;
  // `completed` is the *approved* count — tasks a reviewer signed off under any
  // batch status (Approved / Verified / Checked / …), not tasks an annotator
  // merely marked 'Completed'. The completion bar stays a *group* question, so
  // it keeps using this total; the grid below splits the group into one tile
  // per export batch, plus a tile for the 'Completed' review queue.
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
      <p class="sub">${completed} of ${total} task${total === 1 ? "" : "s"} approved${remaining ? ` · ${remaining} remaining` : ""}</p>
    </div>

    <div class="metric-grid">
      ${tile({ label: "Total tasks", value: total, sub: "Images in this project", href: "#/tasks" })}
      ${statusTiles(m).map((t) => tile(t)).join("")}
      ${tile({ label: "Total classes", value: m.classes || 0, sub: "Labels available to every task", href: "#/classes" })}
      ${tile({ label: "Comments", value: m.comments || 0 })}
      ${tile({ label: "Time logged", value: formatTime(m.total_time || 0), sub: "Across all tasks" })}
      ${tile({ label: "Avg per task", value: formatTime(m.avg_time_per_task || 0) })}
      ${(() => {
        // The project's stored status — the same value the projects list and
        // the shell's nav pill show. `m.status` now carries it too (the metrics
        // endpoint reports the derivation separately as `derived_status`), but
        // `project` is preferred because a cached bundle can be talking to a
        // server that still collapses the two. The derived value is already
        // represented on this page by the completion bar.
        const st = project?.status || m.status || "New";
        return tile({ label: "Status", value: st, cls: statusClass(st) || "is-new" });
      })()}
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

/**
 * `#/projects` — the projects this team can reach (04_UI_UX.md § 5.2).
 *
 * Read-only for everyone, including the team owner. Grants are managed from the
 * *project* side by the project owner (03_API.md § 3), so there is deliberately
 * no edit control here — the copy says "reach" rather than "permissions" to
 * avoid implying otherwise.
 */
import { apiFetch } from "../../api.js?v=1";
import { escapeHTML } from "../../utils.js?v=1";
import { createDataTable } from "../../components/data-table.js?v=1";
import { roleBadge } from "../../components/role-badge.js?v=1";

let ctx = null;
let table = null;

function shellHTML() {
  return `
    <div class="view-header">
      <h3>Projects</h3>
      <p class="muted">What this team can work on. Managed by each project's owner.</p>
    </div>
    <div data-role="table"></div>`;
}

async function load(container) {
  const res = await apiFetch(`/api/teams/${encodeURIComponent(ctx.teamId)}/projects`);
  if (!res || !res.ok) {
    container.querySelector("[data-role='table']").innerHTML =
      `<div class="mgmt-error">Could not load this team's projects.</div>`;
    return;
  }
  table.setRows(await res.json());
}

export async function mount(container, context) {
  ctx = context;
  container.innerHTML = shellHTML();

  table = createDataTable({
    mount: container.querySelector("[data-role='table']"),
    rowId: (row) => row.project_id,
    sortKey: "name",
    emptyMessage:
      "No projects yet. A project owner needs to grant this team access from their project's Access tab.",
    matches: (row, q) => String(row.name || "").toLowerCase().includes(q),
    columns: [
      {
        key: "name",
        label: "Project",
        render: (row) =>
          `<a class="cell-link" href="project.html?id=${encodeURIComponent(row.project_id)}#/home">${escapeHTML(row.name || "Untitled")}</a>`,
      },
      {
        key: "role",
        label: "This team's role",
        // The grant vocabulary (viewer/annotator/reviewer/manager), not the
        // team vocabulary — hence roleBadge rather than teamRoleBadge.
        render: (row) => roleBadge(row.role),
      },
      { key: "slug", label: "Slug", render: (row) => escapeHTML(row.slug || "—") },
    ],
  });

  await load(container);
}

export function unmount() {
  ctx = null;
  table = null;
}

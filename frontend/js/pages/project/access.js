/**
 * `#/access` — grant management (04_UI_UX.md § 6.2). Owner only.
 *
 * Granting is owner-only rather than manager+ (03_API.md § 3): a manager who
 * could grant could grant their own team `manager` elsewhere. The router keeps
 * everyone else out; this view assumes it has an owner.
 */
import { apiFetch } from "../../api.js?v=2";
import { escapeHTML } from "../../utils.js?v=1";
import { createDataTable } from "../../components/data-table.js?v=2";
import { fetchMyTeams, fillTeamSelect } from "../../components/team-picker.js?v=1";

const GRANT_ROLES = [
  ["viewer", "Viewer — read only"],
  ["annotator", "Annotator — can label"],
  ["reviewer", "Reviewer — can approve"],
  ["manager", "Manager — can administer"],
];

let ctx = null;
let table = null;
let root = null;
let myTeams = [];

function notice(message, kind = "info") {
  const box = root?.querySelector("[data-role='notice']");
  if (!box) return;
  box.className = kind === "error" ? "mgmt-error" : "mgmt-notice";
  box.textContent = message;
  box.style.display = message ? "block" : "none";
}

function roleOptions(selected) {
  return GRANT_ROLES.map(
    ([value, label]) =>
      `<option value="${value}"${value === selected ? " selected" : ""}>${escapeHTML(label)}</option>`
  ).join("");
}

function shellHTML() {
  return `
    <div class="view-header">
      <h3>Access</h3>
      <p class="muted">Teams that can work on this project.</p>
    </div>

    <div data-role="notice" style="display:none;"></div>
    <div data-role="table"></div>

    <section class="access-grant">
      <h4>Grant access</h4>
      <div data-role="grant-form"></div>
    </section>

    <p class="muted access-owner-note">
      You are the project owner. Ownership is not a grant and cannot be revoked here.
    </p>`;
}

function grantFormHTML() {
  // You may only grant to a team you belong to (E-16), so an owner with no
  // teams has nothing to offer — say what to do rather than showing an empty
  // select.
  if (!myTeams.length) {
    return `<p class="muted">
      You need a team first. <a class="cell-link" href="teams.html">Create one on the Teams page</a>,
      add members, then grant it access here.
    </p>`;
  }
  return `
    <div class="access-grant-row">
      <select data-role="grant-team" aria-label="Team"></select>
      <select data-role="grant-role" aria-label="Role">${roleOptions("annotator")}</select>
      <button class="primary" data-role="grant">Grant access</button>
    </div>`;
}

// --- data ------------------------------------------------------------------

async function load() {
  const res = await apiFetch(`/api/projects/${encodeURIComponent(ctx.projectId)}/grants`);
  if (!res || !res.ok) {
    notice("Could not load this project's access list.", "error");
    return;
  }
  table.setRows(await res.json());
}

// --- actions ---------------------------------------------------------------

async function changeRole(teamId, role) {
  const res = await apiFetch(
    `/api/projects/${encodeURIComponent(ctx.projectId)}/grants/${encodeURIComponent(teamId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
    }
  );
  if (!res) return;
  if (!res.ok) {
    notice(`Could not change that role (${res.status}).`, "error");
    await load(); // resync the select with what the server actually holds
    return;
  }
  // Inline confirmation rather than a save button: a single-field row commits
  // on change, the same pattern classes.js uses.
  notice("Saved.");
}

async function grant() {
  const teamId = root.querySelector("[data-role='grant-team']")?.value;
  const role = root.querySelector("[data-role='grant-role']")?.value;
  if (!teamId) return;

  const res = await apiFetch(`/api/projects/${encodeURIComponent(ctx.projectId)}/grants`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ team_id: Number(teamId), role }),
  });
  if (!res) return;
  if (!res.ok) {
    notice(`Could not grant access (${res.status}).`, "error");
    return;
  }
  const saved = await res.json();
  notice(`${saved.team_name} can now work on this project as ${saved.role}.`);
  await load();
}

async function revoke(row) {
  // The consequence is stated in counts before the click, not discovered after
  // it: revoking also returns that team's tasks to the unassigned pool (E-08).
  const confirmed = confirm(
    `Members of ${row.team_name} will lose access to this project.\n\n` +
    `Any tasks assigned to this team will return to the unassigned pool. ` +
    `Annotations are not deleted.`
  );
  if (!confirmed) return;

  const res = await apiFetch(
    `/api/projects/${encodeURIComponent(ctx.projectId)}/grants/${encodeURIComponent(row.team_id)}`,
    { method: "DELETE" }
  );
  if (!res) return;
  if (!res.ok) {
    notice(`Could not revoke access (${res.status}).`, "error");
    return;
  }
  const result = await res.json();
  notice(
    result.tasks_unassigned
      ? `${row.team_name} no longer has access. ${result.tasks_unassigned} task(s) returned to the unassigned pool.`
      : `${row.team_name} no longer has access.`
  );
  await load();
}

// --- lifecycle -------------------------------------------------------------

export async function mount(container, context) {
  ctx = context;
  root = container;
  container.innerHTML = shellHTML();

  table = createDataTable({
    mount: container.querySelector("[data-role='table']"),
    rowId: (row) => row.team_id,
    sortKey: "team_name",
    emptyMessage: "No teams have access yet. Only you can see this project.",
    matches: (row, q) => String(row.team_name || "").toLowerCase().includes(q),
    columns: [
      { key: "team_name", label: "Team", render: (row) => escapeHTML(row.team_name || "—") },
      {
        key: "role",
        label: "Role",
        render: (row) =>
          `<select class="inline-select" data-action="role" data-id="${escapeHTML(row.team_id)}">
             ${roleOptions(row.role)}
           </select>`,
      },
      {
        key: "actions",
        label: "",
        sortable: false,
        align: "right",
        render: () => `<button class="icon-button" data-action="revoke" title="Revoke access">✕</button>`,
      },
    ],
  });

  table.onAction("revoke", revoke);

  // Selects are re-rendered on every table paint, so delegate rather than
  // binding per element.
  container.addEventListener("change", (e) => {
    const select = e.target.closest("[data-action='role']");
    if (select) changeRole(select.dataset.id, select.value);
  });

  myTeams = await fetchMyTeams();
  const formHolder = container.querySelector("[data-role='grant-form']");
  formHolder.innerHTML = grantFormHTML();
  const teamSelect = formHolder.querySelector("[data-role='grant-team']");
  if (teamSelect) {
    fillTeamSelect(teamSelect, myTeams);
    formHolder.querySelector("[data-role='grant']")?.addEventListener("click", grant);
  }

  await load();
}

export function unmount() {
  ctx = null;
  table = null;
  root = null;
  myTeams = [];
}

/**
 * Level 1: the teams list (04_UI_UX.md § 4).
 *
 * Any user may create a team (01_DESIGN.md § 5), so `+ New team` is always
 * visible — there is no role that hides it.
 */
import { apiFetch } from "../api.js?v=2";
import { escapeHTML } from "../utils.js?v=1";
import { createDataTable } from "../components/data-table.js?v=3";
import { createModal } from "../components/modal.js?v=1";
import { teamRoleBadge } from "../components/role-badge.js?v=1";
import { canManageTeam, ownsTeam } from "../permissions.js?v=1";

const els = {
  error: document.getElementById("errorBanner"),
  search: document.getElementById("searchInput"),
  mount: document.getElementById("tableMount"),
  newBtn: document.getElementById("newTeamBtn"),
  modal: document.getElementById("teamModal"),
  modalClose: document.getElementById("teamModalClose"),
  modalCancel: document.getElementById("teamModalCancel"),
  form: document.getElementById("teamForm"),
  fName: document.getElementById("teamFormName"),
  fDescription: document.getElementById("teamFormDescription"),
};

let currentUser = null;
let modal = null;

function showError(message) {
  els.error.textContent = message;
  els.error.style.display = "block";
}

function clearError() {
  els.error.style.display = "none";
}

function formatDate(value) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString();
}

/**
 * The empty state is the entire onboarding path for a brand-new user (E-30).
 *
 * Showing them their own username is the single most useful thing on this
 * screen: it is exactly the string they have to hand to a manager to be added
 * to a team. Without it they have to go hunting for it.
 */
function emptyStateHTML() {
  const username = currentUser?.username;
  const askLine = username
    ? `or ask a team manager to add you by your username
       (<code class="inline-code">${escapeHTML(username)}</code>).`
    : "or ask a team manager to add you by your username.";
  return `
    <div class="mgmt-empty mgmt-empty-block">
      <p><strong>You're not in any team yet.</strong></p>
      <p>Create one to group annotators and grant them access to your
         projects — ${askLine}</p>
      <p class="muted">Teams are how project owners share work:
         a project owner grants a <em>team</em> access, not individuals.</p>
    </div>`;
}

const table = createDataTable({
  mount: els.mount,
  rowId: (row) => row.id,
  sortKey: "name",
  emptyMessage: "No teams match your search.",
  matches: (row, q) =>
    String(row.name || "").toLowerCase().includes(q) ||
    String(row.description || "").toLowerCase().includes(q),
  columns: [
    {
      key: "name",
      label: "Team",
      render: (row) =>
        `<a class="cell-link" href="teams.html?id=${encodeURIComponent(row.id)}#/members">${escapeHTML(row.name)}</a>`,
    },
    { key: "my_role", label: "Your role", render: (row) => teamRoleBadge(row.my_role) },
    { key: "member_count", label: "Members", align: "right" },
    { key: "project_count", label: "Projects", align: "right" },
    { key: "created_at", label: "Created", render: (row) => escapeHTML(formatDate(row.created_at)) },
    {
      key: "actions",
      label: "",
      sortable: false,
      align: "right",
      render: (row) => {
        // Levelled per row: the caller's role differs from team to team, so
        // this cannot be decided once for the whole table.
        const buttons = [];
        if (ownsTeam(row.my_role)) {
          buttons.push(
            `<button class="icon-button" data-action="delete" title="Delete team">🗑</button>`
          );
        } else {
          buttons.push(
            `<button class="icon-button" data-action="leave" title="Leave team">🚪</button>`
          );
        }
        if (canManageTeam(row.my_role)) {
          buttons.unshift(
            `<a class="icon-button" href="teams.html?id=${encodeURIComponent(row.id)}#/settings" title="Team settings">⚙️</a>`
          );
        }
        return buttons.join(" ");
      },
    },
  ],
});

async function load() {
  clearError();
  const res = await apiFetch("/api/teams");
  if (!res) return; // apiFetch redirected to login
  if (!res.ok) {
    showError(`Could not load your teams (${res.status}).`);
    return;
  }
  const teams = await res.json();

  if (!teams.length) {
    els.mount.innerHTML = emptyStateHTML();
    return;
  }
  table.setRows(teams);
}

// --- actions ---------------------------------------------------------------

async function createTeam(event) {
  event.preventDefault();
  clearError();

  const res = await apiFetch("/api/teams", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: els.fName.value.trim(),
      description: els.fDescription.value.trim() || null,
    }),
  });
  if (!res) return;

  if (res.status === 409) {
    const body = await res.json().catch(() => null);
    showError(body?.detail || "You have reached the maximum number of teams.");
    modal.close();
    return;
  }
  if (!res.ok) {
    showError(`Could not create the team (${res.status}).`);
    return;
  }

  // Straight into the roster: adding people is the obvious next step, so
  // landing anywhere else would just make them navigate again.
  const team = await res.json();
  window.location.href = `teams.html?id=${encodeURIComponent(team.id)}#/members`;
}

async function leaveTeam(row) {
  if (!confirm(`Leave "${row.name}"? You will lose access to its projects.`)) return;

  const res = await apiFetch(`/api/teams/${encodeURIComponent(row.id)}/members/me`, {
    method: "DELETE",
  });
  if (!res) return;
  if (res.status === 409) {
    // The owner must transfer first — a team always has exactly one owner.
    const body = await res.json().catch(() => null);
    showError(body?.detail || "Transfer ownership before leaving this team.");
    return;
  }
  if (!res.ok) {
    showError(`Could not leave the team (${res.status}).`);
    return;
  }
  await load();
}

async function deleteTeam(row) {
  // Deletion is confirmed by typing the slug, matching the server's ?confirm=
  // requirement (E-06). The settings view states the consequences in counts;
  // this shortcut restates the essentials rather than pretending it is trivial.
  const typed = prompt(
    `Deleting "${row.name}" removes it from ${row.project_count} project(s) and ` +
    `returns its tasks to the unassigned pool. Annotations are not deleted.\n\n` +
    `Type the team's slug to confirm:`
  );
  if (typed == null) return;

  const res = await apiFetch(
    `/api/teams/${encodeURIComponent(row.id)}?confirm=${encodeURIComponent(typed.trim())}`,
    { method: "DELETE" }
  );
  if (!res) return;
  if (res.status === 400) {
    showError("That did not match the team's slug. Nothing was deleted.");
    return;
  }
  if (!res.ok) {
    showError(`Could not delete the team (${res.status}).`);
    return;
  }
  await load();
}

// --- init ------------------------------------------------------------------

export async function start(user) {
  currentUser = user;

  modal = createModal(els.modal, {
    closeButton: els.modalClose,
    focusOnOpen: els.fName,
    onClose: () => els.form.reset(),
  });
  els.modalCancel.addEventListener("click", () => modal.close());
  els.newBtn.addEventListener("click", () => modal.open());
  els.form.addEventListener("submit", createTeam);

  els.search.addEventListener("input", () => table.setQuery(els.search.value));

  table.onAction("leave", leaveTeam);
  table.onAction("delete", deleteTeam);

  await load();
}

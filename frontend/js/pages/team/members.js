/**
 * `#/members` — the team roster (04_UI_UX.md § 5.1).
 *
 * The add-member modal is the interaction users hit hardest, and its failure
 * mode matters more than its success one: a mistyped username must leave the
 * modal open with the value intact so it can be corrected.
 */
import { apiFetch } from "../../api.js?v=5";
import { escapeHTML } from "../../utils.js?v=1";
import { createDataTable } from "../../components/data-table.js?v=4";
import { createModal, setFieldError, clearFieldError } from "../../components/modal.js?v=1";
import { teamRoleBadge } from "../../components/role-badge.js?v=1";
import { canManageTeam, ownsTeam } from "../../permissions.js?v=1";

const els = {
  modal: document.getElementById("memberModal"),
  modalClose: document.getElementById("memberModalClose"),
  modalCancel: document.getElementById("memberModalCancel"),
  form: document.getElementById("memberForm"),
  fUsername: document.getElementById("memberFormUsername"),
  fRole: document.getElementById("memberFormRole"),
};

let ctx = null;
let table = null;
let modal = null;
let root = null;

function formatDate(value) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleDateString();
}

function notice(message, kind = "info") {
  const box = root?.querySelector("[data-role='notice']");
  if (!box) return;
  box.className = kind === "error" ? "mgmt-error" : "mgmt-notice";
  box.textContent = message;
  box.style.display = message ? "block" : "none";
}

// --- rendering -------------------------------------------------------------

function columns() {
  const iAmManager = canManageTeam(ctx.myRole);

  return [
    {
      key: "username",
      label: "Username",
      render: (row) => {
        const isMe = row.user_id === ctx.currentUser?.id;
        return `${escapeHTML(row.username)}${isMe ? ` <span class="muted">(you)</span>` : ""}`;
      },
    },
    {
      key: "role",
      label: "Role",
      render: (row) => {
        // The owner's role is never a dropdown: ownership moves by transfer, so
        // offering it here would promise something the API refuses.
        if (row.role === "owner" || !iAmManager) return teamRoleBadge(row.role);
        return `
          <select class="inline-select" data-action="role" data-id="${escapeHTML(row.user_id)}">
            <option value="member"${row.role === "member" ? " selected" : ""}>Member</option>
            <option value="manager"${row.role === "manager" ? " selected" : ""}>Manager</option>
          </select>`;
      },
    },
    { key: "added_by", label: "Added by", render: (row) => escapeHTML(row.added_by_username || "—") },
    { key: "created_at", label: "Joined", render: (row) => escapeHTML(formatDate(row.created_at)) },
    {
      key: "actions",
      label: "",
      sortable: false,
      align: "right",
      render: (row) => {
        const isMe = row.user_id === ctx.currentUser?.id;
        // The owner is never removable (a team always has exactly one owner);
        // everyone else can remove themselves, and a manager can remove others.
        if (row.role === "owner") return "";
        if (isMe) return `<button class="icon-button" data-action="leave" title="Leave team">🚪</button>`;
        if (!iAmManager) return "";
        return `<button class="icon-button" data-action="remove" title="Remove from team">✕</button>`;
      },
    },
  ];
}

function shellHTML() {
  const iAmManager = canManageTeam(ctx.myRole);
  return `
    <div class="view-header">
      <h3>Members</h3>
      ${iAmManager ? `<button class="primary" data-role="add">+ Add member</button>` : ""}
    </div>
    <div data-role="notice" style="display:none;"></div>
    <div data-role="table"></div>`;
}

// --- data ------------------------------------------------------------------

async function load() {
  const res = await apiFetch(`/api/teams/${encodeURIComponent(ctx.teamId)}/members`);
  if (!res || !res.ok) {
    notice("Could not load the roster.", "error");
    return;
  }
  const members = await res.json();

  // `added_by` is a user id; resolve it against the roster we already have so
  // the column reads as a name without a second request. Someone who has since
  // left the team cannot be resolved, and falls back to "—".
  const byId = new Map(members.map((m) => [m.user_id, m.username]));
  for (const m of members) m.added_by_username = byId.get(m.added_by) || null;

  table.setRows(members);
}

// --- actions ---------------------------------------------------------------

async function addMember(event) {
  event.preventDefault();
  clearFieldError(els.fUsername);

  const username = els.fUsername.value.trim();
  const res = await apiFetch(`/api/teams/${encodeURIComponent(ctx.teamId)}/members`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, role: els.fRole.value }),
  });
  if (!res) return;

  if (res.status === 404) {
    // Inline, not a toast: the modal stays open with the typed value intact so
    // a typo can be fixed without retyping (04_UI_UX.md § 5.1).
    setFieldError(
      els.fUsername,
      `No user named '${username}'. Usernames are case-sensitive.`
    );
    return;
  }
  if (res.status === 429) {
    setFieldError(els.fUsername, "Too many members added too quickly. Try again shortly.");
    return;
  }
  if (res.status === 403) {
    setFieldError(els.fUsername, "You cannot grant a role above your own.");
    return;
  }
  if (!res.ok) {
    setFieldError(els.fUsername, `Could not add that member (${res.status}).`);
    return;
  }

  const member = await res.json();
  modal.close();
  await load();
  // A double-add is an idempotent success (E-01), so say what actually
  // happened rather than claiming a new member was added.
  const existing = table.getRows().filter((r) => r.user_id === member.user_id).length > 1;
  notice(
    existing || member.role !== els.fRole.value
      ? `${member.username} is already a member — role unchanged (${member.role}).`
      : `${member.username} added as ${member.role}.`
  );
}

async function changeRole(userId, role) {
  const res = await apiFetch(
    `/api/teams/${encodeURIComponent(ctx.teamId)}/members/${encodeURIComponent(userId)}`,
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
  notice("Role updated.");
  await load();
}

async function removeMember(row) {
  if (!confirm(`Remove ${row.username} from this team?`)) return;
  const res = await apiFetch(
    `/api/teams/${encodeURIComponent(ctx.teamId)}/members/${encodeURIComponent(row.user_id)}`,
    { method: "DELETE" }
  );
  if (!res) return;
  if (!res.ok) {
    notice(`Could not remove that member (${res.status}).`, "error");
    return;
  }
  notice(`${row.username} removed.`);
  await load();
}

async function leaveTeam() {
  if (!confirm("Leave this team? You will lose access to its projects.")) return;
  const res = await apiFetch(`/api/teams/${encodeURIComponent(ctx.teamId)}/members/me`, {
    method: "DELETE",
  });
  if (!res) return;
  if (res.status === 409) {
    const body = await res.json().catch(() => null);
    notice(body?.detail || "Transfer ownership before leaving this team.", "error");
    return;
  }
  if (!res.ok) {
    notice(`Could not leave the team (${res.status}).`, "error");
    return;
  }
  window.location.href = "teams.html";
}

// --- lifecycle -------------------------------------------------------------

export async function mount(container, context) {
  ctx = context;
  root = container;
  container.innerHTML = shellHTML();

  table = createDataTable({
    mount: container.querySelector("[data-role='table']"),
    rowId: (row) => row.user_id,
    sortKey: "username",
    columns: columns(),
    emptyMessage: "No members yet.",
    matches: (row, q) => String(row.username || "").toLowerCase().includes(q),
  });

  table.onAction("remove", removeMember);
  table.onAction("leave", leaveTeam);

  // Role selects are re-rendered on every table paint, so the change handler is
  // delegated from the container rather than bound per element.
  container.addEventListener("change", (e) => {
    const select = e.target.closest("[data-action='role']");
    if (select) changeRole(select.dataset.id, select.value);
  });

  if (canManageTeam(ctx.myRole)) {
    // The modal markup lives in teams.html, outside the view container, so its
    // listeners survive the innerHTML swap that tears the view down. Binding on
    // every mount would stack a second submit handler and fire two POSTs.
    // Built once, on first mount; `ctx` is read at call time so a later mount
    // still talks to the current team.
    if (!modal) {
      modal = createModal(els.modal, {
        closeButton: els.modalClose,
        focusOnOpen: els.fUsername,
        onClose: () => {
          els.form.reset();
          clearFieldError(els.fUsername);
        },
      });
      els.modalCancel.addEventListener("click", () => modal.close());
      els.form.addEventListener("submit", addMember);
    }
    // This button *is* inside the view, so it is a fresh element each mount and
    // must be bound each time.
    container
      .querySelector("[data-role='add']")
      ?.addEventListener("click", () => modal.open());
  }

  // NOTE: there is deliberately no username typeahead here, and no
  // `GET /api/users?q=` to build one from. Onboarding is by exact username so
  // the endpoint cannot be driven as a user-enumeration oracle
  // (01_DESIGN.md § 5.1, E-14). Please do not "improve" this into a search box.

  await load();
}

export function unmount() {
  // The modal is page-level, so it would otherwise stay open across a route
  // change and float above the next view.
  modal?.close();
  // `modal` itself is intentionally kept: its listeners are bound once (see
  // mount) and rebuilding it would re-bind them.
  ctx = null;
  table = null;
  root = null;
}

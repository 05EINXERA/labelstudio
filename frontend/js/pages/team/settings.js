/**
 * `#/settings` — rename, transfer ownership, delete (04_UI_UX.md § 5.3).
 *
 * Two privilege levels on one screen: rename is manager+, the danger zone is
 * owner-only. The router already keeps plain members out entirely; this view
 * gates the owner-only half again, because a manager can legitimately be here.
 */
import { apiFetch } from "../../api.js?v=1";
import { escapeHTML } from "../../utils.js?v=1";
import { ownsTeam } from "../../permissions.js?v=1";

let ctx = null;
let root = null;

function notice(message, kind = "info") {
  const box = root?.querySelector("[data-role='notice']");
  if (!box) return;
  box.className = kind === "error" ? "mgmt-error" : "mgmt-notice";
  box.textContent = message;
  box.style.display = message ? "block" : "none";
}

function shellHTML() {
  const team = ctx.team;
  const members = team.members || [];
  const others = members.filter((m) => m.user_id !== ctx.currentUser?.id);

  const dangerZone = ownsTeam(ctx.myRole)
    ? `
      <section class="danger-zone">
        <h3>Danger zone</h3>

        <div class="danger-row">
          <div>
            <strong>Transfer ownership</strong>
            <p class="muted">You become a manager. A team always has exactly one owner.</p>
          </div>
          ${
            others.length
              ? `<div class="danger-action">
                   <select data-role="transfer-target" aria-label="New owner">
                     ${others
                       .map(
                         (m) =>
                           `<option value="${escapeHTML(m.username)}">${escapeHTML(m.username)}</option>`
                       )
                       .join("")}
                   </select>
                   <button class="tool-button" data-role="transfer">Transfer</button>
                 </div>`
              : `<p class="muted">Add another member first.</p>`
          }
        </div>

        <div class="danger-row">
          <div>
            <strong>Delete this team</strong>
            <!-- Consequences stated in counts, not in the abstract (E-06): the
                 owner should know exactly what they are about to break. -->
            <p class="muted">
              ${team.project_count} project(s) will lose this team's access.
              Tasks assigned to this team return to the unassigned pool.
              <strong>Annotations are not deleted.</strong>
            </p>
          </div>
          <div class="danger-action">
            <input type="text" data-role="delete-confirm"
                   placeholder="Type '${escapeHTML(team.slug)}'" aria-label="Confirm team slug">
            <button class="danger-button" data-role="delete">Delete team</button>
          </div>
        </div>
      </section>`
    : `<p class="muted">Only the team owner can transfer ownership or delete this team.</p>`;

  return `
    <div class="view-header"><h3>Settings</h3></div>
    <div data-role="notice" style="display:none;"></div>

    <form data-role="details" class="settings-form">
      <div class="form-field">
        <label for="teamSettingsName">Team name</label>
        <input type="text" id="teamSettingsName" maxlength="120" required
               value="${escapeHTML(team.name || "")}">
      </div>
      <div class="form-field">
        <label for="teamSettingsDescription">Description</label>
        <input type="text" id="teamSettingsDescription" maxlength="500"
               value="${escapeHTML(team.description || "")}">
      </div>
      <p class="muted">Renaming also updates the team's URL slug
         (currently <code class="inline-code">${escapeHTML(team.slug)}</code>).</p>
      <button type="submit" class="primary">Save changes</button>
    </form>

    ${dangerZone}`;
}

// --- actions ---------------------------------------------------------------

async function saveDetails(event) {
  event.preventDefault();
  const res = await apiFetch(`/api/teams/${encodeURIComponent(ctx.teamId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      name: root.querySelector("#teamSettingsName").value.trim(),
      description: root.querySelector("#teamSettingsDescription").value.trim() || null,
    }),
  });
  if (!res) return;
  if (!res.ok) {
    notice(`Could not save changes (${res.status}).`, "error");
    return;
  }
  await ctx.reloadTeam();
  notice("Saved.");
  // Re-render so the slug hint and the delete confirmation reflect the new name.
  render(root);
}

async function transferOwnership() {
  const username = root.querySelector("[data-role='transfer-target']")?.value;
  if (!username) return;
  if (
    !confirm(
      `Transfer ownership of this team to ${username}? You will become a manager ` +
      `and will no longer be able to delete the team.`
    )
  ) {
    return;
  }

  const res = await apiFetch(`/api/teams/${encodeURIComponent(ctx.teamId)}/transfer`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username }),
  });
  if (!res) return;
  if (!res.ok) {
    notice(`Could not transfer ownership (${res.status}).`, "error");
    return;
  }
  // The caller is now a manager, so the danger zone must disappear. Reloading
  // the team refreshes ctx.myRole, and the router re-renders the badge.
  await ctx.reloadTeam();
  notice(`${username} is now the owner. You are a manager.`);
  render(root);
}

async function deleteTeam() {
  const typed = root.querySelector("[data-role='delete-confirm']")?.value.trim();
  if (!typed) {
    notice(`Type the team's slug ('${ctx.team.slug}') to confirm.`, "error");
    return;
  }

  const res = await apiFetch(
    `/api/teams/${encodeURIComponent(ctx.teamId)}?confirm=${encodeURIComponent(typed)}`,
    { method: "DELETE" }
  );
  if (!res) return;
  if (res.status === 400) {
    notice("That did not match the team's slug. Nothing was deleted.", "error");
    return;
  }
  if (!res.ok) {
    notice(`Could not delete the team (${res.status}).`, "error");
    return;
  }
  window.location.href = "teams.html";
}

// --- lifecycle -------------------------------------------------------------

function render(container) {
  container.innerHTML = shellHTML();
  container.querySelector("[data-role='details']")?.addEventListener("submit", saveDetails);
  container.querySelector("[data-role='transfer']")?.addEventListener("click", transferOwnership);
  container.querySelector("[data-role='delete']")?.addEventListener("click", deleteTeam);
}

export async function mount(container, context) {
  ctx = context;
  root = container;
  render(container);
}

export function unmount() {
  ctx = null;
  root = null;
}

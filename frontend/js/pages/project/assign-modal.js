/**
 * The task-assignment dialog: pick a team, then optionally a person in it.
 *
 * One module serving both entry points — the per-row ✋ action and the bulk bar
 * — because they ask the identical question and differ only in how many task
 * ids they apply the answer to. Two copies would drift.
 *
 * Replaces an earlier `prompt()` that asked the user to type a numeric team id
 * off a printed list. That was unusable with more than two teams and offered no
 * way to pick a person at all.
 *
 * Ordering matters and is enforced by the UI: **a member can only be chosen
 * once a team is chosen**, because the member list is the roster of the
 * selected team. Assigning a person from a team that has no grant would be
 * invisible work (E-09), which the server rejects with a 422 anyway.
 */
import { escapeHTML } from "../../utils.js?v=1";
// Shared with the canvas popover so the two pickers cannot disagree about who
// is in a team — see assign-options.js for the roster's dedup caveat.
import { membersForTeam } from "./assign-options.js?v=1";

let overlay = null;
let resolver = null;

function renderMemberOptions(members, teamId, selected) {
  const inTeam = membersForTeam(members, teamId);
  const options = [`<option value="">— Anyone in the team —</option>`];
  for (const m of inTeam) {
    const sel = String(m.user_id) === String(selected) ? " selected" : "";
    options.push(
      `<option value="${escapeHTML(m.user_id)}"${sel}>${escapeHTML(m.username)}</option>`
    );
  }
  return options.join("");
}

function template({ title, teams, members, current }) {
  const teamOptions = [`<option value="">— Unassigned (shared pool) —</option>`]
    .concat(
      teams.map((t) => {
        const sel = String(t.id) === String(current.teamId) ? " selected" : "";
        return `<option value="${escapeHTML(t.id)}"${sel}>${escapeHTML(t.name)}</option>`;
      })
    )
    .join("");

  return `
    <div class="modal-content">
      <div class="modal-header">
        <h2>${escapeHTML(title)}</h2>
        <button class="modal-close" data-role="close" type="button">&times;</button>
      </div>
      <div class="modal-body" style="display:grid; gap:14px;">
        <div class="form-field">
          <label for="assignTeamSelect">Team</label>
          <select id="assignTeamSelect" data-role="team">${teamOptions}</select>
          <p class="field-hint">Only teams granted access to this project are listed.</p>
        </div>
        <div class="form-field">
          <label for="assignMemberSelect">Assign to</label>
          <select id="assignMemberSelect" data-role="member">
            ${renderMemberOptions(members, current.teamId, current.userId)}
          </select>
          <p class="field-hint" data-role="member-hint">
            Leave as "anyone" to let the whole team work on it. Naming a person
            reserves it — others in the team will not be able to save changes.
          </p>
        </div>
      </div>
      <div class="modal-footer">
        <button type="button" class="tool-button" data-role="cancel">Cancel</button>
        <button type="button" class="primary" data-role="apply">Apply</button>
      </div>
    </div>`;
}

function close(result) {
  overlay?.classList.remove("is-active");
  const done = resolver;
  resolver = null;
  done?.(result);
}

/**
 * Open the dialog and resolve with the chosen assignment, or `null` if
 * cancelled.
 *
 * @param {object} opts
 * @param {string} opts.title
 * @param {Array<{id:number,name:string}>} opts.teams   granted teams
 * @param {Array<object>} opts.members                  assignable members
 * @param {{teamId?:any,userId?:any}} [opts.current]    pre-selection
 * @returns {Promise<{assigned_team_id:number|null, assignee_user_id:number|null}|null>}
 */
export function openAssignDialog({ title, teams, members, current = {} }) {
  // Built lazily and reused: the dialog is page-level, so creating it per call
  // would stack listeners the way a per-mount modal would.
  if (!overlay) {
    overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.id = "assignTaskModal";
    document.body.appendChild(overlay);

    overlay.addEventListener("click", (e) => {
      if (e.target === overlay) close(null);
      if (e.target.closest("[data-role='close'], [data-role='cancel']")) close(null);
      if (e.target.closest("[data-role='apply']")) {
        const teamId = overlay.querySelector("[data-role='team']").value;
        const userId = overlay.querySelector("[data-role='member']").value;
        close({
          assigned_team_id: teamId ? Number(teamId) : null,
          // Clearing the team clears the person too: a named assignee with no
          // team is a dangling reservation nobody can see in the Team column.
          assignee_user_id: teamId && userId ? Number(userId) : null,
        });
      }
    });

    // Changing the team repopulates the member list — the roster shown must
    // always belong to the team actually selected.
    overlay.addEventListener("change", (e) => {
      if (!e.target.closest("[data-role='team']")) return;
      const teamId = e.target.value;
      const memberSelect = overlay.querySelector("[data-role='member']");
      memberSelect.innerHTML = renderMemberOptions(overlay._members || [], teamId, null);
      memberSelect.disabled = !teamId;
    });

    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && overlay.classList.contains("is-active")) close(null);
    });
  }

  overlay._members = members;
  overlay.innerHTML = template({ title, teams, members, current });
  const memberSelect = overlay.querySelector("[data-role='member']");
  memberSelect.disabled = !current.teamId;

  overlay.classList.add("is-active");
  requestAnimationFrame(() => overlay.querySelector("[data-role='team']")?.focus());

  return new Promise((resolve) => {
    resolver = resolve;
  });
}

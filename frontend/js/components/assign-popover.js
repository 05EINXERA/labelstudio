/**
 * The canvas's task-assignment popover: a small panel anchored under the
 * toolbar's Assign button.
 *
 * ## Why not reuse the Tasks page's `assign-modal.js`
 *
 * Not a styling preference — three structural reasons:
 *
 *  1. **It is a page-level singleton.** It appends one overlay to `document.body`
 *     and keeps its selected-roster state on that element (`overlay._members`)
 *     with a module-scoped `resolver`. It serves the Tasks page's two entry
 *     points (row action, bulk bar), which ask the same question about *many*
 *     ids. Adding a third caller on a different page to that shared mutable
 *     state is how the Tasks page acquires a bug it did not have.
 *
 *  2. **A full-screen overlay is wrong here.** The manager is assigning *this*
 *     image, and deciding who gets it usually means looking at it. Covering the
 *     canvas to ask the question hides the reason for the answer.
 *
 *  3. **Only one task, always.** No bulk mode, no id list — so it can show the
 *     current assignee inline and offer Unassign directly, which the modal
 *     cannot do generically across a selection.
 *
 * The part that genuinely must not diverge — *who is in this team* — is shared
 * via `pages/project/assign-options.js`, imported by both.
 *
 * ## What this module is not
 *
 * It is a picker. It performs no fetch and no write: it resolves with the chosen
 * assignment and lets `canvas-assign.js` own the request. That keeps the
 * permission/refresh sequencing in one place rather than split across a UI
 * component.
 */
import { escapeHTML } from "../utils.js?v=1";
import {
  membersForTeam,
  describeAssignment,
} from "../pages/project/assign-options.js?v=1";

let host = null;
let resolver = null;
let roster = [];
/** The element focus returns to on close — the button that opened us. */
let opener = null;

function memberOptions(members, teamId, selected) {
  const options = [`<option value="">— Anyone in the team —</option>`];
  for (const m of membersForTeam(members, teamId)) {
    const sel = String(m.user_id) === String(selected) ? " selected" : "";
    options.push(
      `<option value="${escapeHTML(m.user_id)}"${sel}>${escapeHTML(m.username)}</option>`
    );
  }
  return options.join("");
}

function template({ teams, members, current, task }) {
  const teamOptions = [`<option value="">— Unassigned (shared pool) —</option>`]
    .concat(
      teams.map((t) => {
        const sel = String(t.id) === String(current.teamId) ? " selected" : "";
        return `<option value="${escapeHTML(t.id)}"${sel}>${escapeHTML(t.name)}</option>`;
      })
    )
    .join("");

  const now = describeAssignment(task, members);
  // The "currently assigned to" line is the reason this opens from an
  // "Assigned" button at all: the manager's first question is who has it, and
  // answering that should not require reading the two selects below.
  const currentLine =
    now.state === "none"
      ? `<span class="assign-pop-none">Not assigned to anyone yet</span>`
      : `Currently with <strong>${escapeHTML(now.label)}</strong>` +
        (now.state === "team" ? " (anyone in the team)" : "");

  // Unassign is only offered when there is something to clear. Showing it on an
  // already-unassigned task would be a control that cannot do anything.
  const unassign =
    now.state === "none"
      ? ""
      : `<button type="button" class="tool-button assign-pop-clear" data-role="unassign">Unassign</button>`;

  return `
    <div class="assign-pop-head">
      <h3 id="assignPopTitle">Assign this task</h3>
      <button class="assign-pop-close" data-role="close" type="button"
        aria-label="Close">&times;</button>
    </div>
    <p class="assign-pop-current">${currentLine}</p>
    <div class="assign-pop-body">
      <div class="form-field">
        <label for="assignPopTeam">Team</label>
        <select id="assignPopTeam" data-role="team">${teamOptions}</select>
      </div>
      <div class="form-field">
        <label for="assignPopMember">Person</label>
        <select id="assignPopMember" data-role="member">
          ${memberOptions(members, current.teamId, current.userId)}
        </select>
        <p class="field-hint">
          Leave as “anyone” to let the whole team work on it. Naming a person
          reserves it — others in the team cannot save changes.
        </p>
      </div>
    </div>
    <div class="assign-pop-foot">
      ${unassign}
      <span class="assign-pop-spacer"></span>
      <button type="button" class="tool-button" data-role="cancel">Cancel</button>
      <button type="button" class="primary" data-role="apply">Assign</button>
    </div>`;
}

function close(result) {
  if (!host) return;
  host.hidden = true;
  host.classList.remove("is-open");
  opener?.setAttribute("aria-expanded", "false");
  // Return focus to the button that opened the popover. Without this, closing
  // with Escape drops focus to <body> and keyboard users lose their place in
  // the toolbar.
  try { opener?.focus(); } catch { /* opener may have been re-rendered away */ }

  const done = resolver;
  resolver = null;
  done?.(result);
}

/** Close without resolving a choice. Used when the task changes underneath us. */
export function closeAssignPopover() {
  if (host && !host.hidden) close(null);
}

function onDocumentClick(e) {
  if (!host || host.hidden) return;
  if (host.contains(e.target)) return;
  if (opener && opener.contains(e.target)) return; // the opener toggles itself
  close(null);
}

function onKeydown(e) {
  if (!host || host.hidden) return;
  if (e.key === "Escape") {
    e.stopPropagation();
    close(null);
  }
}

function ensureHost() {
  if (host) return host;

  host = document.createElement("div");
  host.className = "assign-popover";
  host.id = "assignPopover";
  host.hidden = true;
  host.setAttribute("role", "dialog");
  host.setAttribute("aria-modal", "false");
  host.setAttribute("aria-labelledby", "assignPopTitle");
  document.body.appendChild(host);

  host.addEventListener("click", (e) => {
    if (e.target.closest("[data-role='close'], [data-role='cancel']")) {
      close(null);
      return;
    }

    if (e.target.closest("[data-role='unassign']")) {
      // Explicit nulls, not omitted fields: the endpoint distinguishes the two
      // via `model_fields_set`, and an omitted field means "leave alone".
      close({ assigned_team_id: null, assignee_user_id: null });
      return;
    }

    if (e.target.closest("[data-role='apply']")) {
      const teamId = host.querySelector("[data-role='team']").value;
      const userId = host.querySelector("[data-role='member']").value;
      close({
        assigned_team_id: teamId ? Number(teamId) : null,
        // Clearing the team clears the person too — a named assignee with no
        // team is a reservation that appears nowhere in the Team column.
        assignee_user_id: teamId && userId ? Number(userId) : null,
      });
    }
  });

  host.addEventListener("change", (e) => {
    if (!e.target.closest("[data-role='team']")) return;
    const teamId = e.target.value;
    const memberSelect = host.querySelector("[data-role='member']");
    // Reset the person when the team changes: the roster shown must belong to
    // the team actually selected, and keeping a stale selection would submit
    // someone the manager can no longer see in the list.
    memberSelect.innerHTML = memberOptions(roster, teamId, null);
    memberSelect.disabled = !teamId;
  });

  // Capture phase so Escape closes the popover before any canvas-level handler
  // treats it as "cancel the current drawing".
  document.addEventListener("keydown", onKeydown, true);
  document.addEventListener("click", onDocumentClick);

  return host;
}

/**
 * Position the popover under its anchor, kept inside the viewport.
 *
 * Right-aligned to the anchor because the button sits at the right end of the
 * toolbar; clamped so a narrow window cannot push the panel off-screen.
 */
function position(anchor) {
  const rect = anchor.getBoundingClientRect();
  const width = host.offsetWidth || 320;
  const gap = 6;

  let left = rect.right - width;
  const margin = 8;
  if (left < margin) left = margin;
  const maxLeft = window.innerWidth - width - margin;
  if (left > maxLeft) left = Math.max(margin, maxLeft);

  host.style.top = `${Math.round(rect.bottom + gap)}px`;
  host.style.left = `${Math.round(left)}px`;
}

/**
 * Open the popover under `anchor` and resolve with the chosen assignment, or
 * `null` if dismissed.
 *
 * @param {object} opts
 * @param {HTMLElement} opts.anchor                      button to hang under
 * @param {Array<{id:number,name:string}>} opts.teams    granted teams
 * @param {Array<object>} opts.members                   assignable roster
 * @param {object} opts.task                             the open gallery row
 * @returns {Promise<{assigned_team_id:number|null, assignee_user_id:number|null}|null>}
 */
export function openAssignPopover({ anchor, teams, members, task }) {
  ensureHost();

  // A second open while one is pending resolves the first as dismissed, so no
  // caller is left awaiting a promise that never settles.
  if (resolver) close(null);

  roster = members || [];
  opener = anchor;

  const current = {
    teamId: task?.assigned_team_id ?? "",
    userId: task?.assignee_user_id ?? "",
  };

  host.innerHTML = template({ teams, members: roster, current, task });
  const memberSelect = host.querySelector("[data-role='member']");
  memberSelect.disabled = !current.teamId;

  host.hidden = false;
  host.classList.add("is-open");
  anchor?.setAttribute("aria-expanded", "true");

  // Position after the panel is laid out — offsetWidth is 0 while hidden.
  position(anchor);
  requestAnimationFrame(() => {
    position(anchor);
    host.querySelector("[data-role='team']")?.focus();
  });

  return new Promise((resolve) => {
    resolver = resolve;
  });
}

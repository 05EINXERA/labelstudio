/**
 * Role pill renderer, shared by the teams list, the team roster, the project
 * header and the Access view.
 *
 * Returns an HTML string rather than a node because every caller feeds it into
 * a `data-table.js` column `render()`, which builds rows as HTML. The role is
 * escaped even though it comes from a fixed server vocabulary — it lands in
 * `innerHTML`, and "it can only be one of five values" is exactly the
 * assumption that stops being true later.
 */
import { escapeHTML } from "../utils.js?v=1";
import { roleLabel } from "../permissions.js?v=1";

/** Project-role pill. `role` may be null for "no access". */
export function roleBadge(role) {
  const cls = role ? `is-role-${escapeHTML(role)}` : "is-role-none";
  return `<span class="pill role-pill ${cls}">${escapeHTML(roleLabel(role))}</span>`;
}

/**
 * Team-role pill. Same vocabulary problem, different axis: a team `manager` and
 * a project `manager` are unrelated, so they get their own class prefix rather
 * than sharing colours by accident.
 */
export function teamRoleBadge(role) {
  const cls = role ? `is-team-${escapeHTML(role)}` : "is-role-none";
  return `<span class="pill role-pill ${cls}">${escapeHTML(roleLabel(role))}</span>`;
}

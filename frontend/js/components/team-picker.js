/**
 * A `<select>` of the caller's teams, reused by the Access view, the Tasks
 * team filter and bulk assign.
 *
 * Populated from `GET /api/teams`, which returns only teams the caller belongs
 * to. That is not merely a convenience: you may only grant to a team you are a
 * member of (E-16), so offering any other team would build a picker whose
 * options the API rejects.
 */
import { apiFetch } from "../api.js?v=5";
import { escapeHTML } from "../utils.js?v=1";

/** Fetch the caller's teams once. Returns [] on failure rather than throwing —
 *  a picker that cannot populate should degrade to empty, not break its page. */
export async function fetchMyTeams() {
  try {
    const res = await apiFetch("/api/teams");
    if (!res || !res.ok) return [];
    const teams = await res.json();
    return Array.isArray(teams) ? teams : [];
  } catch (err) {
    console.error("Could not load teams", err);
    return [];
  }
}

/**
 * Fill a `<select>` with team options.
 *
 * @param {HTMLSelectElement} select
 * @param {Array<{id:number,name:string}>} teams
 * @param {object} [opts]
 * @param {string} [opts.placeholder] leading option with an empty value
 * @param {Array<{value:string,label:string}>} [opts.extraOptions] e.g. Unassigned
 * @param {string|number} [opts.selected]
 */
export function fillTeamSelect(select, teams, opts = {}) {
  const { placeholder, extraOptions = [], selected } = opts;

  const options = [];
  if (placeholder) options.push(`<option value="">${escapeHTML(placeholder)}</option>`);
  for (const extra of extraOptions) {
    options.push(`<option value="${escapeHTML(extra.value)}">${escapeHTML(extra.label)}</option>`);
  }
  for (const team of teams) {
    options.push(`<option value="${escapeHTML(team.id)}">${escapeHTML(team.name)}</option>`);
  }

  select.innerHTML = options.join("");
  if (selected != null) select.value = String(selected);
}

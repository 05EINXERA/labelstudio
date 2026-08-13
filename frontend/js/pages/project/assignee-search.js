/**
 * Match the Tasks view's assignee-name search against the project roster.
 *
 * The roster (`/api/projects/{id}/assignable-members`) is already fetched on
 * mount and is bounded — ~20-25 annotators in the target deployment — so the
 * typed name is resolved to user ids *here*, on the client, and sent as the
 * existing `assignee` filter. That keeps `GET /api/tasks` free of a join
 * against `users` and costs no request per keystroke.
 *
 * Pure by design, like `objects-filter.js`: no DOM, no `state` import, no
 * fetch. The whole module is a string-to-ids function, which is what makes the
 * "matched nobody" case testable without a server.
 *
 * Known limit: only people currently on the roster can be matched. A task
 * assigned to someone since removed from every granted team is unreachable by
 * name (it already displays a stale assignee). Filtering by the dropdown still
 * finds it.
 */

/** Sent when a query matches nobody. `api/routers/tasks.py` maps it to a
 *  false() filter, i.e. an empty page. Must NOT be an empty string — dropping
 *  the filter would show every task, the exact opposite of "no matches". */
export const NO_MATCH = "none";

/**
 * Resolve a typed name fragment to an `assignee` filter value.
 *
 * @param {Array<{user_id:number, username:string}>} members  roster rows;
 *        the same person may appear once per granted team they belong to.
 * @param {string} query  raw text from the search box.
 * @returns {string|null} a comma-separated "user-<id>" list, NO_MATCH when
 *          nothing matched, or null when the query is blank — meaning "this
 *          control is not filtering", so the caller falls back to the dropdown.
 */
export function matchAssignees(members, query) {
  const needle = String(query ?? "").trim().toLowerCase();
  if (!needle) return null;

  // Deduplicate: someone in two granted teams is one person with one id, but
  // arrives as two rows (see test_assignable_members_deduplicates_across_teams).
  // Two identical ids in the IN clause would be harmless but sloppy, and would
  // make the generated URL depend on team membership.
  const ids = [];
  const seen = new Set();
  for (const member of members || []) {
    const id = member?.user_id;
    if (id == null || seen.has(id)) continue;
    if (!String(member.username ?? "").toLowerCase().includes(needle)) continue;
    seen.add(id);
    ids.push(id);
  }

  if (!ids.length) return NO_MATCH;
  return ids.map((id) => `user-${id}`).join(",");
}

/**
 * True when `value` is an assignee filter this search produced, rather than one
 * of the dropdown's own values ("All", "mine", "unassigned", a bare "user-<id>").
 *
 * Used on restore: a multi-id value must never be written into the <select>,
 * which has no matching <option> and would blank itself.
 */
export function isSearchFilterValue(value) {
  if (!value) return false;
  return value === NO_MATCH || value.includes(",");
}

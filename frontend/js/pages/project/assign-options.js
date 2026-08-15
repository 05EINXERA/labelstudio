/**
 * Building the team / member option lists for the assignment pickers.
 *
 * Pure by design, like `objects-filter.js` and `assignee-search.js`: no DOM, no
 * `state` import, no fetch. It exists because there are now **two** assignment
 * pickers — the Tasks page modal (`assign-modal.js`) and the canvas popover
 * (`components/assign-popover.js`) — and they must answer "who is in this team?"
 * identically. Two copies of that rule would drift, and the failure mode is
 * silent: a picker that quietly omits a person is indistinguishable from a
 * person who really cannot be assigned.
 *
 * ## The roster's shape, and its one sharp edge
 *
 * `GET /api/projects/{id}/assignable-members` returns one row per person, each
 * carrying the team they were *reached through*. It deduplicates on user id and
 * keeps the first team encountered (api/routers/grants.py — "which team is shown
 * is cosmetic").
 *
 * That makes `membersForTeam` narrower than it looks: someone who belongs to two
 * granted teams appears under only **one** of them, so selecting their other
 * team will not list them. This is pre-existing behaviour, shared by both
 * pickers, and it is deliberately preserved here rather than papered over —
 * changing it means changing the endpoint's contract, which the Tasks page and
 * the assignee-name search also read.
 *
 * It is not a correctness hole in the assignment itself: assigning a user who is
 * not in the chosen team is *allowed* server-side, with a warning (E-10,
 * `_validate_assignment`) — individual assignment is advisory. The only cost is
 * that this particular path cannot reach that combination.
 */

/**
 * The roster rows belonging to one team.
 *
 * Returns `[]` for a falsy team id rather than the whole roster: "no team
 * chosen" must offer nobody, because a person picked with no team is a dangling
 * reservation that shows up nowhere in the Team column.
 *
 * @param {Array<{user_id:number,username:string,team_id:number}>} members
 * @param {number|string|null|undefined} teamId
 * @returns {Array<object>}
 */
export function membersForTeam(members, teamId) {
  if (!teamId) return [];
  // String-compared throughout: `<select>.value` is always a string, while the
  // roster carries numbers. `==` would do it, but an explicit cast documents
  // that the mismatch is expected rather than accidental.
  return (members || []).filter((m) => String(m?.team_id) === String(teamId));
}

/**
 * Resolve the display name for an assigned user id, or null.
 *
 * Falls back to null (not "Unknown") when the id is absent from the roster —
 * that happens transiently after a member is removed from their team, and the
 * caller renders that case as unassigned rather than inventing a name.
 *
 * @param {Array<object>} members
 * @param {number|string|null} userId
 * @returns {string|null}
 */
export function usernameFor(members, userId) {
  if (userId == null) return null;
  const hit = (members || []).find((m) => String(m?.user_id) === String(userId));
  return hit?.username ?? null;
}

/**
 * Describe an assignment in one short phrase, for a button label or a header.
 *
 * The precedence — person, then team, then nothing — mirrors both the Tasks
 * page's Assignee column and the server's refusal messages
 * (`_require_assigned_team_membership`): the individual is the narrower claim
 * and the one that actually gates writing, so it is what a reader needs to see.
 *
 * @param {{assignee_user_id?:any, assignee_name?:string,
 *          assigned_team_id?:any, assigned_team_name?:string}} task
 * @param {Array<object>} [members] roster, used to recover a missing name
 * @returns {{state:'user'|'team'|'none', label:string}}
 */
export function describeAssignment(task, members = []) {
  if (!task) return { state: "none", label: "Unassigned" };

  if (task.assignee_user_id != null) {
    const name = task.assignee_name || usernameFor(members, task.assignee_user_id);
    // A named assignee whose name we cannot resolve means they were removed
    // from the team; the server clears the field, so this is a brief window
    // before the next refresh. Degrade to the team rather than showing an id.
    if (name) return { state: "user", label: name };
  }

  if (task.assigned_team_id != null) {
    const team = task.assigned_team_name;
    if (team) return { state: "team", label: team };
    return { state: "team", label: "a team" };
  }

  return { state: "none", label: "Unassigned" };
}

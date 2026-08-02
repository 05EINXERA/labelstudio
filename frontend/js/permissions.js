/**
 * Client-side role helpers.
 *
 * ⚠️ THIS IS A DELIBERATE MIRROR OF `api/permissions.py`. ⚠️
 *
 * The ranking below duplicates `_RANK` in that module. The duplication is
 * intentional: this project has no build step (rule 13), so there is no way to
 * share one definition between Python and the browser without adding a
 * toolchain the project explicitly refuses.
 *
 * **This copy is for rendering only. The server is the security boundary.**
 * Every helper here answers "should I draw this button?" — never "is this
 * allowed?". A stale bundle showing a control the API refuses is a cosmetic
 * bug (E-17); a server check removed because "the client already hides it" is
 * a vulnerability. Do not delete a server-side check on the strength of
 * anything in this file.
 *
 * If you change the ranking here, change `api/permissions.py` to match.
 */

/** Project roles, lowest to highest. Mirrors `ProjectRole` / `_RANK`. */
export const RANK = {
  viewer: 0,
  annotator: 1,
  reviewer: 2,
  manager: 3,
  owner: 4,
};

/** Team roles, a separate vocabulary. Mirrors `TeamRole` / `_TEAM_RANK`. */
export const TEAM_RANK = {
  member: 0,
  manager: 1,
  owner: 2,
};

/**
 * True when `role` is present and ranks at or above `minimum`.
 * A null/undefined/unknown role is always false — "no role" is not "viewer".
 */
export function atLeast(role, minimum) {
  const have = RANK[role];
  const need = RANK[minimum];
  if (have === undefined || need === undefined) return false;
  return have >= need;
}

export function canAnnotate(role) { return atLeast(role, "annotator"); }
export function canReview(role) { return atLeast(role, "reviewer"); }
export function canManage(role) { return atLeast(role, "manager"); }
export function isOwner(role) { return role === "owner"; }

/** The team-axis equivalent of `atLeast`. */
export function teamAtLeast(role, minimum) {
  const have = TEAM_RANK[role];
  const need = TEAM_RANK[minimum];
  if (have === undefined || need === undefined) return false;
  return have >= need;
}

export function canManageTeam(role) { return teamAtLeast(role, "manager"); }
export function ownsTeam(role) { return role === "owner"; }

/** Human-readable label for a role, for badges and messages. */
export function roleLabel(role) {
  if (!role) return "No access";
  return role.charAt(0).toUpperCase() + role.slice(1);
}

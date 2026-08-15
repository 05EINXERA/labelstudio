/**
 * Guards `frontend/js/pages/project/assign-options.js` — the one piece of
 * assignment logic shared by BOTH pickers (the Tasks page modal and the canvas
 * popover). It is pure, so it is testable in plain node with no DOM shim.
 *
 * What is worth pinning here:
 *
 *  1. **A falsy team must yield nobody, not everybody.** `membersForTeam(r, "")`
 *     returning the full roster would let a manager pick a person while the
 *     team select still reads "Unassigned", producing a named assignee with no
 *     team — a reservation that appears in no Team column and that both pickers
 *     deliberately prevent.
 *
 *  2. **The roster's dedup caveat is real and intentional.** The endpoint keeps
 *     only the first team per user, so someone in two granted teams is NOT
 *     listed under their second team. This test documents that as expected
 *     behaviour rather than leaving the next reader to discover it as a bug.
 *
 *  3. **String/number comparison.** `<select>.value` is a string, the roster
 *     carries numbers. A `===` on mixed types would silently list nobody.
 *
 *  4. **describeAssignment precedence** — person over team over nothing, which
 *     is what the canvas button label and the popover header both render, and
 *     which mirrors the server's refusal messages.
 */
import {
  membersForTeam,
  usernameFor,
  describeAssignment,
} from "../../frontend/js/pages/project/assign-options.js";

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log("  PASS", name)) : (fail++, console.log("  FAIL", name));
};

// Shaped like /api/projects/{id}/assignable-members: already deduplicated by
// user id, each row carrying the team the person was reached through.
const roster = [
  { user_id: 3, username: "priya", team_id: 1, team_name: "Alpha" },
  { user_id: 7, username: "Prakash", team_id: 1, team_name: "Alpha" },
  { user_id: 9, username: "sam", team_id: 2, team_name: "Beta" },
];

console.log("membersForTeam");
ok("lists only the selected team's people",
  membersForTeam(roster, 1).map((m) => m.user_id).join(",") === "3,7");
ok("matches a string team id against numeric roster rows",
  membersForTeam(roster, "2").map((m) => m.user_id).join(",") === "9");
ok("empty team id yields nobody, not the whole roster",
  membersForTeam(roster, "").length === 0);
ok("null team id yields nobody",
  membersForTeam(roster, null).length === 0);
ok("unknown team yields nobody",
  membersForTeam(roster, 99).length === 0);
ok("tolerates a missing roster",
  membersForTeam(undefined, 1).length === 0);

console.log("membersForTeam — documented dedup caveat");
// Priya is in teams 1 and 2, but the endpoint kept only team 1. Selecting
// team 2 therefore cannot reach her. Assigning her anyway is still legal
// server-side (E-10, warning not rejection) — just not via this path.
ok("a person deduped onto their first team is absent from the second",
  membersForTeam(roster, 2).every((m) => m.user_id !== 3));

console.log("usernameFor");
ok("resolves a known id", usernameFor(roster, 7) === "Prakash");
ok("resolves across string/number", usernameFor(roster, "7") === "Prakash");
ok("returns null for an unknown id", usernameFor(roster, 404) === null);
ok("returns null for a null id", usernameFor(roster, null) === null);

console.log("describeAssignment");
ok("names the individual when one is set", (() => {
  const d = describeAssignment(
    { assignee_user_id: 3, assignee_name: "priya", assigned_team_id: 1, assigned_team_name: "Alpha" },
    roster
  );
  return d.state === "user" && d.label === "priya";
})());

ok("prefers the person over the team", (() => {
  const d = describeAssignment(
    { assignee_user_id: 9, assignee_name: "sam", assigned_team_id: 1, assigned_team_name: "Alpha" },
    roster
  );
  return d.label === "sam";
})());

ok("recovers a missing assignee_name from the roster", (() => {
  const d = describeAssignment({ assignee_user_id: 7, assigned_team_id: 1 }, roster);
  return d.state === "user" && d.label === "Prakash";
})());

ok("falls back to the team when the assignee cannot be named", (() => {
  // Happens transiently after a member is removed: the server clears the field,
  // but a gallery row loaded before that still carries the id. Showing a raw id
  // would be worse than showing the team.
  const d = describeAssignment(
    { assignee_user_id: 404, assigned_team_id: 2, assigned_team_name: "Beta" },
    roster
  );
  return d.state === "team" && d.label === "Beta";
})());

ok("reports the team when only a team is set", (() => {
  const d = describeAssignment({ assigned_team_id: 2, assigned_team_name: "Beta" }, roster);
  return d.state === "team" && d.label === "Beta";
})());

ok("reports unassigned when nothing is set", (() => {
  const d = describeAssignment({ assigned_team_id: null, assignee_user_id: null }, roster);
  return d.state === "none" && d.label === "Unassigned";
})());

ok("treats a null task as unassigned rather than throwing",
  describeAssignment(null).state === "none");

ok("works with no roster supplied", (() => {
  const d = describeAssignment({ assignee_user_id: 3, assignee_name: "priya" });
  return d.state === "user" && d.label === "priya";
})());

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

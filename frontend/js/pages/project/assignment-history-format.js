/**
 * Pure formatting for the assignment-history modal.
 *
 * No DOM and no imports, so it runs under plain node
 * (`tests/js/assignment_history_spec.mjs`) like `untangle.js`. Every function
 * returns plain text; the caller escapes it before putting it in markup.
 * See .devnotes/features/task-assignment-history/02_DESIGN.md § 2 and § 8.
 */

const pad = (n) => String(n).padStart(2, "0");

/** `20/09/2026 12:00:00` in the viewer's local time. Locale-independent. */
export function formatStamp(iso) {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso ?? "—");
  return (
    `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
  );
}

function personName(side) {
  if (side.user) return side.user;
  // Name snapshot missing but the id survived (a user that was already gone at
  // migration time): say so rather than printing nothing.
  return side.user_id != null ? `User #${side.user_id}` : null;
}

function teamName(side) {
  if (side.team) return side.team;
  return side.team_id != null ? `Team #${side.team_id}` : null;
}

/**
 * The state one end of a change describes:
 *   person set  -> "User B · Team X"   (team shown as a suffix when known)
 *   team only   -> "Team X (anyone)"
 *   neither     -> "Unassigned"
 */
export function describeSide(side) {
  const person = personName(side || {});
  const team = teamName(side || {});
  if (person) return team ? `${person} · ${team}` : person;
  if (team) return `${team} (anyone)`;
  return "Unassigned";
}

const SOURCE_NOTES = {
  bulk_assign: "bulk assign",
  team_deleted: "team deleted",
  grant_revoked: "team access revoked",
  member_left: "left the team",
  member_removed: "removed from the team",
};

/**
 * One log line.
 *
 * @returns {{stamp:string, initial:boolean, from:(string|null), to:string,
 *            note:string}}
 *   `initial` rows (the migration backfill) have no `from`: the assignee at the
 *   time history began is shown as the first entry. `note` is the optional
 *   "who/why" suffix.
 */
export function formatEvent(event) {
  const initial = event.source === "backfill";
  const bits = [];
  if (SOURCE_NOTES[event.source]) bits.push(SOURCE_NOTES[event.source]);
  if (event.changed_by_username) bits.push(`by ${event.changed_by_username}`);
  return {
    stamp: formatStamp(event.created_at),
    initial,
    from: initial ? null : describeSide(event.from),
    to: describeSide(event.to),
    note: bits.join(" · "),
  };
}

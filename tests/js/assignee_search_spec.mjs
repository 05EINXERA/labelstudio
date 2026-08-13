/**
 * Behaviour spec for the Tasks view's assignee-name search.
 *
 * Run: node tests/js/assignee_search_spec.mjs
 *
 * `frontend/js/pages/project/assignee-search.js` turns the typed name into the
 * `assignee` filter value sent to `GET /api/tasks`. Three things are guarded:
 *
 *  1. **A query matching nobody must return NO_MATCH, never null/"".** null
 *     means "not filtering", so the caller falls back to the dropdown and the
 *     user who typed a nonexistent name would be shown *every* task — the exact
 *     opposite of what they asked for. This is the bug the sentinel exists for.
 *
 *  2. Deduplication. Someone in two granted teams arrives as two roster rows
 *     with one user_id (see test_assignable_members_deduplicates_across_teams);
 *     emitting the id twice would make the URL depend on team membership.
 *
 *  3. isSearchFilterValue() must recognise exactly the values this module
 *     produces, because tasks.js uses it to decide what may be written into the
 *     <select> — a multi-id value has no matching <option> and blanks it.
 *
 * The module imports nothing, so no DOM shim is needed.
 */
const url = new URL('../../frontend/js/pages/project/assignee-search.js', import.meta.url);
const { matchAssignees, isSearchFilterValue, NO_MATCH } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

// Roster shaped like /api/projects/{id}/assignable-members returns: one row per
// (person, granted team), so Priya appears twice.
const roster = [
  { user_id: 3, username: 'priya', team_id: 1 },
  { user_id: 3, username: 'priya', team_id: 2 },
  { user_id: 7, username: 'Prakash', team_id: 1 },
  { user_id: 9, username: 'sam', team_id: 2 },
];

console.log('matchAssignees');
ok('blank query does not filter', matchAssignees(roster, '') === null);
ok('whitespace-only query does not filter', matchAssignees(roster, '   ') === null);
ok('null query does not filter', matchAssignees(roster, null) === null);
ok('undefined query does not filter', matchAssignees(roster, undefined) === null);

ok('exact name matches one id', matchAssignees(roster, 'sam') === 'user-9');
ok('mid-string substring matches', matchAssignees(roster, 'riy') === 'user-3');
ok('case-insensitive', matchAssignees(roster, 'PRAKASH') === 'user-7');
ok('surrounding whitespace ignored', matchAssignees(roster, '  sam  ') === 'user-9');

ok('prefix shared by two people matches both', matchAssignees(roster, 'pr') === 'user-3,user-7');
ok('duplicate roster rows yield one id', matchAssignees(roster, 'priya') === 'user-3');

// The load-bearing one.
ok('no match returns the sentinel, not null', matchAssignees(roster, 'zzz') === NO_MATCH);
ok('sentinel is not falsy-empty', NO_MATCH !== '' && NO_MATCH != null);
ok('empty roster with a query returns the sentinel', matchAssignees([], 'sam') === NO_MATCH);
ok('missing roster with a query returns the sentinel', matchAssignees(null, 'sam') === NO_MATCH);
ok('empty roster with no query still returns null', matchAssignees([], '') === null);

// Malformed roster rows must not crash the toolbar or emit "user-null".
ok('rows without an id are skipped', matchAssignees([{ username: 'sam' }], 'sam') === NO_MATCH);
ok(
  'rows without a username are skipped',
  matchAssignees([{ user_id: 4 }, { user_id: 9, username: 'sam' }], 'sam') === 'user-9',
);

console.log('isSearchFilterValue');
ok('multi-id list is a search value', isSearchFilterValue('user-3,user-7') === true);
ok('sentinel is a search value', isSearchFilterValue(NO_MATCH) === true);
// These are the dropdown's own vocabulary and must stay assignable to the
// <select>, so they must NOT be treated as search-produced.
ok('single id is not a search value', isSearchFilterValue('user-3') === false);
ok('"All" is not a search value', isSearchFilterValue('All') === false);
ok('"mine" is not a search value', isSearchFilterValue('mine') === false);
ok('"unassigned" is not a search value', isSearchFilterValue('unassigned') === false);
ok('empty is not a search value', isSearchFilterValue('') === false);
ok('null is not a search value', isSearchFilterValue(null) === false);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

/**
 * Spec for the assignment-history formatter.
 *
 * Run: node tests/js/assignment_history_spec.mjs  (or via
 * tests/test_assignment_history_js.py)
 *
 * Covers .devnotes/features/task-assignment-history/03_EDGE_CASES.md
 * H-16, H-17, H-18, H-22.
 */
const url = new URL('../../frontend/js/pages/project/assignment-history-format.js', import.meta.url);
const { formatStamp, describeSide, formatEvent } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};
const side = (o = {}) => ({ team: null, team_id: null, user: null, user_id: null, ...o });

// describeSide
ok('person only', describeSide(side({ user: 'User A', user_id: 1 })) === 'User A');
ok('person with team suffix',
   describeSide(side({ user: 'User B', user_id: 2, team: 'Team X', team_id: 1 })) === 'User B · Team X');
ok('team only reads "anyone"', describeSide(side({ team: 'Team X', team_id: 1 })) === 'Team X (anyone)');
ok('neither is Unassigned', describeSide(side()) === 'Unassigned');
ok('missing side is Unassigned', describeSide(undefined) === 'Unassigned');
ok('H-22 missing user name falls back to the id', describeSide(side({ user_id: 7 })) === 'User #7');
ok('H-22 missing team name falls back to the id', describeSide(side({ team_id: 3 })) === 'Team #3 (anyone)');

// formatStamp: dd/mm/yyyy HH:MM:SS in local time
const local = new Date(2026, 8, 20, 12, 0, 5); // 20 Sep 2026 12:00:05 local
ok('stamp format', formatStamp(local.toISOString()) === '20/09/2026 12:00:05');
ok('stamp pads single digits', formatStamp(new Date(2026, 0, 2, 3, 4, 5).toISOString()) === '02/01/2026 03:04:05');
ok('H-18 explicit Z and +00:00 agree',
   formatStamp('2026-09-20T12:00:00Z') === formatStamp('2026-09-20T12:00:00+00:00'));
ok('unparseable stamp is shown, not thrown', formatStamp('garbage') === 'garbage');

// formatEvent
const first = formatEvent({
  source: 'backfill', created_at: local.toISOString(), changed_by_username: null,
  from: side(), to: side({ user: 'User A', user_id: 1 }),
});
ok('H-16 backfill is the initial row with no from', first.initial === true && first.from === null && first.to === 'User A');

const change = formatEvent({
  source: 'assign', created_at: local.toISOString(), changed_by_username: 'boss',
  from: side({ user: 'User A', user_id: 1 }), to: side({ user: 'User B', user_id: 2 }),
});
ok('a change shows from and to', change.from === 'User A' && change.to === 'User B' && change.initial === false);
ok('actor shown as a note', change.note === 'by boss');

const clear = formatEvent({
  source: 'member_removed', created_at: local.toISOString(), changed_by_username: 'boss',
  from: side({ user: 'User B', user_id: 2, team: 'Team X', team_id: 1 }), to: side(),
});
ok('system clear explains itself', clear.to === 'Unassigned' && clear.note === 'removed from the team · by boss');

const fresh = formatEvent({
  source: 'assign', created_at: local.toISOString(), changed_by_username: null,
  from: side(), to: side({ user: 'User A', user_id: 1 }),
});
ok('H-17 first real change reads Unassigned -> X', fresh.from === 'Unassigned' && fresh.to === 'User A');

// The formatter returns raw text; escaping is the renderer's job, so markup in
// a name must survive untouched here (and be escaped there).
ok('names pass through unmodified', describeSide(side({ user: '<b>x</b>', user_id: 1 })) === '<b>x</b>');

// The actions column: the history icon is a manager+ control (rendering only;
// the server is the authority — rule 18b), and sits between Assign and Edit.
const colsUrl = new URL('../../frontend/js/pages/project/task-columns.js', import.meta.url);
const { buildColumns } = await import(colsUrl);
const actions = (role) => buildColumns({
  role, projectId: 7, teamsById: new Map(), usersById: new Map(), lockCache: {},
  currentUser: { id: 1 },
}).find((c) => c.key === 'actions').render({ id: 1, status: 'New' });
ok('manager sees the history button', actions('manager').includes('data-action="history"'));
ok('owner sees the history button', actions('owner').includes('data-action="history"'));
ok('reviewer does not', !actions('reviewer').includes('data-action="history"'));
ok('annotator does not', !actions('annotator').includes('data-action="history"'));
const m = actions('manager');
ok('history sits between assign and edit',
   m.indexOf('data-action="assign"') < m.indexOf('data-action="history"') &&
   m.indexOf('data-action="history"') < m.indexOf('data-action="edit"'));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

/**
 * Behaviour spec for the Move Tasks view's read-only column set.
 *
 * Run: node tests/js/move_columns_spec.mjs (or via tests/test_move_columns.py)
 *
 * The Move Tasks table reuses the Tasks view's column definitions so the two
 * look alike as cells evolve. What it must *not* reuse is the ability to change
 * anything: an inline status select or a Delete button there would make a
 * picker into a second editor, and the canvas link would send an owner off to
 * annotate when they meant to select.
 *
 * These are pure functions of (row, view-state), so they test directly.
 */
const url = new URL('../../frontend/js/pages/project/task-columns.js', import.meta.url);
const { buildColumns } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

const base = {
  projectId: 7,
  teamsById: new Map([[3, 'Team A']]),
  usersById: new Map([[11, 'diwana']]),
  lockCache: {},
  currentUser: { id: 11 },
};

const columns = (opts) => buildColumns({ ...base, ...opts });
const keys = (cols) => cols.map((c) => c.key);
const cell = (cols, key, row) => cols.find((c) => c.key === key).render(row);

const row = {
  id: 5,
  description: 'P1000123.jpg',
  status: 'In Progress',
  assigned_team_id: 3,
  assignee_user_id: 11,
  time_spent: 90,
  class_count: 2,
  comment_count: 1,
};

// --- what the picker drops --------------------------------------------------

{
  const ro = columns({ role: 'owner', readOnly: true });
  ok('read-only drops the row actions column', !keys(ro).includes('actions'));
  ok('read-only keeps every identifying column',
     ['image_path', 'description', 'assigned_team_id', 'assignee', 'status',
      'time_spent', 'classes', 'comments'].every((k) => keys(ro).includes(k)));
}

{
  // The lock badge stays: a task somebody has open is exactly the one the move
  // will refuse, so seeing it before submitting saves a round trip.
  const ro = columns({ role: 'owner', readOnly: true });
  ok('read-only keeps the lock column', keys(ro).includes('_lock'));
}

// --- what the picker must not render ---------------------------------------

{
  const ro = columns({ role: 'owner', readOnly: true });
  ok('read-only renders the filename as text, not a canvas link',
     !cell(ro, 'description', row).includes('<a '));
  ok('read-only status is a pill, never a select',
     !cell(ro, 'status', row).includes('<select'));
}

{
  // An owner is a reviewer by rank, so without readOnly this is a select. That
  // is the case that would silently turn the picker into an approval surface.
  const full = columns({ role: 'owner' });
  ok('the Tasks view still gets its inline status select for a reviewer',
     cell(full, 'status', row).includes('<select'));
  ok('the Tasks view still gets its row actions', keys(full).includes('actions'));
  ok('the Tasks view still links the filename to the canvas',
     cell(full, 'description', row).includes('<a '));
}

// --- cells still carry the information a mover needs ------------------------

{
  const ro = columns({ role: 'owner', readOnly: true });
  ok('the team a task is assigned to is still named',
     cell(ro, 'assigned_team_id', row).includes('Team A'));
  ok('the person a task is assigned to is still named',
     cell(ro, 'assignee', row).includes('diwana'));
}

{
  // Filenames come from uploads and are user-controlled, so the picker's own
  // renderer has to escape them like every other cell does.
  const ro = columns({ role: 'owner', readOnly: true });
  const hostile = { ...row, description: '<img src=x onerror=alert(1)>.jpg' };
  const html = cell(ro, 'description', hostile);
  ok('a hostile filename is escaped', !html.includes('<img src=x'));
}

// --- the nav gate -----------------------------------------------------------

{
  // The route is owner-only. `router.js` resolves an unlisted route to Home
  // using this very function, so hiding the tab and refusing the typed URL are
  // one decision rather than two that can drift (rule 18d).
  const navUrl = new URL('../../frontend/js/components/project-nav.js', import.meta.url);
  const { visibleNavItems } = await import(navUrl);
  const routes = (role) => visibleNavItems(role).map((i) => i.route);

  ok('an owner sees Move Tasks', routes('owner').includes('move'));
  ok('a manager does not', !routes('manager').includes('move'));
  ok('a reviewer does not', !routes('reviewer').includes('move'));
  ok('an annotator does not', !routes('annotator').includes('move'));
  ok('a viewer does not', !routes('viewer').includes('move'));
  ok('someone with no role at all does not', !routes(null).includes('move'));
  ok('Move Tasks sits last, after Access',
     routes('owner').slice(-2).join(',') === 'access,move');
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

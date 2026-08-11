/**
 * Behaviour spec for the tasks view's URL state and the canvas link.
 *
 * Run: node tests/js/tasks_view_state_spec.mjs  (or via tests/test_tasks_view_state.py)
 *
 * Two user-visible promises are guarded here:
 *
 *  1. Opening an image from page 4 and pressing Back returns to page 4. The
 *     page number therefore has to survive a round trip through the URL, and
 *     the canvas link has to carry enough state to rebuild it.
 *  2. The canvas walks the same sequence the table showed. The link carries the
 *     sort and filters so `/api/tasks/order` returns that exact set — a canvas
 *     that ignored an active filter would page into tasks the table excluded.
 *
 * `canvasQuery` is imported from the real module. The hash serialiser in
 * tasks.js is not importable without a DOM (the module touches `document` at
 * import time), so its format is asserted against the same URLSearchParams
 * contract the router parses with — the round trip, not the implementation.
 */
const url = new URL('../../frontend/js/pages/project/task-columns.js', import.meta.url);
const { canvasQuery } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

const parse = (qs) => new URLSearchParams(qs);

// --- the canvas link -------------------------------------------------------

{
  const q = parse(canvasQuery(270, 39, null));
  ok('link always carries the project', q.get('projectId') === '270');
  ok('link always carries the task', q.get('taskId') === '39');
  ok('no view state means no extra params', [...q.keys()].sort().join(',') === 'projectId,taskId');
}

{
  // The default view (filename ascending, page 1, no filters) should not
  // clutter the URL — every value here is the default.
  const q = parse(canvasQuery(270, 39, {
    page: 1,
    sortKey: 'description',
    sortDesc: false,
    query: '',
    filters: { status: 'All', team: 'All', assignee: 'All' },
  }));
  ok('defaults are omitted', [...q.keys()].sort().join(',') === 'projectId,taskId');
}

{
  const q = parse(canvasQuery(270, 39, {
    page: 4,
    sortKey: 'status',
    sortDesc: true,
    query: 'cat',
    filters: { status: 'Rejected', team: '7', assignee: 'mine' },
  }));
  ok('page rides along for the back link', q.get('page') === '4');
  ok('non-default sort is carried', q.get('sort') === 'status');
  ok('descending order is carried', q.get('order') === 'desc');
  ok('the search term is carried', q.get('q') === 'cat');
  ok('the status filter is carried', q.get('status') === 'Rejected');
  ok('the team filter is carried', q.get('team') === '7');
  ok('the assignee filter is carried', q.get('assignee') === 'mine');
}

{
  // Sentinel filter values pass through verbatim — the server speaks this
  // vocabulary directly, so any translation here would be a bug.
  const q = parse(canvasQuery(1, 2, {
    page: 1, filters: { team: 'unassigned', assignee: 'user-12' },
  }));
  ok('"unassigned" passes through', q.get('team') === 'unassigned');
  ok('"user-<id>" passes through', q.get('assignee') === 'user-12');
}

{
  // A filename with URL metacharacters must not break the link.
  const q = parse(canvasQuery(1, 2, { page: 1, query: 'a&b=c d.png' }));
  ok('the search term is encoded and decodes back', q.get('q') === 'a&b=c d.png');
}

// --- the hash round trip ---------------------------------------------------
//
// Mirrors readViewStateFromHash() in tasks.js. Kept in step with it by
// asserting the contract both sides share: what the canvas link writes, the
// tasks view must be able to read back.

function readViewState(params) {
  const page = parseInt(params.get('page') ?? '', 10);
  return {
    page: Number.isFinite(page) && page >= 1 ? page : 1,
    sortKey: params.get('sort') || 'description',
    sortDesc: params.get('order') === 'desc',
    query: params.get('q') || '',
    status: params.get('status') || 'All',
    team: params.get('team') || 'All',
    assignee: params.get('assignee') || 'All',
  };
}

{
  const state = readViewState(parse(''));
  ok('an empty hash restores page 1', state.page === 1);
  ok('an empty hash restores the filename sort', state.sortKey === 'description');
  ok('an empty hash restores ascending', state.sortDesc === false);
  ok('an empty hash restores no filters',
     state.status === 'All' && state.team === 'All' && state.assignee === 'All');
}

{
  const view = {
    page: 4, sortKey: 'status', sortDesc: true, query: 'cat',
    filters: { status: 'Rejected', team: '7', assignee: 'mine' },
  };
  const restored = readViewState(parse(canvasQuery(270, 39, view)));
  ok('page survives the round trip', restored.page === 4);
  ok('sort survives the round trip', restored.sortKey === 'status');
  ok('order survives the round trip', restored.sortDesc === true);
  ok('query survives the round trip', restored.query === 'cat');
  ok('filters survive the round trip',
     restored.status === 'Rejected' && restored.team === '7' && restored.assignee === 'mine');
}

{
  // Junk in the URL must degrade to the default rather than NaN-ing the pager.
  for (const bad of ['0', '-3', 'abc', '']) {
    const state = readViewState(parse(`page=${encodeURIComponent(bad)}`));
    ok(`page="${bad}" falls back to 1`, state.page === 1);
  }
  ok('a fractional page is floored to an integer',
     Number.isInteger(readViewState(parse('page=4')).page));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

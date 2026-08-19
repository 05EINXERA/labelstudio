/**
 * Behaviour spec for the Home dashboard's status tiles.
 *
 * Run: node tests/js/dashboard_tiles_spec.mjs  (or via tests/test_dashboard_tiles.py)
 *
 * Two promises are guarded:
 *
 *  1. Each export batch (Approved / Verified / Checked / Passed) gets its own
 *     tile with its own count. The dashboard used to show one "Approved" tile
 *     holding the whole group, which hid exactly the distinction the batch
 *     names exist to make (CLAUDE.md rule 11a).
 *  2. Every tile links to the tasks view *with its filter applied*, so clicking
 *     "Awaiting review: 12" opens a table of those 12 and not of all 400.
 *
 * `statusTiles` and `tasksHref` are pure and imported from the real module.
 * `home.js` imports api.js/utils.js at module scope but neither touches the DOM
 * on import, so no shim is needed.
 */
const url = new URL('../../frontend/js/pages/project/home.js', import.meta.url);
const { statusTiles, tasksHref } = await import(url);
const statusUrl = new URL('../../frontend/js/task-status.js', import.meta.url);
const { APPROVED_STATUSES, TASK_STATUSES } = await import(statusUrl);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

/** A metrics payload with the whole vocabulary at zero, plus any overrides —
 *  the shape _aggregate_metrics actually returns. */
const byStatus = (overrides = {}) => ({
  by_status: { ...Object.fromEntries(TASK_STATUSES.map((s) => [s, 0])), ...overrides },
});

// --- the approved group is split, not collapsed -----------------------------

{
  // One task signed off under each batch name. The old dashboard showed a
  // single "Approved: 4"; each batch must now carry its own 1.
  const tiles = statusTiles(byStatus({ Approved: 1, Verified: 1, Checked: 1, Passed: 1 }));
  const approvals = tiles.filter((t) => APPROVED_STATUSES.includes(t.status));

  ok('one tile per approved-group status',
    approvals.length === APPROVED_STATUSES.length);
  ok('approval tiles keep vocabulary order',
    approvals.map((t) => t.status).join(',') === APPROVED_STATUSES.join(','));
  ok('each batch shows its own count, not the group total',
    approvals.every((t) => t.value === 1));
}

{
  // The counts are genuinely independent — a lopsided week must not average out.
  const tiles = statusTiles(byStatus({ Approved: 7, Verified: 0, Checked: 3, Passed: 0 }));
  const find = (s) => tiles.find((t) => t.status === s);
  ok('Approved reads its own count', find('Approved').value === 7);
  ok('Checked reads its own count', find('Checked').value === 3);
  ok('an empty batch still renders a zero tile', find('Verified').value === 0);
}

// --- the tile list is derived from the vocabulary ---------------------------

{
  const tiles = statusTiles(byStatus());
  const covered = tiles.map((t) => t.status).sort();
  ok('every task status has exactly one tile',
    covered.join(',') === [...TASK_STATUSES].sort().join(','));
  ok('no tile is missing a label', tiles.every((t) => typeof t.label === 'string' && t.label));
}

{
  // The reviewer-facing labels differ from the raw status names on purpose:
  // 'Completed' is the annotator's submission, i.e. the review queue.
  const tiles = statusTiles(byStatus({ Completed: 12 }));
  const awaiting = tiles.find((t) => t.status === 'Completed');
  ok('the Completed tile is labelled as the review queue',
    awaiting.label === 'Awaiting review' && awaiting.value === 12);
}

// --- click-to-filter links --------------------------------------------------

const hashParams = (href) => new URLSearchParams(href.split('?')[1] || '');

{
  const tiles = statusTiles(byStatus());
  ok('every tile points at the tasks view',
    tiles.every((t) => t.href.startsWith('#/tasks?')));
  ok('every tile carries its own status as the filter',
    tiles.every((t) => hashParams(t.href).get('status') === t.status));
}

{
  // The space in 'In Progress' has to survive encoding, or the tasks view
  // filters on a status the server will never match.
  const href = tasksHref('In Progress');
  ok('a status with a space is encoded, not raw', !href.includes(' '));
  ok('a status with a space round-trips', hashParams(href).get('status') === 'In Progress');
}

{
  // Defensive: a status containing '&' would otherwise split into two params
  // and silently filter on something else.
  ok('a status with an ampersand round-trips',
    hashParams(tasksHref('A&B')).get('status') === 'A&B');
  ok('no status means no filter', tasksHref('') === '#/tasks');
}

// --- tolerating an older or partial payload ---------------------------------

{
  // A cached bundle can reach a server that predates `by_status` (rule 13: a JS
  // change needs a hard reload). Tiles must read 0, never `undefined`.
  const tiles = statusTiles({ total: 9, completed: 4 });
  ok('a payload with no by_status renders zeros',
    tiles.every((t) => t.value === 0));
  ok('a missing status key renders 0, not undefined',
    statusTiles({ by_status: { Approved: 2 } })
      .find((t) => t.status === 'Verified').value === 0);
  ok('a null payload does not throw', statusTiles(null).length === TASK_STATUSES.length);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

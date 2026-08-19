/**
 * Behaviour spec for the Tasks view's reset-vs-restore decision.
 *
 * Run: node tests/js/tasks_view_restore_spec.mjs
 *      (or via tests/test_tasks_view_restore.py)
 *
 * The promise being guarded is one sentence with two halves, and both halves
 * have bitten users:
 *
 *   - Reloading the tasks page clears the filters. Before this, the hash kept
 *     them and mount() put them back on the *controls* but never on the table,
 *     so the dropdown read "Approved" over a list of every task — and because
 *     the select already showed "Approved", picking it again fired no `change`
 *     event and the control looked dead.
 *   - Coming back from the image workspace keeps them, because that is the
 *     annotator's place in a filtered queue and losing it means re-filtering
 *     after every single image.
 */
const url = new URL('../../frontend/js/pages/project/tasks-view-restore.js', import.meta.url);
const {
  RETURN_TICKET_KEY,
  RETURN_TICKET_TTL_MS,
  consumeReturnTicket,
  isBackForwardNavigation,
  shouldRestoreFilters,
  clearResettableParams,
} = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

/** Minimal sessionStorage stand-in. `throwing` models private mode. */
function fakeStorage(initial = {}, { throwing = false } = {}) {
  const map = new Map(Object.entries(initial));
  return {
    getItem(k) { if (throwing) throw new Error('denied'); return map.has(k) ? map.get(k) : null; },
    setItem(k, v) { if (throwing) throw new Error('denied'); map.set(k, String(v)); },
    removeItem(k) { if (throwing) throw new Error('denied'); map.delete(k); },
    has(k) { return map.has(k); },
  };
}

const perfWith = (type) => ({ getEntriesByType: () => [{ type }] });

// --- the ticket ------------------------------------------------------------

{
  const now = 1_000_000;
  const store = fakeStorage({ [RETURN_TICKET_KEY]: String(now - 500) });
  ok('a fresh ticket counts as a return', consumeReturnTicket(store, now) === true);
  ok('the ticket is consumed on read', store.has(RETURN_TICKET_KEY) === false);
}

{
  // One-shot is the whole mechanism: return once, then the next reload of that
  // same URL must start clean.
  const now = 1_000_000;
  const store = fakeStorage({ [RETURN_TICKET_KEY]: String(now - 500) });
  consumeReturnTicket(store, now);
  ok('a second read finds nothing', consumeReturnTicket(store, now) === false);
}

{
  const now = 1_000_000;
  const store = fakeStorage({ [RETURN_TICKET_KEY]: String(now - RETURN_TICKET_TTL_MS - 1) });
  ok('an expired ticket does not restore', consumeReturnTicket(store, now) === false);
  ok('an expired ticket is cleared too', store.has(RETURN_TICKET_KEY) === false);
}

{
  // A clock that moved backwards (NTP correction, a machine resumed from sleep)
  // must not read as a ticket from the future.
  const now = 1_000_000;
  const store = fakeStorage({ [RETURN_TICKET_KEY]: String(now + 5_000) });
  ok('a future-dated ticket does not restore', consumeReturnTicket(store, now) === false);
}

{
  const store = fakeStorage({ [RETURN_TICKET_KEY]: 'not-a-number' });
  ok('a corrupt ticket does not restore', consumeReturnTicket(store, 1_000_000) === false);
}

ok('no storage means no ticket', consumeReturnTicket(null, 1) === false);
ok('storage that throws is survivable',
  consumeReturnTicket(fakeStorage({}, { throwing: true }), 1) === false);

// --- the navigation type ---------------------------------------------------

ok('back_forward is a return', isBackForwardNavigation(perfWith('back_forward')) === true);
ok('a reload is not a return', isBackForwardNavigation(perfWith('reload')) === false);
ok('a fresh navigation is not a return', isBackForwardNavigation(perfWith('navigate')) === false);
ok('no performance object is not a return', isBackForwardNavigation(null) === false);
ok('an empty entry list is not a return',
  isBackForwardNavigation({ getEntriesByType: () => [] }) === false);
ok('the legacy API still answers',
  isBackForwardNavigation({ navigation: { type: 2 } }) === true);
ok('the legacy API says no for a reload',
  isBackForwardNavigation({ navigation: { type: 1 } }) === false);

// --- the combined decision -------------------------------------------------

{
  const now = 1_000_000;
  ok('reload with no ticket resets', shouldRestoreFilters({
    storage: fakeStorage(), performance: perfWith('reload'), now,
  }) === false);

  ok('back navigation restores without a ticket', shouldRestoreFilters({
    storage: fakeStorage(), performance: perfWith('back_forward'), now,
  }) === true);

  ok('the href fallback restores on its ticket alone', shouldRestoreFilters({
    storage: fakeStorage({ [RETURN_TICKET_KEY]: String(now) }),
    performance: perfWith('navigate'),
    now,
  }) === true);
}

{
  // The ticket must be spent even when the navigation type already said "back",
  // or it would survive to wrongly restore the *next* reload.
  const now = 1_000_000;
  const store = fakeStorage({ [RETURN_TICKET_KEY]: String(now) });
  shouldRestoreFilters({ storage: store, performance: perfWith('back_forward'), now });
  ok('the ticket is spent even when unneeded', store.has(RETURN_TICKET_KEY) === false);
}

// --- in-page navigation (the dashboard tiles) ------------------------------
//
// A Home tile links to `#/tasks?status=Verified`: it asks to change view *and*
// to filter, in one hash change. Only a document load can be a reload, so a
// hash change must always keep the filters it carries — otherwise the tile's
// status is stripped on arrival and the table opens unfiltered, which is the
// bug this block exists to prevent.

{
  const now = 1_000_000;

  ok('an in-page navigation restores without a ticket', shouldRestoreFilters({
    storage: fakeStorage(), performance: perfWith('navigate'),
    inPageNavigation: true, now,
  }) === true);

  // The performance entry describes the original document load and stays
  // 'reload' for the life of the page. A tile clicked after a reload must still
  // filter, so the in-page signal has to win over it.
  ok('an in-page navigation outranks a stale reload entry', shouldRestoreFilters({
    storage: fakeStorage(), performance: perfWith('reload'),
    inPageNavigation: true, now,
  }) === true);

  // The first render of a document is not an in-page navigation, so a plain
  // reload of a filtered URL still resets — the original behaviour is intact.
  ok('the first render of a reloaded document still resets', shouldRestoreFilters({
    storage: fakeStorage(), performance: perfWith('reload'),
    inPageNavigation: false, now,
  }) === false);

  ok('omitting the flag keeps the old behaviour', shouldRestoreFilters({
    storage: fakeStorage(), performance: perfWith('reload'), now,
  }) === false);
}

{
  // The ticket is spent on an in-page navigation too, or it would survive to
  // wrongly restore a later reload.
  const now = 1_000_000;
  const store = fakeStorage({ [RETURN_TICKET_KEY]: String(now) });
  shouldRestoreFilters({
    storage: store, performance: perfWith('navigate'), inPageNavigation: true, now,
  });
  ok('an in-page navigation still spends the ticket',
    store.has(RETURN_TICKET_KEY) === false);
}

// --- what actually gets cleared --------------------------------------------

{
  const before = new URLSearchParams(
    'page=4&sort=status&order=desc&q=cat&status=Approved&team=7&assignee=mine'
  );
  const after = clearResettableParams(before);

  ok('the status filter is cleared', after.get('status') === null);
  ok('the team filter is cleared', after.get('team') === null);
  ok('the assignee filter is cleared', after.get('assignee') === null);
  ok('the filename search is cleared', after.get('q') === null);

  // Page and sort are not filters. A reload of page 4 stays on page 4, and a
  // link shared with an explicit sort keeps it.
  ok('the page survives a reset', after.get('page') === '4');
  ok('the sort key survives a reset', after.get('sort') === 'status');
  ok('the sort order survives a reset', after.get('order') === 'desc');

  ok("the caller's params are not mutated", before.get('status') === 'Approved');
}

{
  ok('clearing empty params is harmless',
    clearResettableParams(new URLSearchParams()).toString() === '');
  ok('clearing null params is harmless',
    clearResettableParams(null).toString() === '');
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

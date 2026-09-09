/**
 * Behaviour spec for `retainSelection` on the data-table.
 *
 * Run: node tests/js/data_table_selection_spec.mjs  (or via
 * tests/test_data_table_selection.py)
 *
 * By default the table intersects `state.selected` with the rows currently on
 * screen, on every `loadPage()` and every `setRows()`. That is right for a bulk
 * action on one page — an id the user can no longer see must not be submitted
 * by a later Delete — and it is exactly wrong for the Move Tasks view, where
 * the selection is deliberately assembled across searches and pages.
 *
 * So both behaviours have to be pinned: the opt-in must work, and the default
 * must be untouched, because every existing table relies on it.
 */
import { parseDocument } from './dom-shim.mjs';

const url = new URL('../../frontend/js/components/data-table.js', import.meta.url);
const { createDataTable } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

/**
 * A server-mode table over synthetic rows whose page content the test drives.
 * `pages` maps a query string to the rows the server returns for it, so a
 * "search" can be simulated by changing the query.
 */
function table(opts = {}) {
  const mount = parseDocument();
  let serve = (q) => [{ id: 1, name: 'one' }, { id: 2, name: 'two' }, { id: 3, name: 'three' }];
  const seen = [];

  const api = createDataTable({
    mount,
    columns: [{ key: 'name', label: 'Name' }],
    rowId: (r) => r.id,
    selectable: true,
    onSelectionChange: (sel) => seen.push(new Set(sel)),
    server: {
      fetchPage: async (q) => {
        const items = serve(q);
        return { items, total: 30, page: q.page, page_size: q.pageSize, total_pages: 3 };
      },
    },
    ...opts,
  });
  return { mount, api, seen, setServe: (fn) => { serve = fn; } };
}

const rowBoxes = (mount) => mount.querySelectorAll('[data-role="row"]');
const selectAll = (mount) => mount.querySelector('[data-role="select-all"]');

/** Tick the checkbox for `id` the way a user would. */
function check(mount, id) {
  const cb = rowBoxes(mount).find((c) => c.dataset.id === String(id));
  cb.checked = true;
  cb.dispatch('change');
}

// --- the default is unchanged ----------------------------------------------

{
  const { mount, api, setServe } = table();
  await api.load();
  check(mount, 1);
  ok('default: a selection is recorded', api.getSelection().has(1));

  setServe(() => [{ id: 9, name: 'nine' }]);
  await api.load();
  ok('default: a selection off the current page is dropped',
     api.getSelection().size === 0);
}

{
  // Client mode goes through setRows(), which has its own copy of the prune.
  const mount = parseDocument();
  const api = createDataTable({
    mount,
    columns: [{ key: 'name', label: 'Name' }],
    rowId: (r) => r.id,
    selectable: true,
  });
  api.setRows([{ id: 1, name: 'one' }, { id: 2, name: 'two' }]);
  check(mount, 1);
  api.setRows([{ id: 2, name: 'two' }]);
  ok('default: setRows drops a selection whose row is gone',
     api.getSelection().size === 0);
}

// --- retainSelection -------------------------------------------------------

{
  const { mount, api, setServe } = table({ retainSelection: true });
  await api.load();
  check(mount, 1);
  check(mount, 2);

  // The user types in the search box; the server answers with a different set.
  setServe(() => [{ id: 3, name: 'three' }]);
  await api.load();

  ok('retained: a search does not clear the selection',
     api.getSelection().size === 2 && api.getSelection().has(1));

  // ...and selecting from the narrowed result adds to it rather than replacing.
  check(mount, 3);
  ok('retained: selections accumulate across searches',
     [...api.getSelection()].sort().join(',') === '1,2,3');
}

{
  const { mount, api, setServe } = table({ retainSelection: true });
  await api.load();
  check(mount, 1);
  setServe(() => [{ id: 5, name: 'five' }]);
  await api.load();

  ok('retained: rows off-page are not in getRows()',
     api.getRows().every((r) => r.id !== 1));
  ok('retained: but the id is still selected — the caller needs its own row map',
     api.getSelection().has(1));
}

{
  const { mount, api } = table({ retainSelection: true });
  await api.load();
  check(mount, 1);
  api.clearSelection();
  ok('retained: clearSelection still empties everything',
     api.getSelection().size === 0);
}

{
  // The header box means "all on this page", and must keep meaning that: a
  // selection the user cannot see, driving a project-sized move, is how the
  // wrong thousand tasks get moved.
  const { mount, api, setServe } = table({ retainSelection: true });
  await api.load();
  check(mount, 1);

  setServe(() => [{ id: 7, name: 'seven' }, { id: 8, name: 'eight' }]);
  await api.load();
  ok('retained: select-all is unchecked when this page is unselected',
     selectAll(mount).checked === false);

  const all = selectAll(mount);
  all.checked = true;
  all.dispatch('change');
  ok('retained: select-all adds only this page, keeping the earlier ids',
     [...api.getSelection()].sort((a, b) => a - b).join(',') === '1,7,8');
}

{
  // onSelectionChange must still fire, since the action bar renders from it.
  const { mount, api, seen } = table({ retainSelection: true });
  await api.load();
  check(mount, 1);
  ok('retained: onSelectionChange still fires', seen.length === 1 && seen[0].has(1));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

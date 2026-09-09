/**
 * Behaviour spec for the data-table's `onRender` hook.
 *
 * Run: node tests/js/data_table_on_render_spec.mjs
 *
 * The Move Tasks bar describes a retained selection *relative to the rows on
 * screen* — "4 tasks selected (3 not shown by the current search)". That count
 * is only correct if it is recomputed whenever the visible rows change, and the
 * hooks that existed before did not cover that:
 *
 *  - `onSelectionChange` fires when the user ticks a box, not when a search
 *    changes which rows are on the page;
 *  - `onStateChange` fires *before* the server fetch resolves, so `getRows()`
 *    still returns the previous page at that moment.
 *
 * The reported bug: select 3, search, select a 4th, then clear the search. All
 * 4 rows are back on screen, but the bar still reads "3 not shown" because
 * nothing had recomputed it since the last tick.
 */
import { parseDocument } from './dom-shim.mjs';

const url = new URL('../../frontend/js/components/data-table.js', import.meta.url);
const { createDataTable } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

const tick = () => new Promise((r) => setTimeout(r, 0));
// setQuery debounces at 300ms in server mode.
const settle = () => new Promise((r) => setTimeout(r, 360));

/** Four named rows; the query filters them server-side, as the real API does. */
function movableTable(onRender) {
  const mount = parseDocument();
  const all = [
    { id: 1, name: 'alpha.png' },
    { id: 2, name: 'beta.png' },
    { id: 3, name: 'gamma.png' },
    { id: 4, name: 'delta.png' },
  ];
  const fetchPage = async (q) => {
    const items = q.query
      ? all.filter((r) => r.name.includes(q.query))
      : all.slice();
    return { items, total: items.length, page: 1, page_size: q.pageSize, total_pages: 1 };
  };
  const api = createDataTable({
    mount,
    columns: [{ key: 'name', label: 'Name' }],
    rowId: (r) => r.id,
    selectable: true,
    retainSelection: true,
    server: { fetchPage },
    onRender,
  });
  return { mount, api };
}

/** How many selected ids are absent from the rows currently rendered. */
const offPage = (api) => {
  const visible = new Set(api.getRows().map((r) => r.id));
  return [...api.getSelection()].filter((id) => !visible.has(id)).length;
};

// --- the reported sequence -------------------------------------------------

{
  let api = null;
  let seen = null;                       // offPage as of the most recent render
  const { mount, api: t } = movableTable(() => {
    if (api) seen = offPage(api);
  });
  api = t;

  // The shim does not toggle `checked` for us, so set it then fire `change` —
  // the same two steps a real checkbox click produces.
  const check = (id) => {
    const cb = mount.querySelectorAll('[data-role="row"]')
      .find((box) => box.dataset.id === String(id));
    cb.checked = true;
    cb.dispatch('change');
  };

  await api.load();

  // 1. Select three of the four tasks. All are on screen, so nothing is hidden.
  check(1); check(2); check(3);
  ok('nothing is off-page while every selected row is visible', seen === 0);

  // 2. Search "delta": the three selected rows drop out of view.
  api.setQuery('delta');
  await settle();
  ok('the search hides the three already-selected rows', api.getRows().length === 1);
  ok('the bar is told three are off-page', seen === 3);

  // 3. Select the fourth, which is the only row on screen.
  check(4);
  ok('four are selected in total', api.getSelection().size === 4);
  ok('three are still off-page', seen === 3);

  // 4. Clear the search. All four rows are back on screen, so the count must
  //    fall to zero — this is the bug: without onRender nothing recomputed it
  //    and the bar kept reading "3 not shown".
  api.setQuery('');
  await settle();
  ok('clearing the search brings all four rows back', api.getRows().length === 4);
  ok('no row is reported off-page once the search is cleared', seen === 0);
}

// --- the hook fires for the other row-changing paths ------------------------

{
  let renders = 0;
  const { api } = movableTable(() => { renders++; });
  await api.load();

  const afterLoad = renders;
  api.setFilter('status', 'Approved');
  await tick();
  ok('a filter change re-renders and notifies', renders > afterLoad);

  const afterFilter = renders;
  api.clearSelection();
  ok('clearing the selection notifies', renders > afterFilter);
}

// --- absent hook is harmless ------------------------------------------------

{
  const { api } = movableTable(undefined);
  await api.load();
  ok('a table without onRender still loads', api.getRows().length === 4);
}

console.log(`
${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

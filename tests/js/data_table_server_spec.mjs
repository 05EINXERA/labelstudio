/**
 * Behaviour spec for the data-table's server-side mode.
 *
 * Run: node tests/js/data_table_server_spec.mjs  (or via tests/test_data_table_server.py)
 *
 * Server mode exists because the Tasks view now pages in SQL
 * (.devnotes/tasks-pagination/PLAN.md § 3.3). The failure modes it has to avoid
 * are all silent ones:
 *
 *  - re-sorting or re-filtering the returned page *locally*, which would order
 *    10 rows against each other instead of against the other 3,990;
 *  - reporting `rows.length` as the total, so a 4,000-task project reads
 *    "10 entries";
 *  - applying a slow response that a newer click has already superseded, which
 *    lands the user on a page they navigated away from;
 *  - stranding the viewer past the end after a delete or a narrowing filter.
 *
 * Client mode must be entirely unaffected — that is covered by
 * data_table_pager_spec.mjs, which runs against the same module.
 */
import { parseDocument } from './dom-shim.mjs';

const url = new URL('../../frontend/js/components/data-table.js', import.meta.url);
const { createDataTable } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

const tick = () => new Promise((r) => setTimeout(r, 0));

/** A server-mode table over `total` synthetic rows, 10 per page. */
function serverTable(total, opts = {}) {
  const mount = parseDocument();
  const calls = [];
  let gate = null;                       // set to hold responses open

  const fetchPage = async (q) => {
    calls.push({ ...q });
    if (gate) await gate;
    const start = (q.page - 1) * q.pageSize;
    const items = [];
    for (let i = start; i < Math.min(start + q.pageSize, total); i++) {
      items.push({ id: i + 1, name: `task ${i + 1}` });
    }
    return {
      items,
      total,
      page: q.page,
      page_size: q.pageSize,
      total_pages: Math.max(1, Math.ceil(total / q.pageSize)),
    };
  };

  const api = createDataTable({
    mount,
    columns: [{ key: 'name', label: 'Name' }],
    rowId: (r) => r.id,
    server: { fetchPage },
    ...opts,
  });
  return { mount, api, calls, setGate: (g) => { gate = g; } };
}

const numbers = (mount) =>
  mount.querySelectorAll('.pager-num').map((n) => n.text.trim()).join(' ');
const current = (mount) =>
  mount.querySelectorAll('.pager-num.is-current').map((n) => n.text.trim()).join(',');
const bodyRows = (mount) =>
  mount.querySelectorAll('tbody tr').map((r) => r.text.trim());
const info = (mount) => mount.querySelector('.data-table-info').text.trim();
const click = (mount, sel) => mount.querySelector(sel).click();

// --- totals come from the server ------------------------------------------

{
  const { mount, api } = serverTable(4000);
  await api.load();
  ok('pager reflects the server total, not the page length',
     numbers(mount) === '1 2 3 400');
  ok('info line reports the server total', info(mount) === 'Showing 1 to 10 of 4000 entries');
  ok('only one page of rows is rendered', bodyRows(mount).length === 10);
}

// --- no local re-sorting or re-filtering ----------------------------------

{
  // The server returns rows in a deliberate order; the table must render them
  // as given even though a client-mode sort would reorder them.
  const mount = parseDocument();
  const served = [{ id: 3, name: 'zebra' }, { id: 1, name: 'alpha' }, { id: 2, name: 'mango' }];
  const api = createDataTable({
    mount,
    columns: [{ key: 'name', label: 'Name' }],
    rowId: (r) => r.id,
    sortKey: 'name',
    server: {
      fetchPage: async () => ({ items: served, total: 300, page: 1, page_size: 10, total_pages: 30 }),
    },
  });
  await api.load();
  ok('rows render in server order despite a sortKey',
     bodyRows(mount).map((t) => t.trim()).join(',') === 'zebra,alpha,mango');
}

{
  // A query that matches nothing locally must not blank the page: the server
  // already applied it and returned what it returned.
  const mount = parseDocument();
  const api = createDataTable({
    mount,
    columns: [{ key: 'name', label: 'Name' }],
    rowId: (r) => r.id,
    server: {
      fetchPage: async () => ({
        items: [{ id: 1, name: 'alpha' }], total: 1, page: 1, page_size: 10, total_pages: 1,
      }),
    },
  });
  await api.load();
  ok('server rows are not re-filtered locally', bodyRows(mount).length === 1);
}

// --- navigation re-fetches -------------------------------------------------

{
  const { mount, api, calls } = serverTable(4000);
  await api.load();
  calls.length = 0;

  click(mount, '[data-role="next"]');
  await tick();
  ok('next requests page 2', calls.at(-1).page === 2);
  ok('next renders page 2 rows', bodyRows(mount)[0].includes('task 11'));

  click(mount, '[data-role="last"]');
  await tick();
  ok('last requests the real final page, not MAX_SAFE_INTEGER',
     calls.at(-1).page === 400);
  ok('last marks page 400 current', current(mount) === '400');

  click(mount, '[data-role="first"]');
  await tick();
  ok('first returns to page 1', calls.at(-1).page === 1);
}

{
  const { mount, api, calls } = serverTable(4000);
  await api.load();
  calls.length = 0;
  click(mount, '[data-page="3"]');
  await tick();
  ok('a number click requests that page', calls.at(-1).page === 3);
  ok('a number click renders that page', bodyRows(mount)[0].includes('task 21'));
}

{
  // Clicking the page you are already on should not spend a request.
  const { mount, api, calls } = serverTable(4000);
  await api.load();
  calls.length = 0;
  const first = mount.querySelector('[data-role="first"]');
  ok('first is disabled on page 1', first.disabled);
  first.click();
  await tick();
  ok('a disabled arrow issues no request', calls.length === 0);
}

// --- sorting re-fetches from page 1 ---------------------------------------

{
  const { mount, api, calls } = serverTable(4000);
  await api.load();
  click(mount, '[data-page="3"]');
  await tick();
  calls.length = 0;

  mount.querySelector('th.is-sortable').click();
  await tick();
  ok('sorting asks the server, not the client', calls.length === 1);
  ok('sorting resets to page 1', calls.at(-1).page === 1);
  ok('sorting passes the sort key', calls.at(-1).sortKey === 'name');
}

// --- stale responses are discarded ----------------------------------------

{
  const { mount, api, calls, setGate } = serverTable(4000);
  await api.load();

  // Hold every response open, fire two navigations, then release. The first
  // (page 2) resolves last; the table must still show page 3.
  let release;
  setGate(new Promise((r) => { release = r; }));
  click(mount, '[data-role="next"]');        // page 2
  await tick();
  click(mount, '[data-page="3"]');           // page 3, supersedes it
  await tick();
  release();
  await tick();
  await tick();

  ok('two requests were issued', calls.filter((c) => c.page === 2 || c.page === 3).length === 2);
  ok('the superseded response is discarded', current(mount) === '3');
  ok('the rendered rows are the newest page', bodyRows(mount)[0].includes('task 21'));
}

// --- clamping past the end -------------------------------------------------

{
  // Restoring `page=99` from a stale bookmark on a 5-row project: the table
  // must clamp to the last page and re-fetch, not sit on an empty page.
  const mount = parseDocument();
  const calls = [];
  const api = createDataTable({
    mount,
    columns: [{ key: 'name', label: 'Name' }],
    rowId: (r) => r.id,
    server: {
      fetchPage: async (q) => {
        calls.push(q.page);
        const items = q.page === 1 ? [{ id: 1, name: 'only' }] : [];
        return { items, total: 1, page: q.page, page_size: 10, total_pages: 1 };
      },
    },
  });
  api.setInitialState({ page: 99 });
  await api.load();
  await tick();

  ok('an out-of-range page is re-requested after clamping',
     calls[0] === 99 && calls.at(-1) === 1);
  ok('the clamped page renders its rows', bodyRows(mount)[0].includes('only'));
  ok('the pager settles on the last real page', current(mount) === '1');
}

// --- failures surface ------------------------------------------------------

{
  const mount = parseDocument();
  const api = createDataTable({
    mount,
    columns: [{ key: 'name', label: 'Name' }],
    rowId: (r) => r.id,
    server: { fetchPage: async () => { throw new Error('Could not load tasks (500).'); } },
  });
  await api.load();
  ok('a failed fetch shows its message rather than "no rows"',
     bodyRows(mount).join(' ').includes('Could not load tasks (500).'));
}

// --- state is reported for the URL ----------------------------------------

{
  const seen = [];
  const { mount, api } = serverTable(4000, { onStateChange: (s) => seen.push(s) });
  await api.load();
  click(mount, '[data-page="3"]');
  await tick();

  ok('onStateChange reports the new page', seen.at(-1).page === 3);
  ok('getState agrees with it', api.getState().page === 3);
  ok('getState carries the totals', api.getState().total === 4000);
}

// --- the whole view is restorable in one pass ------------------------------

{
  // The Tasks view restores page, sort, search and filters off the URL. All of
  // it has to arrive in the *one* fetch that follows: going through
  // setQuery()/setFilter() instead would fire a request each and, because those
  // setters mean "the user just changed this", reset to page 1 and throw the
  // restored page away.
  const { api, calls } = serverTable(4000, {
    onStateChange: () => { throw new Error('setInitialState must not emit onStateChange'); },
  });
  api.setInitialState({
    page: 4,
    sortKey: 'status',
    sortDesc: true,
    query: 'cat',
    filters: { status: 'Approved', team: '7' },
  });
  await api.load();

  ok('restoring the whole view costs exactly one fetch', calls.length === 1);
  ok('the restored page is the one requested', calls[0].page === 4);
  ok('the restored sort reaches the server',
     calls[0].sortKey === 'status' && calls[0].sortDesc === true);
  ok('the restored search reaches the server', calls[0].query === 'cat');
  ok('the restored filters reach the server',
     calls[0].filters.status === 'Approved' && calls[0].filters.team === '7');
  ok('getState reports the restored filters', api.getState().filters.status === 'Approved');
}

{
  // Merged, not assigned: restoring one filter must not wipe another.
  const { api } = serverTable(50);
  api.setInitialState({ filters: { status: 'Approved' } });
  api.setInitialState({ filters: { team: '7' } });
  ok('filters merge across calls',
     api.getState().filters.status === 'Approved' && api.getState().filters.team === '7');
}

{
  // Omitting a key leaves it alone, so the existing page-only callers keep
  // working unchanged.
  const { api } = serverTable(50);
  api.setInitialState({ query: 'cat', filters: { status: 'Approved' } });
  api.setInitialState({ page: 2 });
  ok('an omitted query is left alone', api.getState().query === 'cat');
  ok('omitted filters are left alone', api.getState().filters.status === 'Approved');
}

{
  // An explicitly empty query clears, rather than being ignored as falsy.
  const { api } = serverTable(50);
  api.setInitialState({ query: 'cat' });
  api.setInitialState({ query: '' });
  ok('an empty query clears the search', api.getState().query === '');
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

/**
 * Behaviour spec for the data-table pager.
 *
 * Run: node tests/js/data_table_pager_spec.mjs  (or via tests/test_data_table_pager.py)
 *
 * The pager replaced two Previous/Next buttons with first/prev, numbered pages
 * and next/last. Two things are easy to get wrong and are guarded here:
 *
 *  1. The number window. With 400 tasks the control must not render 40 buttons,
 *     but it also must not elide a *single* page behind an ellipsis (which is
 *     wider than the number it hides) or drop the first/last page.
 *
 *  2. Clamping. "Last" deliberately overshoots and lets pageInfo() clamp, and
 *     a shrinking filter can strand state.page past the end. Either bug shows
 *     as an empty table on a page that reports rows.
 *
 * `data-table.js` builds rows as an HTML string and re-binds listeners on every
 * render, so the spec drives the real module through a minimal DOM shim rather
 * than testing a reimplementation of the windowing.
 */
import { parseDocument } from './dom-shim.mjs';

const url = new URL('../../frontend/js/components/data-table.js', import.meta.url);
const { createDataTable } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

/** A table over `n` synthetic rows, 10 per page. */
function table(n, opts = {}) {
  const mount = parseDocument();
  const rows = Array.from({ length: n }, (_, i) => ({ id: i + 1, name: `task ${i + 1}` }));
  const api = createDataTable({
    mount,
    columns: [{ key: 'name', label: 'Name' }],
    rowId: (r) => r.id,
    ...opts,
  });
  api.setRows(rows);
  return { mount, api };
}

/** The pager's visible labels, e.g. ['«','‹','1','2','…','20','›','»']. */
const pagerLabels = (mount) =>
  mount.querySelectorAll('.data-table-pager .pager-btn, .data-table-pager .pager-gap')
    .map((n) => n.text.trim());

const numbers = (mount) =>
  mount.querySelectorAll('.pager-num').map((n) => n.text.trim()).join(' ');

const current = (mount) =>
  mount.querySelectorAll('.pager-num.is-current').map((n) => n.text.trim()).join(',');

const click = (mount, sel) => mount.querySelector(sel).click();

const bodyRows = (mount) =>
  mount.querySelectorAll('tbody tr').map((r) => r.text.trim());

// --- 1. the number window -------------------------------------------------

{
  const { mount } = table(5);           // one page
  ok('single page shows only page 1', numbers(mount) === '1');
  ok('single page has no ellipsis', !pagerLabels(mount).includes('…'));
}

{
  const { mount } = table(30);          // three pages, all shown
  ok('three pages show 1 2 3', numbers(mount) === '1 2 3');
}

{
  const { mount } = table(200);         // 20 pages, on page 1
  ok('20 pages from page 1 windows to 1 2 3 … 20', numbers(mount) === '1 2 3 20');
  ok('20 pages from page 1 has one ellipsis',
     pagerLabels(mount).filter((l) => l === '…').length === 1);
  ok('first and last are always present',
     numbers(mount).startsWith('1 ') && numbers(mount).endsWith(' 20'));
}

{
  const { mount } = table(200);
  click(mount, '[data-page="3"]');      // walk inward to the middle
  click(mount, '[data-page="5"]');
  click(mount, '[data-page="7"]');
  ok('mid-range windows two either side', numbers(mount) === '1 5 6 7 8 9 20');
  ok('mid-range has two ellipses',
     pagerLabels(mount).filter((l) => l === '…').length === 2);
}

{
  const { mount } = table(70);          // 7 pages; page 3 leaves page 6 alone
  click(mount, '[data-page="3"]');
  ok('never elides a single page behind an ellipsis', numbers(mount) === '1 2 3 4 5 7');
}

// --- 2. navigation and clamping -------------------------------------------

{
  const { mount } = table(200);
  ok('page 1 marks itself current', current(mount) === '1');
  ok('first is disabled on page 1', mount.querySelector('[data-role="first"]').disabled);
  ok('prev is disabled on page 1', mount.querySelector('[data-role="prev"]').disabled);
  ok('next is enabled on page 1', !mount.querySelector('[data-role="next"]').disabled);

  click(mount, '[data-role="last"]');
  ok('last clamps to the final page', current(mount) === '20');
  ok('last page shows its rows', bodyRows(mount).length === 10);
  ok('last page shows the final row', bodyRows(mount).at(-1).includes('task 200'));
  ok('next is disabled on the last page', mount.querySelector('[data-role="next"]').disabled);
  ok('last is disabled on the last page', mount.querySelector('[data-role="last"]').disabled);

  click(mount, '[data-role="prev"]');
  ok('prev steps back one', current(mount) === '19');
  click(mount, '[data-role="first"]');
  ok('first returns to page 1', current(mount) === '1');
  ok('page 1 shows the first row', bodyRows(mount)[0].includes('task 1'));
}

{
  // Only windowed pages are clickable, so reach 12 via the last page — which
  // is always offered — rather than assuming an off-window number is rendered.
  const { mount } = table(200);
  click(mount, '[data-role="last"]');
  click(mount, '[data-page="18"]');
  ok('a number jump moves the slice', bodyRows(mount)[0].includes('task 171'));
  ok('a number jump marks the new page current', current(mount) === '18');
}

{
  // A filter that shrinks the result set must not strand the viewer on a page
  // past the end, showing an empty table over a non-zero count.
  const { mount, api } = table(200);
  click(mount, '[data-role="last"]');
  api.setQuery('task 7');               // 'task 7', 'task 7x', 'task 17'… < 20 pages
  ok('a shrinking filter resets to page 1', current(mount) === '1');
  ok('a shrinking filter still renders rows', bodyRows(mount).length > 0);

  api.setQuery('');
  click(mount, '[data-role="last"]');
  api.setRows(Array.from({ length: 15 }, (_, i) => ({ id: i + 1, name: `t${i + 1}` })));
  ok('shrinking the rows clamps the page', current(mount) === '2');
  ok('the clamped page renders its rows', bodyRows(mount).length === 5);
}

{
  const { mount } = table(0);
  ok('an empty table stays on page 1', current(mount) === '1');
  ok('an empty table disables every arrow',
     ['first', 'prev', 'next', 'last']
       .every((r) => mount.querySelector(`[data-role="${r}"]`).disabled));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

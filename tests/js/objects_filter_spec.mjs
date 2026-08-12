/**
 * Behaviour spec for the Objects panel's row filters.
 *
 * Run: node tests/js/objects_filter_spec.mjs
 *
 * `frontend/js/objects-filter.js` decides which annotations the Objects panel
 * lists (the current selection, or the hidden ones, or all of them) and how
 * many are hidden. Two things are guarded here:
 *
 *  1. The precedence and grouping rules — a filter that quietly drops the wrong
 *     rows makes the panel lie about what is in the image.
 *
 *  2. **That filtering cannot reach the saved annotation set.** This is the one
 *     that matters. `syncToBackend()` and `saveDraft()` serialise
 *     `state.annotations`; if a filter ever mutated the row list or the
 *     annotations behind it, a user who clicked one object and pressed Ctrl+S
 *     would save only that object over the rest of their work. The module is
 *     pure precisely so this is cheap to assert
 *     (.devnotes/object-selection/01_DESIGN.md § 5).
 *
 * The module imports nothing, so no DOM shim is needed.
 */
const url = new URL('../../frontend/js/objects-filter.js', import.meta.url);
const { visibleRows, hiddenRowCount } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

// A row list shaped like buildRows() produces: five rows, of which rows 2 and 3
// are the two members of one group (collapsed to a single row), plus a comment.
const mk = (id, extra = {}) => ({ id, type: 'box', ...extra });
const a1 = mk('a1');
const g1 = mk('g1', { groupId: 'G' });
const g2 = mk('g2', { groupId: 'G' });
const a4 = mk('a4');
const c5 = mk('c5', { type: 'comment' });

const rows = [
  { annotation: a1, isGroup: false, groupAnns: [a1], index: 1 },
  { annotation: g1, isGroup: true, groupAnns: [g1, g2], index: 2 },
  { annotation: a4, isGroup: false, groupAnns: [a4], index: 3 },
  { annotation: c5, isGroup: false, groupAnns: [c5], index: 4 }
];

const idsOf = (list) => list.map((r) => r.annotation.id);
const none = new Set();

// 1. No selection, no filter: the list passes through untouched.
console.log('\nunfiltered');
{
  const out = visibleRows(rows, { selectedIds: none, hiddenFilterActive: false });
  ok('returns every row', out.length === rows.length);
  ok('preserves order', idsOf(out).join() === 'a1,g1,a4,c5');
  ok('preserves the row objects', out.every((r, i) => r === rows[i]));
  ok('preserves the permanent numbering', out.map((r) => r.index).join() === '1,2,3,4');
}

// 2. Selection filter (Feature 1).
console.log('\nselection filter');
{
  const out = visibleRows(rows, { selectedIds: new Set(['a4']), hiddenFilterActive: false });
  ok('lists only the selected row', idsOf(out).join() === 'a4');
  // The whole point of assigning index in buildRows(): the selected object keeps
  // its real position rather than being renumbered to 1.
  ok('keeps the selected row real number', out[0].index === 3);
}
{
  const out = visibleRows(rows, { selectedIds: new Set(['a1', 'a4']), hiddenFilterActive: false });
  ok('shift-selection lists exactly those rows', idsOf(out).join() === 'a1,a4');
}
{
  // A group renders as one row standing for every member, so selecting any
  // member must keep that row — including the members that are not the
  // representative the row was built from.
  const out = visibleRows(rows, { selectedIds: new Set(['g2']), hiddenFilterActive: false });
  ok('group row survives when a non-representative member is selected',
    idsOf(out).join() === 'g1');
}

// 3. Hidden filter (Feature 2).
console.log('\nhidden filter');
{
  const hidden = new Set(['g1', 'a4']);
  const isHidden = (a) => hidden.has(a.id);
  const out = visibleRows(rows, { selectedIds: none, hiddenFilterActive: true, isHidden });
  ok('lists only hidden rows', idsOf(out).join() === 'g1,a4');

  const shown = visibleRows(rows, { selectedIds: none, hiddenFilterActive: false, isHidden });
  ok('inactive filter lists everything', shown.length === rows.length);

  // Reachable by un-hiding the last object with the filter still on. The panel
  // renders "No hidden objects" for this rather than "No annotations yet".
  const empty = visibleRows(rows, {
    selectedIds: none, hiddenFilterActive: true, isHidden: () => false
  });
  ok('nothing hidden yields an empty list', empty.length === 0);
}

// 4. Precedence: selection beats the hidden filter (01_DESIGN.md § 2.1).
console.log('\nprecedence');
{
  const isHidden = (a) => a.id === 'g1';
  const out = visibleRows(rows, {
    selectedIds: new Set(['a4']), hiddenFilterActive: true, isHidden
  });
  ok('selection wins over an active hidden filter', idsOf(out).join() === 'a4');
  // Selecting a hidden object must still show it — the row is the only way to
  // un-hide it, and hiding does not deselect.
  const sel = visibleRows(rows, {
    selectedIds: new Set(['g1']), hiddenFilterActive: false, isHidden
  });
  ok('a selected hidden object is still listed', idsOf(sel).join() === 'g1');
}

// 5. The hidden count.
console.log('\nhidden count');
{
  ok('counts hidden rows', hiddenRowCount(rows, (a) => a.id === 'a1' || a.id === 'a4') === 2);
  ok('counts a group once', hiddenRowCount(rows, (a) => a.groupId === 'G') === 1);
  ok('excludes comments', hiddenRowCount(rows, (a) => a.id === 'c5') === 0);
  ok('nothing hidden counts zero', hiddenRowCount(rows, () => false) === 0);
  ok('missing predicate counts zero', hiddenRowCount(rows, undefined) === 0);
}

// 6. THE SAFETY TEST — filtering never mutates the input.
//
// If this fails, a filtered panel can corrupt what gets saved.
console.log('\nno mutation of the annotation set');
{
  const before = JSON.stringify(rows);
  const combos = [
    { selectedIds: none, hiddenFilterActive: false },
    { selectedIds: new Set(['a4']), hiddenFilterActive: false },
    { selectedIds: new Set(['g2']), hiddenFilterActive: true, isHidden: () => true },
    { selectedIds: none, hiddenFilterActive: true, isHidden: (a) => a.id === 'a1' },
    { selectedIds: new Set(['a1', 'a4']), hiddenFilterActive: true, isHidden: () => false }
  ];
  let newArrays = true;
  for (const combo of combos) {
    const out = visibleRows(rows, combo);
    if (out === rows) newArrays = false;
    hiddenRowCount(rows, combo.isHidden || (() => false));
  }
  ok('always returns a new array, never the input', newArrays);
  ok('leaves the row list and its annotations untouched', JSON.stringify(rows) === before);
  ok('row count is unchanged after filtering', rows.length === 4);
}

// 7. Defensive: a missing predicate must not hide real work.
console.log('\ndegenerate input');
{
  const out = visibleRows(rows, { selectedIds: none, hiddenFilterActive: true });
  ok('active filter with no predicate shows everything', out.length === rows.length);
  ok('a non-array yields an empty list', visibleRows(null, {}).length === 0);
  ok('missing options are tolerated', visibleRows(rows).length === rows.length);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

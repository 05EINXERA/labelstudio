/**
 * Behaviour spec for the Objects panel as a whole.
 *
 * Run: node tests/js/objects_panel_spec.mjs
 *
 * `objects_filter_spec.mjs` tests the filter rules in isolation, against hand-
 * built rows. This one wires them to the REAL `state.js` — the real
 * `isAnnotationHidden` (which combines the per-object and per-class hide sets),
 * the real `selectedId` setter (which cascades a group selection), and the real
 * `resetWorkspaceForNewImage`. Those interactions are where the two features
 * actually live:
 *
 *   - clicking one member of a group must list the group's single row;
 *   - a class-level hide must be counted by the header badge, not just an
 *     individual one;
 *   - deselecting must fall back to the *hidden* view when that filter is on,
 *     not to everything (01_DESIGN.md § 2.1);
 *   - and none of it may touch `state.annotations`, which is what gets saved.
 *
 * `buildRows()` is duplicated here rather than imported: the real one lives in
 * components/workspace.js, which pulls in canvas contexts and the whole DOM
 * surface. The copy is a handful of lines and is kept identical to the
 * original; if the grouping rule changes, both move. The alternative — no
 * integration coverage at all — is worse.
 *
 * state.js touches window/localStorage at import time and pulls in view.js,
 * which constructs an Image(), so all three browser globals are stubbed (same
 * arrangement as history_spec.mjs).
 */
globalThis.window = { location: { origin: 'http://test' } };
globalThis.Image = class { constructor(){ this.naturalWidth=0; this.naturalHeight=0; } };
globalThis.localStorage = { _d:new Map(), getItem(k){return this._d.has(k)?this._d.get(k):null;}, setItem(k,v){this._d.set(k,String(v));}, removeItem(k){this._d.delete(k);} };

const st = await import(new URL('../../frontend/js/state.js', import.meta.url));
const { visibleRows, hiddenRowCount } =
  await import(new URL('../../frontend/js/objects-filter.js', import.meta.url));
const { state, isAnnotationHidden, resetWorkspaceForNewImage } = st;

// Mirror of buildRows() in components/workspace.js — see the note above.
function buildRows() {
  const pg = new Set(); const rows = [];
  state.annotations.forEach((a) => {
    if (a.groupId) { if (pg.has(a.groupId)) return; pg.add(a.groupId); }
    const isGroup = !!a.groupId;
    rows.push({ annotation: a, isGroup,
      groupAnns: isGroup ? state.annotations.filter(x => x.groupId === a.groupId) : [a],
      index: rows.length + 1 });
  });
  return rows;
}
const panel = () => {
  const rows = buildRows();
  const shown = visibleRows(rows, { selectedIds: state.selectedIds,
    hiddenFilterActive: state.hiddenFilterActive, isHidden: isAnnotationHidden });
  return { total: rows.length, hidden: hiddenRowCount(rows, isAnnotationHidden),
           labels: shown.map(r => `${r.index}.${r.annotation.id}`) };
};
let pass=0, fail=0;
const ok=(n,c)=>{c?(pass++,console.log('  PASS',n)):(fail++,console.log('  FAIL',n));};

state.labels = [{id:'L1',name:'car',color:'#000'},{id:'L2',name:'tree',color:'#111'}];
state.annotations = [
  {id:'a1',type:'box',labelId:'L1'}, {id:'a2',type:'box',labelId:'L2'},
  {id:'g1',type:'box',labelId:'L1',groupId:'G'}, {id:'g2',type:'box',labelId:'L1',groupId:'G'},
  {id:'a5',type:'box',labelId:'L2'},
];
const snapshotBefore = JSON.stringify(state.annotations);

console.log('\ndefault: all objects listed');
let p = panel();
ok('4 rows (group collapsed)', p.total === 4);
ok('lists all', p.labels.join() === '1.a1,2.a2,3.g1,4.a5');
ok('hidden count 0', p.hidden === 0);

console.log('\nFeature 1: selection filters');
state.selectedId = 'a5';
p = panel();
ok('only the selected object', p.labels.join() === '4.a5');
ok('keeps real number 4', p.labels[0].startsWith('4.'));
ok('header total still 4', p.total === 4);

console.log('\nselecting a group member selects the group');
state.selectedId = 'g2';
p = panel();
ok('group row shown once', p.labels.join() === '3.g1');
ok('selectedIds cascaded to both members', state.selectedIds.has('g1') && state.selectedIds.has('g2'));

console.log('\nunselect restores the full list');
state.selectedId = null;
p = panel();
ok('back to all 4', p.labels.join() === '1.a1,2.a2,3.g1,4.a5');

console.log('\nFeature 2: hidden count + filter');
state.hiddenAnnotationIds.add('a1');
p = panel();
ok('count 1 after hiding one', p.hidden === 1);
state.hiddenLabelIds.add('L2');           // hides a2 and a5 by class
p = panel();
ok('class hide included in count', p.hidden === 3);
state.hiddenFilterActive = true;
p = panel();
ok('lists only hidden', p.labels.join() === '1.a1,2.a2,4.a5');
ok('hidden rows keep real numbers', p.labels.join().includes('4.a5'));

console.log('\nprecedence: selection wins over hidden filter');
state.selectedId = 'g1';
p = panel();
ok('shows the selection, not the hidden set', p.labels.join() === '3.g1');
state.selectedId = null;
p = panel();
ok('deselect falls back to hidden-only', p.labels.join() === '1.a1,2.a2,4.a5');

console.log('\nun-hiding everything with the filter on');
state.hiddenAnnotationIds.clear(); state.hiddenLabelIds.clear();
p = panel();
ok('empty result', p.labels.length === 0);
ok('count 0', p.hidden === 0);
ok('but annotations still exist', p.total === 4);

console.log('\nSAFETY: annotations untouched throughout');
ok('state.annotations byte-identical', JSON.stringify(state.annotations) === snapshotBefore);
ok('still 5 annotations in storage', state.annotations.length === 5);

console.log('\ntask switch resets view state');
state.hiddenFilterActive = true; state.hiddenAnnotationIds.add('a1'); state.hiddenLabelIds.add('L1');
resetWorkspaceForNewImage();
ok('filter cleared', state.hiddenFilterActive === false);
ok('hidden annotations cleared', state.hiddenAnnotationIds.size === 0);
ok('hidden classes cleared', state.hiddenLabelIds.size === 0);
ok('labels survive the switch', state.labels.length === 2);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail?1:0);

/**
 * Behaviour spec for the canvas keyboard-shortcut decisions.
 *
 * Run: node tests/js/shortcuts_spec.mjs
 *
 * `frontend/js/shortcuts.js` decides which class a digit key selects and which
 * annotations "H" toggles. Three things are guarded here:
 *
 *  1. Digit -> class index, including that "0" means the tenth class and that
 *     eleventh-and-beyond classes stay unbound.
 *
 *  2. That "H" with an empty selection targets nothing. This is what makes a
 *     highlighted *class* a no-op: an active class lives in state.activeLabelId
 *     and never enters selectedIds, so if hideTargetIds ever returned rows for
 *     an empty selection, pressing H after picking a class would hide objects
 *     the user never selected.
 *
 *  3. That a mixed selection flips in one direction, so H twice returns to
 *     where it started.
 *
 *  4. That a held "H" resolves to one peek rather than a stream of toggles.
 *     This is the flicker fix: the auto-repeat events a held key produces used
 *     to invert the hide on every one of them.
 *
 * The module imports nothing, so no DOM shim is needed.
 */
const url = new URL('../../frontend/js/shortcuts.js', import.meta.url);
const { labelIndexForCode, hideTargetIds, hideTargetIdsWhileDrawing, shouldHide, hideKeyAction, MAX_CLASS_SHORTCUTS } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

console.log('labelIndexForCode');
ok('Digit1 is the first class', labelIndexForCode('Digit1') === 0);
ok('Digit9 is the ninth class', labelIndexForCode('Digit9') === 8);
ok('Digit0 is the tenth class', labelIndexForCode('Digit0') === MAX_CLASS_SHORTCUTS - 1);
ok('Numpad3 matches Digit3', labelIndexForCode('Numpad3') === labelIndexForCode('Digit3'));
ok('letters are not class keys', labelIndexForCode('KeyH') === -1);
ok('empty/absent code is not a class key', labelIndexForCode('') === -1 && labelIndexForCode(undefined) === -1);
// Guards the layout-independence reason for keying on `code`: on AZERTY the
// unshifted "1" key produces "&", so a `.key` lookup would be dead there.
ok('the produced character is never consulted', labelIndexForCode('&') === -1);

console.log('hideTargetIds');
const anns = [
  { id: 'a1' },
  { id: 'g1', groupId: 'G' },
  { id: 'g2', groupId: 'G' },
  { id: 'a4' }
];

ok('empty selection targets nothing (a highlighted class is a no-op)',
  hideTargetIds(new Set(), anns).length === 0);
ok('a null selection targets nothing', hideTargetIds(null, anns).length === 0);
ok('an ungrouped selection is just itself',
  JSON.stringify(hideTargetIds(new Set(['a1']), anns)) === JSON.stringify(['a1']));

const groupTargets = hideTargetIds(new Set(['g1']), anns).sort();
ok('selecting one group member targets the whole group',
  JSON.stringify(groupTargets) === JSON.stringify(['g1', 'g2']));

const mixed = hideTargetIds(new Set(['a1', 'g2']), anns).sort();
ok('a mixed selection expands only its groups',
  JSON.stringify(mixed) === JSON.stringify(['a1', 'g1', 'g2']));

const before = JSON.stringify(anns);
hideTargetIds(new Set(['g1', 'a1']), anns);
ok('annotations are never mutated', JSON.stringify(anns) === before);

console.log('shouldHide');
const hiddenSet = new Set(['a1']);
const isHidden = (id) => hiddenSet.has(id);

ok('a visible object hides', shouldHide(['a4'], 'a4', isHidden) === true);
ok('a hidden object shows', shouldHide(['a1'], 'a1', isHidden) === false);
ok('the primary annotation decides for the batch',
  shouldHide(['a1', 'a4'], 'a4', isHidden) === true);
ok('a primary outside the batch falls back to the first id',
  shouldHide(['a1', 'a4'], 'nope', isHidden) === false);
ok('an empty batch does nothing', shouldHide([], 'a1', isHidden) === false);
ok('a missing predicate defaults to hiding', shouldHide(['a4'], 'a4', null) === true);

// H twice must return to the starting state: with one direction chosen for the
// whole batch, the second press asks the opposite question of the same
// representative. Deciding per-annotation would turn a mixed selection into a
// different mixed selection instead.
const batch = ['a1', 'a4'];
const first = shouldHide(batch, 'a1', isHidden);
batch.forEach((id) => (first ? hiddenSet.add(id) : hiddenSet.delete(id)));
const second = shouldHide(batch, 'a1', isHidden);
ok('H twice reverses itself', first !== second);

// --- hideKeyAction: tap vs hold vs release -----------------------------------
//
// A held key delivers one keydown with repeat=false followed by a stream with
// repeat=true. The old handler toggled on all of them, so the shapes flickered
// at the platform repeat rate. Only the first event may toggle; the rest must
// collapse into a single peek that ends on keyup.
console.log('hideKeyAction');
ok('the first keydown taps',
  hideKeyAction({ type: 'keydown', repeat: false, peeking: false }) === 'toggle');
ok('the first repeat starts a peek',
  hideKeyAction({ type: 'keydown', repeat: true, peeking: false }) === 'peek-start');
ok('further repeats do nothing',
  hideKeyAction({ type: 'keydown', repeat: true, peeking: true }) === 'none');
ok('keyup ends an active peek',
  hideKeyAction({ type: 'keyup', repeat: false, peeking: true }) === 'peek-end');
ok("a tap's keyup does nothing",
  hideKeyAction({ type: 'keyup', repeat: false, peeking: false }) === 'none');
ok('an unrelated event type does nothing',
  hideKeyAction({ type: 'keypress', repeat: false, peeking: false }) === 'none');
ok('a missing argument does nothing', hideKeyAction() === 'none');

// The whole point, stated as a sequence: drive a realistic hold through the
// function the way the handler does, threading `peeking` from one event to the
// next, and assert exactly one peek-start and one peek-end come out of it
// however long the key is held.
const held = [
  { type: 'keydown', repeat: false },
  ...Array.from({ length: 30 }, () => ({ type: 'keydown', repeat: true })),
  { type: 'keyup', repeat: false },
];
let peeking = false;
const actions = held.map((e) => {
  const action = hideKeyAction({ ...e, peeking });
  if (action === 'peek-start') peeking = true;
  if (action === 'peek-end') peeking = false;
  return action;
});
ok('a long hold toggles exactly once',
  actions.filter((a) => a === 'toggle').length === 1);
ok('a long hold starts exactly one peek',
  actions.filter((a) => a === 'peek-start').length === 1);
ok('a long hold ends exactly one peek',
  actions.filter((a) => a === 'peek-end').length === 1);
ok('a long hold leaves no peek open', peeking === false);

// --- hideTargetIdsWhileDrawing ----------------------------------------------
//
// Starting a polygon sets state.selectedId but leaves selectedIds empty, so
// plain hideTargetIds returns [] mid-draw and "H" did nothing at all.
console.log('hideTargetIdsWhileDrawing');
ok('the shape being drawn is the target, selection or not',
  JSON.stringify(hideTargetIdsWhileDrawing(new Set(), [], 'drawing-1')) === '["drawing-1"]');
ok('the shape being drawn wins over a stale selection',
  JSON.stringify(hideTargetIdsWhileDrawing(new Set(['a1']), anns, 'drawing-1')) === '["drawing-1"]');
ok('with nothing being drawn it is plain hideTargetIds',
  JSON.stringify(hideTargetIdsWhileDrawing(new Set(['a1']), anns, null))
  === JSON.stringify(hideTargetIds(new Set(['a1']), anns)));

// --- the full press/hold/release cycle ---------------------------------------
//
// The regression this guards: a hold must leave the hidden set exactly as it
// found it. The first implementation tried to undo the tap by recomputing the
// direction at peek-start, but the tap had already flipped the state, so the
// recomputation agreed with itself and re-hid instead of lifting — releasing
// the key left the object hidden for good.
//
// Modelled here the way interactions.js applies the actions: a sticky set, a
// peek set, and the ids the tap hid.
console.log('press/hold/release cycle');

function runPress({ events, startHidden = [] }) {
  const sticky = new Set(startHidden);
  const peek = new Set();
  const targets = ['a1'];
  let peeking = false;
  let lastTap = null;

  for (const e of events) {
    const action = hideKeyAction({ ...e, peeking });
    if (action === 'toggle') {
      const hide = shouldHide(targets, 'a1', (id) => sticky.has(id));
      lastTap = { ids: targets.slice(), hid: hide };
      targets.forEach((id) => (hide ? sticky.add(id) : sticky.delete(id)));
    } else if (action === 'peek-start') {
      // Reverse the tap in whichever direction it went.
      if (lastTap) lastTap.ids.forEach((id) => (lastTap.hid ? sticky.delete(id) : sticky.add(id)));
      lastTap = null;
      peeking = true;
      targets.forEach((id) => peek.add(id));
    } else if (action === 'peek-end') {
      peeking = false;
      lastTap = null;
      peek.clear();
    }
  }
  // What the canvas actually shows is the union of the two layers.
  const visibleHidden = new Set([...sticky, ...peek]);
  return { sticky, peek, peeking, hidden: visibleHidden.has('a1') };
}

const HOLD = [
  { type: 'keydown', repeat: false },
  { type: 'keydown', repeat: true },
  { type: 'keydown', repeat: true },
  { type: 'keyup', repeat: false },
];
const TAP = [{ type: 'keydown', repeat: false }, { type: 'keyup', repeat: false }];

const heldVisible = runPress({ events: HOLD, startHidden: [] });
ok('holding H on a visible object leaves it visible on release',
  heldVisible.hidden === false);
ok('a hold leaves no sticky hide behind', heldVisible.sticky.size === 0);
ok('a hold leaves no peek behind', heldVisible.peek.size === 0);
ok('a hold leaves no peek flag set', heldVisible.peeking === false);

// Mid-hold the object must actually be hidden, or the gesture does nothing
// visible. Same press, stopped before the keyup.
const midHold = runPress({ events: HOLD.slice(0, 3), startHidden: [] });
ok('the object is hidden while the key is held', midHold.hidden === true);

// A hold that starts on an already-hidden object must also be inert: the tap
// shows it, the hold re-hides it momentarily, and release restores the sticky
// hide it started with.
const heldHidden = runPress({ events: HOLD, startHidden: ['a1'] });
ok('holding H on a hidden object leaves it hidden on release',
  heldHidden.hidden === true);

// The sticky tap is untouched by all of this.
const tapped = runPress({ events: TAP, startHidden: [] });
ok('a tap still hides stickily', tapped.hidden === true && tapped.sticky.has('a1'));
const untapped = runPress({ events: TAP, startHidden: ['a1'] });
ok('a second tap still shows', untapped.hidden === false);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

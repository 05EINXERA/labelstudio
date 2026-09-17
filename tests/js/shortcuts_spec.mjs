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
const { labelIndexForCode, hideTargetIds, hideTargetIdsWhileDrawing, shouldHide, hideKeyAction, drawHideKeyAction, DRAW_PEEK_MS, MAX_CLASS_SHORTCUTS } = await import(url);

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

// --- drawHideKeyAction: the mid-draw timed peek -------------------------------
//
// While a polygon is being drawn a tap must not toggle stickily: the shape has
// no Objects-panel row to un-hide it from and, once invisible, no selection to
// press "H" against. So a tap hides on a timer that reveals it again by itself.
//
// The subtle part is the keyup. A tap's release must NOT end the peek — the
// hide is meant to outlive the key — while a hold's release must. `timed` is
// what tells the two apart.
console.log('drawHideKeyAction');
ok('a mid-draw tap starts a timed peek',
  drawHideKeyAction({ type: 'keydown', repeat: false, peeking: false, timed: false }) === 'peek-timed');
ok("a timed peek's keyup leaves it running",
  drawHideKeyAction({ type: 'keyup', repeat: false, peeking: true, timed: true }) === 'none');
ok('a repeat upgrades a timed peek to a hold',
  drawHideKeyAction({ type: 'keydown', repeat: true, peeking: true, timed: true }) === 'peek-start');
ok('a repeat with no peek yet starts a hold',
  drawHideKeyAction({ type: 'keydown', repeat: true, peeking: false, timed: false }) === 'peek-start');
ok('further repeats during a hold do nothing',
  drawHideKeyAction({ type: 'keydown', repeat: true, peeking: true, timed: false }) === 'none');
ok("a hold's keyup ends it",
  drawHideKeyAction({ type: 'keyup', repeat: false, peeking: true, timed: false }) === 'peek-end');
ok('a keyup with nothing peeking does nothing',
  drawHideKeyAction({ type: 'keyup', repeat: false, peeking: false, timed: false }) === 'none');
ok('an unrelated event type does nothing',
  drawHideKeyAction({ type: 'keypress', repeat: false, peeking: false }) === 'none');
ok('a missing argument does nothing', drawHideKeyAction() === 'none');

// Not pinned to an exact figure — the delay is a tuning knob the team has
// already turned once. What must hold is that it is a real, sane duration:
// long enough to look underneath, short enough not to feel stuck.
ok('the reveal delay is a sane duration',
  Number.isFinite(DRAW_PEEK_MS) && DRAW_PEEK_MS >= 500 && DRAW_PEEK_MS <= 5000);

// A mid-draw tap and release: the shape must still be hidden afterwards, with
// only the timer left to reveal it. This is the requirement in one assertion —
// the non-drawing rules would have ended the peek on the keyup instead.
const tapEvents = [
  { type: 'keydown', repeat: false },
  { type: 'keyup', repeat: false },
];
let peekingD = false, timedD = false;
for (const e of tapEvents) {
  const action = drawHideKeyAction({ ...e, peeking: peekingD, timed: timedD });
  if (action === 'peek-timed') { peekingD = true; timedD = true; }
  if (action === 'peek-start') { peekingD = true; timedD = false; }
  if (action === 'peek-end') { peekingD = false; timedD = false; }
}
ok('a mid-draw tap survives its own keyup', peekingD === true && timedD === true);

// Contrast: the same two events under the non-drawing rules toggle stickily and
// leave no peek. The two gestures must not converge.
let peekingS = false;
for (const e of tapEvents) {
  const action = hideKeyAction({ ...e, peeking: peekingS });
  if (action === 'peek-start') peekingS = true;
  if (action === 'peek-end') peekingS = false;
}
ok('the selection tap is still sticky, not a peek', peekingS === false);

// A mid-draw hold: upgrades to a real hold, and its release does end it.
const holdEvents = [
  { type: 'keydown', repeat: false },
  { type: 'keydown', repeat: true },
  { type: 'keydown', repeat: true },
  { type: 'keyup', repeat: false },
];
let peekingH = false, timedH = false;
const seen = [];
for (const e of holdEvents) {
  const action = drawHideKeyAction({ ...e, peeking: peekingH, timed: timedH });
  seen.push(action);
  if (action === 'peek-timed') { peekingH = true; timedH = true; }
  if (action === 'peek-start') { peekingH = true; timedH = false; }
  if (action === 'peek-end') { peekingH = false; timedH = false; }
}
ok('a mid-draw hold ends on release', peekingH === false);
ok('a mid-draw hold takes over from the timed peek',
  seen[0] === 'peek-timed' && seen.includes('peek-start') && seen[seen.length - 1] === 'peek-end');

// --- a hold while drawing must not disturb earlier hides ----------------------
//
// Reported from the browser: hide object A with a tap, start drawing B, then
// hold "H" — and A came back. With several objects hidden, only the last one
// did, which was the tell: `lastTap` is a single slot each tap overwrites, and
// it survived across gestures. The hold's roll-back then fired against a tap
// from a *previous* press, undoing a hide the user had made deliberately.
//
// The fix stamps each tap with the press that made it, so a repeat only rolls
// back its own press. Modelled here across two separate presses.
console.log('cross-gesture lastTap staleness');

function runScenario({ stickyTap, drawPress }) {
  const sticky = new Set();
  const peek = new Set();
  let peeking = false, timed = false, lastTap = null, pressId = 0;

  const apply = (action, targets) => {
    if (action === 'toggle') {
      const hide = shouldHide(targets, targets[0], (id) => sticky.has(id));
      lastTap = { ids: targets.slice(), hid: hide, press: pressId };
      targets.forEach((id) => (hide ? sticky.add(id) : sticky.delete(id)));
    } else if (action === 'peek-timed') {
      // A mid-draw tap establishes no sticky toggle, so nothing is left armed.
      lastTap = null;
      peeking = true; timed = true;
      targets.forEach((id) => peek.add(id));
    } else if (action === 'peek-start') {
      // Only this press's own tap may be rolled back.
      if (lastTap && lastTap.press === pressId) {
        lastTap.ids.forEach((id) => (lastTap.hid ? sticky.delete(id) : sticky.add(id)));
      }
      lastTap = null;
      peeking = true; timed = false;
      targets.forEach((id) => peek.add(id));
    } else if (action === 'peek-end') {
      peeking = false; timed = false; lastTap = null;
      peek.clear();
    }
  };

  // Press 1: a plain tap on the selection, not drawing.
  for (const e of stickyTap.events) {
    if (e.type === 'keydown' && !e.repeat) pressId += 1;
    apply(hideKeyAction({ ...e, peeking }), stickyTap.targets);
  }

  // Press 2: a hold while a different shape is being drawn.
  for (const e of drawPress.events) {
    if (e.type === 'keydown' && !e.repeat) pressId += 1;
    apply(drawHideKeyAction({ ...e, peeking, timed }), drawPress.targets);
  }

  return { sticky, peek, peeking };
}

const TAP_EVENTS = [{ type: 'keydown', repeat: false }, { type: 'keyup', repeat: false }];
const HOLD_EVENTS = [
  { type: 'keydown', repeat: false },
  { type: 'keydown', repeat: true },
  { type: 'keydown', repeat: true },
  { type: 'keyup', repeat: false },
];

const one = runScenario({
  stickyTap: { events: TAP_EVENTS, targets: ['A'] },
  drawPress: { events: HOLD_EVENTS, targets: ['B'] },
});
ok('an object hidden before drawing stays hidden through a hold',
  one.sticky.has('A'));
ok('the held draw peek is released', one.peek.size === 0 && one.peeking === false);

// Several hidden objects: all of them must survive, not just the older ones.
const many = (() => {
  const sticky = new Set();
  let peeking = false, timed = false, lastTap = null, pressId = 0;
  const peek = new Set();
  const apply = (action, targets) => {
    if (action === 'toggle') {
      const hide = shouldHide(targets, targets[0], (id) => sticky.has(id));
      lastTap = { ids: targets.slice(), hid: hide, press: pressId };
      targets.forEach((id) => (hide ? sticky.add(id) : sticky.delete(id)));
    } else if (action === 'peek-timed') {
      lastTap = null; peeking = true; timed = true;
      targets.forEach((id) => peek.add(id));
    } else if (action === 'peek-start') {
      if (lastTap && lastTap.press === pressId) {
        lastTap.ids.forEach((id) => (lastTap.hid ? sticky.delete(id) : sticky.add(id)));
      }
      lastTap = null; peeking = true; timed = false;
      targets.forEach((id) => peek.add(id));
    } else if (action === 'peek-end') {
      peeking = false; timed = false; lastTap = null; peek.clear();
    }
  };
  // Hide A, then B, then C — three separate taps on three separate selections.
  for (const id of ['A', 'B', 'C']) {
    for (const e of TAP_EVENTS) {
      if (e.type === 'keydown' && !e.repeat) pressId += 1;
      apply(hideKeyAction({ ...e, peeking }), [id]);
    }
  }
  // Now hold H while drawing D.
  for (const e of HOLD_EVENTS) {
    if (e.type === 'keydown' && !e.repeat) pressId += 1;
    apply(drawHideKeyAction({ ...e, peeking, timed }), ['D']);
  }
  return sticky;
})();
ok('every earlier hide survives, not just the older ones',
  many.has('A') && many.has('B') && many.has('C'));

// The roll-back must still work within a single press, or the original
// "released H leaves it hidden" bug comes back.
const samePress = (() => {
  const sticky = new Set();
  let peeking = false, lastTap = null, pressId = 0;
  for (const e of HOLD_EVENTS) {
    if (e.type === 'keydown' && !e.repeat) pressId += 1;
    const action = hideKeyAction({ ...e, peeking });
    if (action === 'toggle') {
      const hide = shouldHide(['X'], 'X', (id) => sticky.has(id));
      lastTap = { ids: ['X'], hid: hide, press: pressId };
      if (hide) sticky.add('X'); else sticky.delete('X');
    } else if (action === 'peek-start') {
      if (lastTap && lastTap.press === pressId) {
        lastTap.ids.forEach((id) => (lastTap.hid ? sticky.delete(id) : sticky.add(id)));
      }
      lastTap = null; peeking = true;
    } else if (action === 'peek-end') {
      peeking = false; lastTap = null;
    }
  }
  return sticky;
})();
ok("a hold still rolls back its own press's tap", samePress.has('X') === false);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

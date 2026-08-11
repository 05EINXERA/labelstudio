/**
 * Behaviour spec for the workspace back arrow's history handling.
 *
 * Run: node tests/js/back_navigation_spec.mjs  (or via tests/test_back_navigation.py)
 *
 * The bug this guards against: the back arrow was a plain <a href>, so clicking
 * it *pushed* a history entry. The stack became
 *
 *     tasks(page 3) -> canvas -> tasks(page 3)
 *
 * and the browser Back button then went *forward* into the canvas again — a
 * loop the annotator could not escape. The arrow now pops history instead of
 * navigating, but only when this tab actually came from the tasks table; a
 * bookmarked or directly-opened canvas has nothing to pop and must still follow
 * its href.
 *
 * `init.js` and `tasks.js` both touch browser globals at import time and cannot
 * be imported under bare node, so the decision logic is restated here against
 * the same contract both sides implement. What is asserted is the rule — when
 * to pop, when to navigate — not the wiring.
 */
let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

const KEY = 'tasks_nav_origin';
const TTL_MS = 60 * 60 * 1000;

/** Mirrors cameFromTasksPage() in init.js. */
function cameFromTasksPage(store, now = Date.now()) {
  let raw;
  try {
    raw = store.getItem(KEY);
  } catch {
    return false;
  }
  if (!raw) return false;
  const age = now - Number(raw);
  return Number.isFinite(age) && age >= 0 && age < TTL_MS;
}

/** Mirrors the arrow's click handler: true = pop history, false = follow href. */
function backArrowPopsHistory(store, event, now = Date.now()) {
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
  if (event.button !== 0) return false;
  return cameFromTasksPage(store, now);
}

/** Mirrors markCanvasNavigation() in tasks.js. */
function marksNavigation(event) {
  if (!event.isTaskLink) return false;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
  return event.button === 0;
}

const storeWith = (value) => {
  const map = new Map(value === undefined ? [] : [[KEY, String(value)]]);
  return {
    getItem: (k) => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => map.set(k, String(v)),
    removeItem: (k) => map.delete(k),
  };
};
const plainClick = { button: 0 };

// --- when the marker is written -------------------------------------------

ok('a plain click on a task link marks the navigation',
   marksNavigation({ isTaskLink: true, button: 0 }) === true);
ok('a click elsewhere in the table does not',
   marksNavigation({ isTaskLink: false, button: 0 }) === false);

for (const mod of ['metaKey', 'ctrlKey', 'shiftKey', 'altKey']) {
  // These open a new tab. That tab has no history to go back through, so
  // telling it otherwise would strand its back arrow.
  ok(`${mod}-click does not mark the navigation`,
     marksNavigation({ isTaskLink: true, button: 0, [mod]: true }) === false);
}
ok('a middle-click does not mark the navigation',
   marksNavigation({ isTaskLink: true, button: 1 }) === false);

// --- when the arrow pops --------------------------------------------------

ok('pops when the canvas was opened from the tasks table',
   backArrowPopsHistory(storeWith(Date.now()), plainClick) === true);
ok('follows the href when there is no marker',
   backArrowPopsHistory(storeWith(undefined), plainClick) === false);

{
  // A tab left open overnight, returned to via a bookmark: the stale marker
  // must not authorise popping history this visit does not own.
  const store = storeWith(Date.now() - (TTL_MS + 1000));
  ok('follows the href when the marker has expired',
     backArrowPopsHistory(store, plainClick) === false);
}

{
  const store = storeWith(Date.now() - (TTL_MS - 1000));
  ok('still pops just inside the TTL', backArrowPopsHistory(store, plainClick) === true);
}

ok('a garbage marker is not trusted',
   backArrowPopsHistory(storeWith('not-a-number'), plainClick) === false);
ok('a future-dated marker is not trusted',
   backArrowPopsHistory(storeWith(Date.now() + 60000), plainClick) === false);

for (const mod of ['metaKey', 'ctrlKey', 'shiftKey', 'altKey']) {
  ok(`${mod}-click on the arrow is left to the browser`,
     backArrowPopsHistory(storeWith(Date.now()), { button: 0, [mod]: true }) === false);
}
ok('a middle-click on the arrow is left to the browser',
   backArrowPopsHistory(storeWith(Date.now()), { button: 1 }) === false);

{
  // Storage disabled (private mode): the arrow must degrade to its href rather
  // than throwing and doing nothing at all.
  const hostile = {
    getItem() { throw new Error('storage disabled'); },
    setItem() { throw new Error('storage disabled'); },
    removeItem() { throw new Error('storage disabled'); },
  };
  ok('storage errors degrade to following the href',
     backArrowPopsHistory(hostile, plainClick) === false);
}

// --- the loop is actually broken ------------------------------------------

{
  // The regression, modelled end to end. Entries are pushed on navigation;
  // popping moves the pointer back instead of appending.
  const stack = ['tasks?page=3'];
  let index = 0;
  const navigate = (url) => { stack.length = index + 1; stack.push(url); index++; };
  const back = () => { if (index > 0) index--; };

  const store = storeWith(undefined);
  // Annotator opens a task from page 3.
  if (marksNavigation({ isTaskLink: true, button: 0 })) store.setItem(KEY, Date.now());
  navigate('canvas');
  ok('opening a task pushes one entry', stack.length === 2 && index === 1);

  // Clicks the app back arrow.
  if (backArrowPopsHistory(store, plainClick)) { store.removeItem(KEY); back(); }
  else navigate('tasks?page=3');

  ok('the arrow returns to the tasks page', stack[index] === 'tasks?page=3');
  ok('the arrow pushed no third entry', stack.length === 2);

  // The browser Back button must now leave the tasks page, not re-enter the
  // canvas. With the old push-based arrow this landed on 'canvas'.
  back();
  ok('browser Back does not return to the canvas', stack[index] !== 'canvas');
  ok('browser Back leaves the tasks page', index === 0);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

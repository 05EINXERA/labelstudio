/**
 * Behaviour spec for the canvas shortcut handler's "is the user typing?" rule.
 *
 * Run: node tests/js/typing_target_spec.mjs
 *
 * `frontend/js/typing-target.js` decides whether a keydown landed in a text
 * entry control, in which case interactions.js must not act on it. The rule
 * used to be `event.target instanceof HTMLInputElement`, which lumped range
 * sliders in with text fields: after clicking the opacity slider the slider
 * kept focus, so H (hide/unhide), the number keys for class changes, Delete
 * and Ctrl+Z all silently stopped working while the shape still looked
 * selected. Nothing on screen explained it.
 *
 * Two properties carry the weight:
 *
 *  1. **A slider is not typing.** This is the reported bug. Also true of the
 *     three AI sliders, which had the same defect long before the opacity one
 *     existed -- that toolbar group is just disabled in this deployment.
 *
 *  2. **A text field still is.** The fix must not overshoot: if a real input
 *     stops blocking shortcuts, typing a class name fires canvas actions on
 *     every letter. That is a far worse bug than the one being fixed, so it
 *     gets more assertions than the slider does.
 *
 * The module reads duck-typed properties rather than using `instanceof`, so
 * plain objects stand in for elements and no DOM shim is needed.
 */
const url = new URL('../../frontend/js/typing-target.js?v=1', import.meta.url);
const { isTypingTarget } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

/** A stand-in for an element, shaped the way the module reads one. */
const el = (tagName, extra = {}) => ({ tagName, ...extra });
const input = (type) => el('INPUT', { type });

// 1. The reported bug.
console.log('\nsliders do not block shortcuts');
{
  ok('a range slider is not a typing target', isTypingTarget(input('range')) === false);
  ok('case in the type is ignored', isTypingTarget(input('RANGE')) === false);
  ok('case in the tag is ignored', isTypingTarget(el('input', { type: 'range' })) === false);
}

// 2. The overshoot guard -- these must all still block.
console.log('\ntext entry still blocks shortcuts');
{
  ok('a text input blocks', isTypingTarget(input('text')) === true);
  ok('an input with NO type blocks (type defaults to text)',
     isTypingTarget(el('INPUT')) === true);
  ok('an input with an empty type blocks', isTypingTarget(input('')) === true);
  ok('a textarea blocks', isTypingTarget(el('TEXTAREA')) === true);
  for (const t of ['search', 'email', 'password', 'url', 'tel', 'number']) {
    ok(`a ${t} input blocks`, isTypingTarget(input(t)) === true);
  }
}

// 3. An unknown input type must default to blocking. Being wrong this way
//    costs a control its arrow keys; being wrong the other way costs a text
//    field every letter.
console.log('\nunknown types default to blocking');
{
  ok('a type this module has never heard of blocks',
     isTypingTarget(input('some-future-type')) === true);
  ok('date blocks', isTypingTarget(input('date')) === true);
  ok('color blocks', isTypingTarget(input('color')) === true);
}

// 4. The other non-typing inputs, for consistency with range.
console.log('\nother non-typing inputs');
{
  for (const t of ['checkbox', 'radio', 'button', 'submit', 'reset', 'file']) {
    ok(`${t} does not block`, isTypingTarget(input(t)) === false);
  }
}

// 5. contenteditable is text entry whatever tag carries it.
console.log('\ncontenteditable');
{
  ok('a contenteditable div blocks',
     isTypingTarget(el('DIV', { isContentEditable: true })) === true);
  ok('a plain div does not block', isTypingTarget(el('DIV')) === false);
  ok('isContentEditable false does not block',
     isTypingTarget(el('DIV', { isContentEditable: false })) === false);
}

// 6. A select consumes letters for type-ahead, so it must block: otherwise a
//    shortcut would both jump the option list and act on the canvas.
console.log('\nselect');
{
  ok('a select blocks', isTypingTarget(el('SELECT')) === true);
}

// 7. The canvas itself, and the elements a shortcut is normally pressed over,
//    must never block -- this is the path the fix exists to keep open.
console.log('\ncanvas and ordinary elements never block');
{
  for (const tag of ['CANVAS', 'BODY', 'DIV', 'BUTTON', 'SPAN', 'LABEL']) {
    ok(`${tag} does not block`, isTypingTarget(el(tag)) === false);
  }
}

// 8. Degenerate input: a keydown with no usable target must not throw, and
//    must not block either -- a thrown guard would take out every shortcut.
console.log('\ndegenerate input');
{
  for (const bad of [null, undefined, 0, '', 'INPUT', false, NaN]) {
    let threw = false, result = null;
    try { result = isTypingTarget(bad); } catch { threw = true; }
    ok(`${String(bad)} does not throw and does not block`, !threw && result === false);
  }
  ok('an object with no tagName does not block', isTypingTarget({}) === false);
  ok('a non-string tagName does not block', isTypingTarget({ tagName: 42 }) === false);
  ok('a non-string type on an input blocks (falls back to text)',
     isTypingTarget({ tagName: 'INPUT', type: 42 }) === true);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

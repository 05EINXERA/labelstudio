/**
 * Behaviour spec for the toolbar's "Vertex : On / Off" pill.
 *
 * Run: node tests/js/vertex_controls_spec.mjs
 *
 * Guards the contract in .devnotes/feat/hide-vertex/01_DESIGN.md D8:
 *  1. The pill shows what is on screen: Off for the sticky hide *and* for a
 *     held "V" (verticesPeekHidden), On otherwise.
 *  2. aria-pressed and the is-on / is-off classes follow the text.
 *  3. A click runs the injected toggle once and hands focus back (blur), so the
 *     next keypress reaches the canvas instead of the button.
 *  4. A page without the pill is a no-op, not a throw.
 *
 * A stub element stands in for the button; the module reads nothing else.
 */
function stubPill() {
  const classes = new Set();
  const attrs = {};
  const listeners = {};
  return {
    textContent: '',
    blurred: 0,
    classList: {
      toggle(name, on) { if (on) classes.add(name); else classes.delete(name); },
      has: (name) => classes.has(name),
    },
    setAttribute(k, v) { attrs[k] = v; },
    getAttribute: (k) => attrs[k],
    addEventListener(type, fn) { listeners[type] = fn; },
    click() { listeners.click?.(); },
    blur() { this.blurred += 1; },
  };
}

let pill = null;
globalThis.document = { getElementById: (id) => (id === 'vertexToggle' ? pill : null) };
globalThis.Image = class {};
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };

// Same ?v= as vertex-controls.js imports, or this would be a second state.
const { state } = await import(new URL('../../frontend/js/state.js?v=13', import.meta.url));
const { initVertexControls, syncVertexPill } = await import(new URL('../../frontend/js/vertex-controls.js', import.meta.url));

let pass = 0, fail = 0;
function ok(name, cond) {
  if (cond) { pass += 1; console.log(`  PASS ${name}`); }
  else { fail += 1; console.log(`  FAIL ${name}`); }
}

// 4. No pill on the page.
let threw = false;
try { initVertexControls(() => {}); syncVertexPill(); } catch { threw = true; }
ok('no pill on the page is a no-op', !threw);

pill = stubPill();
let toggles = 0;
initVertexControls(() => { toggles += 1; state.verticesHidden = !state.verticesHidden; syncVertexPill(); });

ok('defaults to On', pill.textContent === 'Vertex : On');
ok('On carries is-on, not is-off', pill.classList.has('is-on') && !pill.classList.has('is-off'));
ok('On is aria-pressed=true', pill.getAttribute('aria-pressed') === 'true');

pill.click();
ok('click runs the toggle exactly once', toggles === 1);
ok('click hands focus back', pill.blurred === 1);
ok('after click: Off', pill.textContent === 'Vertex : Off');
ok('Off carries is-off, not is-on', pill.classList.has('is-off') && !pill.classList.has('is-on'));
ok('Off is aria-pressed=false', pill.getAttribute('aria-pressed') === 'false');

pill.click();
ok('second click: back to On', pill.textContent === 'Vertex : On' && toggles === 2);

// 1. A hold reads Off while held and On after release, sticky untouched.
state.verticesPeekHidden = true; syncVertexPill();
ok('held V reads Off', pill.textContent === 'Vertex : Off');
state.verticesPeekHidden = false; syncVertexPill();
ok('release reads On again', pill.textContent === 'Vertex : On' && state.verticesHidden === false);

// Sticky hide + hold + release stays Off.
state.verticesHidden = true; state.verticesPeekHidden = true; syncVertexPill();
state.verticesPeekHidden = false; syncVertexPill();
ok('hold over a sticky hide stays Off after release', pill.textContent === 'Vertex : Off');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

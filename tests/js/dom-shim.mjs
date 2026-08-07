/**
 * A tiny DOM good enough to render and drive `data-table.js` under bare node.
 *
 * The frontend has no build step and no JS test runner, so there is no jsdom to
 * lean on. `data-table.js` needs a narrow slice of the DOM: assign `innerHTML`,
 * query by tag / class / attribute, read text, and dispatch clicks. That slice
 * is small enough to implement directly and keeps the spec dependency-free.
 *
 * Deliberately NOT a general DOM. It supports the selector forms the component
 * actually uses (`tag`, `.class`, `[attr="v"]`, `tag.class`, and descendant
 * combinations of those) and nothing more — an unsupported selector throws
 * rather than silently matching nothing, so a spec cannot pass by accident.
 */

// --- parsing --------------------------------------------------------------

const VOID = new Set(['input', 'br', 'hr', 'img', 'meta', 'link']);

class El {
  constructor(tag, attrs = {}) {
    this.tag = tag;
    this.attrs = attrs;
    this.children = [];
    this.parent = null;
    this._listeners = {};
    this._text = '';
  }

  get dataset() {
    const out = {};
    for (const [k, v] of Object.entries(this.attrs)) {
      if (k.startsWith('data-')) {
        out[k.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = v;
      }
    }
    return out;
  }

  get disabled() { return 'disabled' in this.attrs; }

  get checked() { return this._checked ?? 'checked' in this.attrs; }
  set checked(v) { this._checked = !!v; }

  /** Concatenated text of this subtree. */
  get text() {
    return this._text + this.children.map((c) => c.text).join('');
  }

  set innerHTML(html) {
    this.children = parse(html, this);
    // Listeners bound to replaced children are dropped with them, which is
    // exactly what happens in a browser and what the component relies on.
    this._text = '';
  }

  contains(node) {
    for (let n = node; n; n = n.parent) if (n === this) return true;
    return false;
  }

  closest(sel) {
    for (let n = this; n; n = n.parent) if (matches(n, sel)) return n;
    return null;
  }

  addEventListener(type, fn) {
    (this._listeners[type] ||= []).push(fn);
  }

  /** Fires listeners on this node then bubbles, like a real click. */
  dispatch(type, event = {}) {
    const e = { type, target: this, ...event };
    for (let n = this; n; n = n.parent) {
      (n._listeners[type] || []).forEach((fn) => fn.call(n, e));
    }
  }

  click() {
    if (this.disabled) return;   // a disabled button fires nothing
    this.dispatch('click');
  }

  querySelector(sel) { return this.querySelectorAll(sel)[0] ?? null; }

  querySelectorAll(sel) {
    // A comma list is a union, in document order, without duplicates.
    const groups = sel.split(',').map((s) => s.trim()).filter(Boolean);
    const out = [];
    for (const node of descendants(this)) {
      if (groups.some((g) => matchesChain(node, g, this)) && !out.includes(node)) {
        out.push(node);
      }
    }
    return out;
  }
}

function* descendants(root) {
  for (const child of root.children) {
    yield child;
    yield* descendants(child);
  }
}

/** Matches a descendant chain like `.data-table-pager .pager-btn`. */
function matchesChain(node, chain, root) {
  const parts = chain.split(/\s+/).filter(Boolean);
  if (!matches(node, parts.at(-1))) return false;
  let n = node.parent;
  for (let i = parts.length - 2; i >= 0; i--) {
    let found = false;
    for (; n && n !== root.parent; n = n.parent) {
      if (matches(n, parts[i])) { found = true; n = n.parent; break; }
    }
    if (!found) return false;
  }
  return true;
}

const SIMPLE = /^([a-z][a-z0-9]*)?((?:\.[A-Za-z0-9_-]+)*)((?:\[[^\]]+\])*)$/;

function matches(node, sel) {
  const m = SIMPLE.exec(sel.trim());
  if (!m) throw new Error(`dom-shim: unsupported selector: ${sel}`);
  const [, tag, classes, attrs] = m;
  if (tag && node.tag !== tag) return false;
  const have = new Set((node.attrs.class || '').split(/\s+/).filter(Boolean));
  for (const c of classes.split('.').filter(Boolean)) if (!have.has(c)) return false;
  for (const a of attrs.match(/\[[^\]]+\]/g) || []) {
    const am = /^\[([A-Za-z0-9_-]+)(?:=["']?([^"'\]]*)["']?)?\]$/.exec(a);
    if (!am) throw new Error(`dom-shim: unsupported attribute selector: ${a}`);
    const [, name, value] = am;
    if (!(name in node.attrs)) return false;
    if (value !== undefined && node.attrs[name] !== value) return false;
  }
  return true;
}

const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'" };
const decode = (s) => s.replace(/&(#39|amp|lt|gt|quot);/g, (_, e) => ENTITIES[e]);

/** Minimal HTML parser: tags, attributes, text. No comments or CDATA. */
function parse(html, parent) {
  const roots = [];
  const stack = [];
  const add = (node) => {
    const top = stack.at(-1);
    node.parent = top ?? parent;
    (top ? top.children : roots).push(node);
  };

  const re = /<\/?([a-zA-Z][a-zA-Z0-9]*)((?:[^<>"']|"[^"]*"|'[^']*')*)\/?>|([^<]+)/g;
  let m;
  while ((m = re.exec(html))) {
    const [raw, tag, attrText, text] = m;
    if (text !== undefined) {
      const t = decode(text);
      if (t.trim() && stack.length) stack.at(-1)._text += t;
      else if (t.trim()) { const n = new El('#text'); n._text = t; add(n); }
      continue;
    }
    if (raw.startsWith('</')) {
      for (let i = stack.length - 1; i >= 0; i--) {
        if (stack[i].tag === tag) { stack.length = i; break; }
      }
      continue;
    }
    const node = new El(tag, parseAttrs(attrText));
    add(node);
    if (!VOID.has(tag) && !raw.endsWith('/>')) stack.push(node);
  }
  return roots;
}

function parseAttrs(text) {
  const attrs = {};
  const re = /([A-Za-z_:][-A-Za-z0-9_:.]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
  let m;
  while ((m = re.exec(text || ''))) {
    const [, name, dq, sq, bare] = m;
    attrs[name] = decode(dq ?? sq ?? bare ?? '');
  }
  return attrs;
}

/** A detached element usable as a `mount`. */
export function parseDocument(tag = 'div') {
  return new El(tag);
}

/**
 * Spec for list-view thumbnails (frontend/js/thumb-url.js and the thumbnail
 * cell in pages/project/task-columns.js).
 *
 * Run: node tests/js/thumb_url_spec.mjs (or via tests/test_thumb_url.py)
 *
 * The Tasks and Move tables used to draw 40 px thumbnails from ~10 MB
 * originals: ~99 MB per 10-row page per annotator
 * (.devnotes/frontend-telemetry/07_TASKS_PAGE_THUMBNAILS.md). The regression
 * guard is simple and absolute: nothing a list view renders may point at
 * /uploads/ — not the cell, not the edit preview, and not the error fallback.
 */
import { readFileSync } from 'node:fs';

const { thumbUrl, installThumbFallback, THUMB_PLACEHOLDER } = await import(
  new URL('../../frontend/js/thumb-url.js?v=1', import.meta.url));
const { buildColumns } = await import(
  new URL('../../frontend/js/pages/project/task-columns.js', import.meta.url));

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

const UUID = '0123456789abcdef0123456789abcdef';

// --- thumbUrl ---------------------------------------------------------------
ok('stored path -> /thumbs/<name>', thumbUrl(`uploads/${UUID}.jpg`) === `/thumbs/${UUID}.jpg`);
ok('windows separators', thumbUrl('uploads\\' + UUID + '.png') === `/thumbs/${UUID}.png`);
ok('leading slash', thumbUrl(`/uploads/${UUID}.jpg`) === `/thumbs/${UUID}.jpg`);
ok('no image -> null', thumbUrl(null) === null && thumbUrl('') === null);
ok('name is URL-encoded', thumbUrl('uploads/a b.jpg') === '/thumbs/a%20b.jpg');

// --- the table cell ---------------------------------------------------------
const cols = buildColumns({
  role: 'owner', projectId: 7, teamsById: new Map(), usersById: new Map(),
  lockCache: {}, currentUser: { id: 1 }, viewState: () => null,
});
const thumbCol = cols.find((c) => c.key === 'image_path');
const html = thumbCol.render({ id: 1, image_path: `uploads/${UUID}.jpg` });
ok('cell uses /thumbs/', html.includes(`src="/thumbs/${UUID}.jpg"`));
ok('cell NEVER points at /uploads/', !html.includes('/uploads/'));
ok('cell is lazy and async-decoded', html.includes('loading="lazy"') && html.includes('decoding="async"'));
ok('cell reserves its height', html.includes('height="40"'));
ok('cell is marked for the fallback', html.includes('data-thumb'));
ok('no inline onerror (CSP)', !/onerror/i.test(html));
ok('no image -> empty cell', thumbCol.render({ id: 2, image_path: null }) === '');

// --- the fallback -----------------------------------------------------------
function fakeRoot() {
  const root = { listeners: [], addEventListener(type, fn, capture) { this.listeners.push({ type, fn, capture }); } };
  return root;
}
function fakeImg(src, thumb = true) {
  const classes = new Set();
  const img = {
    src, dataset: thumb ? { thumb: '' } : {},
    classList: { add: (c) => classes.add(c), has: (c) => classes.has(c) },
    getAttribute: (name) => (name === 'src' ? img.src : null),
  };
  return img;
}
{
  const root = fakeRoot();
  installThumbFallback(root);
  installThumbFallback(root);
  ok('one listener however often it is installed', root.listeners.length === 1);
  ok('listens for error in the capture phase', root.listeners[0].type === 'error' && root.listeners[0].capture === true);
  const img = fakeImg(`/thumbs/${UUID}.jpg`);
  root.listeners[0].fn({ target: img });
  ok('failed thumb becomes the placeholder', img.src === THUMB_PLACEHOLDER);
  ok('placeholder is not an upload', !img.src.includes('/uploads/'));
  ok('failed thumb is flagged', img.classList.has('task-thumb--missing'));
  const other = fakeImg('/logo.png', false);
  root.listeners[0].fn({ target: other });
  ok('unrelated images are left alone', other.src === '/logo.png');
  img.src = THUMB_PLACEHOLDER;
  root.listeners[0].fn({ target: img });
  ok('placeholder failing does not loop', img.src === THUMB_PLACEHOLDER);
  // The edit preview sits in the template with src="" until opened; Chrome
  // fires `error` for that. Found in a headless run: it was flagged missing.
  const preview = fakeImg('');
  root.listeners[0].fn({ target: preview });
  ok('empty src is not a failed thumbnail', preview.src === '' && !preview.classList.has('task-thumb--missing'));
}
ok('placeholder is an inline image', THUMB_PLACEHOLDER.startsWith('data:image/svg+xml,'));

// --- source guard: the old URL construction is gone from list views ---------
for (const file of ['pages/project/tasks.js', 'pages/project/move.js', 'pages/project/task-columns.js']) {
  const src = readFileSync(new URL(`../../frontend/js/${file}`, import.meta.url), 'utf8');
  ok(`${file} builds no /<image_path> original URL`,
    !/["'`]\/["'`]\s*\+\s*String\([^)]*image_path/.test(src) && !/src="\/\$\{[^}]*image_path/.test(src));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

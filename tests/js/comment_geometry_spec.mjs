/**
 * Behaviour spec for comment dot/pill geometry and hit-testing.
 *
 * Run: node tests/js/comment_geometry_spec.mjs
 *
 * A comment annotation is a bare image-space point — no width/height, no
 * points — drawn as a fixed-screen-size dot plus a text pill. Before this
 * module existed, draw.js painted those two shapes and hitTest() tested
 * something else entirely (a bbox whose missing width/height collapsed to zero
 * area), so a comment was selectable only by hitting one exact image pixel and
 * the pill was not clickable at all.
 *
 * What is guarded here:
 *
 *  1. **The pill is a hit target.** The regression that motivated the module:
 *     clicking the visible comment body must select it, not just the dot.
 *
 *  2. **Dot and pill stay fixed-size under zoom.** They are painted in screen
 *     space; if the geometry ever picked up `scale`, the click target would
 *     drift away from the painted shape as the annotator zooms.
 *
 *  3. **Pan and zoom move the anchor correctly** — the dot tracks the image
 *     point through the imageBox transform.
 *
 * The module imports nothing and takes its text measurer as a parameter, so no
 * DOM or canvas shim is needed.
 */
const url = new URL('../../frontend/js/canvas/comment-geometry.js', import.meta.url);
const {
  commentScreenGeometry, commentHitTest, commentPillText,
  COMMENT_DOT_RADIUS, COMMENT_PILL_HEIGHT
} = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

// A stand-in for ctx.measureText: 7px per character. Deterministic, and enough
// to make a long comment produce a proportionally wider pill.
const measure = (text) => text.length * 7;

const comment = { id: 'c1', type: 'comment', x: 100, y: 50, text: 'needs a tighter box', author: 'ana' };
const identity = { x: 0, y: 0, scale: 1 };

console.log('\ncomment geometry');
{
  const { dot, pill, text } = commentScreenGeometry(comment, identity, measure);
  ok('dot sits on the image point', dot.cx === 100 && dot.cy === 50);
  ok('dot radius is the painted radius', dot.r === COMMENT_DOT_RADIUS);
  ok('pill text is "author: text"', text === 'ana: needs a tighter box');
  ok('pill is offset right of the dot', pill.x === 112);
  ok('pill is vertically centred on the dot', pill.y === 38 && pill.height === COMMENT_PILL_HEIGHT);
  ok('pill width follows the measured text', pill.width === measure(text) + 12);
  ok('commentPillText matches', commentPillText(comment) === text);
}

console.log('\npill has no measurer');
{
  const { dot, pill } = commentScreenGeometry(comment, identity, undefined);
  ok('dot is still returned', dot.cx === 100);
  ok('pill degrades to null, not a bogus rect', pill === null);
}

console.log('\nhit-testing the dot');
{
  const hit = (x, y, margin = 0) => commentHitTest({ x, y }, comment, identity, measure, margin);
  ok('dead centre hits', hit(100, 50));
  ok('inside the dot hits', hit(105, 52));
  ok('just outside the dot misses', !hit(100, 40));
  ok('margin widens the dot', hit(100, 42, 6));
  ok('far away misses even with margin', !hit(100, 200, 6));
}

console.log('\nhit-testing the pill (the point of the change)');
{
  const hit = (x, y) => commentHitTest({ x, y }, comment, identity, measure, 0);
  const { pill } = commentScreenGeometry(comment, identity, measure);
  ok('middle of the pill body hits', hit(pill.x + pill.width / 2, 50));
  ok('pill top edge hits', hit(pill.x + 20, pill.y));
  ok('pill bottom edge hits', hit(pill.x + 20, pill.y + pill.height));
  ok('far right end of a long pill hits', hit(pill.x + pill.width - 1, 50));
  ok('past the right end of the pill misses', !hit(pill.x + pill.width + 5, 50));
  ok('above the pill misses', !hit(pill.x + 20, pill.y - 5));
  ok('below the pill misses', !hit(pill.x + 20, pill.y + pill.height + 5));
  ok('gap between dot and pill start is not a hole', hit(pill.x, 50));
}

console.log('\nlonger text means a wider target');
{
  const long = { ...comment, text: 'this comment is considerably longer than the other one' };
  const shortPill = commentScreenGeometry(comment, identity, measure).pill;
  const longPill = commentScreenGeometry(long, identity, measure).pill;
  ok('longer comment yields a wider pill', longPill.width > shortPill.width);
  ok('a point past the short pill hits the long one',
    commentHitTest({ x: shortPill.x + shortPill.width + 30, y: 50 }, long, identity, measure));
}

console.log('\nzoom and pan');
{
  // The anchor moves with the transform...
  const zoomed = { x: 20, y: 10, scale: 3 };
  const { dot, pill } = commentScreenGeometry(comment, zoomed, measure);
  ok('dot follows the imageBox transform', dot.cx === 20 + 100 * 3 && dot.cy === 10 + 50 * 3);

  // ...but the shapes themselves must not scale: they are painted at a fixed
  // screen size, so a scaled hit target would drift off the drawn one.
  const flat = commentScreenGeometry(comment, identity, measure);
  ok('dot radius does not scale with zoom', dot.r === flat.dot.r);
  ok('pill height does not scale with zoom', pill.height === flat.pill.height);
  ok('pill width does not scale with zoom', pill.width === flat.pill.width);
  ok('pill offset from the dot does not scale', pill.x - dot.cx === flat.pill.x - flat.dot.cx);

  ok('clicking the pill still works when zoomed',
    commentHitTest({ x: pill.x + pill.width / 2, y: dot.cy }, comment, zoomed, measure));

  const panned = { x: -250, y: -80, scale: 1 };
  const pannedGeom = commentScreenGeometry(comment, panned, measure);
  ok('pan shifts the anchor', pannedGeom.dot.cx === -150 && pannedGeom.dot.cy === -30);
  ok('clicking the panned pill works',
    commentHitTest({ x: pannedGeom.pill.x + 5, y: pannedGeom.dot.cy }, comment, panned, measure));
  ok('clicking where it used to be misses',
    !commentHitTest({ x: 100, y: 50 }, comment, panned, measure));
}

console.log('\nmissing author falls back');
{
  ok('no author reads as "User"',
    commentPillText({ x: 0, y: 0, text: 'hi' }) === 'User: hi');
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);

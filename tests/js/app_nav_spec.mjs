/**
 * Behaviour spec for the header nav (`frontend/js/components/app-nav.js`).
 *
 * Run: node tests/js/app_nav_spec.mjs  (or via tests/test_app_nav.py)
 *
 * The nav gained a third entry — the annotation manual — which is unlike the
 * other two in three ways that are each easy to regress and invisible until a
 * user hits them:
 *
 *   1. It is **root-relative** (`/manual/`). The manual's own DEPLOY.md
 *      proposed an absolute `http://192.168.110.150:8080/`; baking a LAN
 *      address into the bundle means every page's nav breaks silently the day
 *      the box changes address, and the fix has to be re-shipped through the
 *      `?v=` pin dance (rule 13). This spec fails if a host or port ever
 *      reappears in a nav href.
 *   2. It opens in a **new tab**, because losing an annotator's place mid-task
 *      is the reason they would not consult a rule they are unsure about.
 *      `target="_blank"` without `rel="noopener"` hands the opened page a
 *      handle on this one, so the two are asserted together.
 *   3. It is **never active**. `renderAppNav` takes the current page's key, and
 *      no in-app page corresponds to the manual, so no call can legitimately
 *      mark it current — but a future page keyed "manual" would, and
 *      `aria-current` on a link that is not the current page misreports the
 *      page to a screen reader.
 *
 * `renderAppNav` only assigns `innerHTML` on the element it is handed, so the
 * spec passes a plain object with an `innerHTML` field rather than pulling in
 * the DOM shim. The module's other export (`wireLogout`) needs real event
 * plumbing and is not covered here.
 */
const url = new URL('../../frontend/js/components/app-nav.js', import.meta.url);
const { renderAppNav } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

/** Render with the given active key (and options) and return the HTML string. */
const render = (activeKey, options) => {
  const container = { innerHTML: '' };
  renderAppNav(container, activeKey, options);
  return container.innerHTML;
};

/** The `<a …>` tag whose text is `label`, without its closing tag. */
const linkFor = (html, label) => {
  const m = html.match(new RegExp(`<a[^>]*>${label}</a>`));
  return m ? m[0] : '';
};

// --- 1. the entries exist ---------------------------------------------------

const html = render('projects');

ok('renders all three entries',
   ['Projects', 'Teams', 'Manual'].every((l) => html.includes(`>${l}</a>`)));

ok('manual sits last, after Projects and Teams',
   html.indexOf('>Projects</a>') < html.indexOf('>Teams</a>') &&
   html.indexOf('>Teams</a>') < html.indexOf('>Manual</a>'));

ok('a null container is a no-op rather than a throw',
   (() => { try { renderAppNav(null, 'projects'); return true; } catch { return false; } })());

// --- 2. the manual link's href ----------------------------------------------

const manual = linkFor(html, 'Manual');

ok('manual href is the root-relative /manual/',
   manual.includes('href="/manual/"'));

// The point of vendoring the manual into this app. If someone "fixes" the link
// by pointing it at a separately-hosted copy, this is what catches it.
ok('no nav href carries a scheme, host or port',
   !/href="[^"]*(https?:|\/\/|:\d{2,5})/.test(html));

// --- 3. new-tab behaviour ---------------------------------------------------

ok('manual opens in a new tab', manual.includes('target="_blank"'));

ok('manual carries rel=noopener alongside target=_blank',
   manual.includes('rel="noopener"'));

ok('in-app links do not open in a new tab',
   !linkFor(html, 'Projects').includes('target=') &&
   !linkFor(html, 'Teams').includes('target='));

// --- 4. active state --------------------------------------------------------

ok('the active in-app link is marked active',
   linkFor(html, 'Projects').includes('is-active') &&
   linkFor(html, 'Projects').includes('aria-current="page"'));

ok('a non-active in-app link is not marked active',
   !linkFor(html, 'Teams').includes('is-active') &&
   !linkFor(html, 'Teams').includes('aria-current'));

ok('teams becomes active when it is the current page',
   linkFor(render('teams'), 'Teams').includes('aria-current="page"'));

ok('the manual is never active on the pages that render the nav',
   ['projects', 'teams'].every((k) => {
     const a = linkFor(render(k), 'Manual');
     return !a.includes('is-active') && !a.includes('aria-current');
   }));

// The guard for a future page keyed "manual": external links must not go
// active even when asked to, or the nav would claim the user is on a page they
// are not on.
ok('the manual stays inactive even if its own key is passed as active',
   (() => {
     const a = linkFor(render('manual'), 'Manual');
     return !a.includes('is-active') && !a.includes('aria-current');
   })());

ok('external links are marked with is-external for styling',
   manual.includes('is-external') &&
   !linkFor(html, 'Projects').includes('is-external'));

// --- The admin-only Attendance tab (R5) -------------------------------------
//
// Hiding the tab is a rendering convenience, never a control: every attendance
// endpoint calls require_admin and answers 404 regardless (CLAUDE.md rule 18b).
// What these guard is that the *default* is hidden -- a nav that showed the tab
// to everyone would send annotators to a page that 404s at them.

ok('attendance is hidden by default',
   !render('projects').includes('attendance.html'));

ok('attendance is hidden when isAdmin is explicitly false',
   !render('projects', { isAdmin: false }).includes('attendance.html'));

ok('attendance appears for an admin',
   render('projects', { isAdmin: true }).includes('attendance.html'));

ok('attendance goes active on its own page',
   linkFor(render('attendance', { isAdmin: true }), 'Attendance')
     .includes('is-active'));

ok('a non-admin still gets the ordinary links',
   ['Projects', 'Teams', 'Manual'].every((label) =>
     render('projects').includes(label)));

ok('attendance is an in-app link, not external',
   !linkFor(render('projects', { isAdmin: true }), 'Attendance')
     .includes('target="_blank"'));

// --- My profile (R9) --------------------------------------------------------
//
// Unlike Attendance, this one is for everyone: the endpoint behind it is
// self-scoped by construction and takes no user parameter, so there is
// nothing on it one person could use to see another.

ok('profile is visible to a non-admin',
   render('projects').includes('profile.html'));

ok('profile is visible to an admin too',
   render('projects', { isAdmin: true }).includes('profile.html'));

ok('profile goes active on its own page',
   linkFor(render('profile'), 'My profile').includes('is-active'));

ok('profile is an in-app link, not external',
   !linkFor(render('projects'), 'My profile').includes('target="_blank"'));

ok('profile sits after the admin tab, so the shared links lead',
   render('projects', { isAdmin: true }).indexOf('profile.html') >
   render('projects', { isAdmin: true }).indexOf('attendance.html'));

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

/**
 * Behaviour spec for the client-side permission helpers.
 *
 * Run: node tests/js/permissions_spec.mjs  (or via tests/test_frontend_permissions.py)
 *
 * Two things are guarded here:
 *
 *  1. `frontend/js/permissions.js` ranks roles identically to
 *     `api/permissions.py`. The duplication is deliberate (no build step), so
 *     the ranking is exactly the kind of thing that drifts silently — a client
 *     that thinks `reviewer >= manager` renders buttons every click 403s on.
 *
 *  2. `visibleNavItems` filters the project nav by role. Getting this wrong is
 *     how a viewer ends up staring at an Imports tab that 403s (E-17).
 *
 * Neither is a security boundary — the server enforces everything — but both
 * decide whether the UI is coherent.
 */
const permsUrl = new URL('../../frontend/js/permissions.js', import.meta.url);
const p = await import(permsUrl);

// project-nav.js imports utils.js, which touches no browser globals at import
// time; nothing else needs shimming for these two modules.
const navUrl = new URL('../../frontend/js/components/project-nav.js', import.meta.url);
const nav = await import(navUrl);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

// 1. The ranking mirrors api/permissions.py's _RANK exactly.
ok('rank order matches the server', JSON.stringify(p.RANK) ===
   JSON.stringify({ viewer: 0, annotator: 1, reviewer: 2, manager: 3, owner: 4 }));
ok('team rank order matches the server', JSON.stringify(p.TEAM_RANK) ===
   JSON.stringify({ member: 0, manager: 1, owner: 2 }));

// 2. atLeast at every boundary, in both directions.
ok('viewer >= viewer', p.atLeast('viewer', 'viewer') === true);
ok('viewer < annotator', p.atLeast('viewer', 'annotator') === false);
ok('annotator >= viewer', p.atLeast('annotator', 'viewer') === true);
ok('annotator >= annotator', p.atLeast('annotator', 'annotator') === true);
ok('annotator < reviewer', p.atLeast('annotator', 'reviewer') === false);
ok('reviewer >= annotator', p.atLeast('reviewer', 'annotator') === true);
ok('reviewer < manager', p.atLeast('reviewer', 'manager') === false);
ok('manager >= reviewer', p.atLeast('manager', 'reviewer') === true);
ok('manager < owner', p.atLeast('manager', 'owner') === false);
ok('owner >= manager', p.atLeast('owner', 'manager') === true);
ok('owner >= owner', p.atLeast('owner', 'owner') === true);

// 3. "No role" is not "viewer". A null role must fail every check, or a user
//    whose access was revoked keeps a fully-rendered UI.
ok('null role fails every check', p.atLeast(null, 'viewer') === false);
ok('undefined role fails every check', p.atLeast(undefined, 'viewer') === false);
ok('unknown role fails every check', p.atLeast('superuser', 'viewer') === false);
ok('unknown minimum fails closed', p.atLeast('owner', 'godmode') === false);

// 4. The named helpers agree with atLeast.
ok('canAnnotate: viewer no', p.canAnnotate('viewer') === false);
ok('canAnnotate: annotator yes', p.canAnnotate('annotator') === true);
ok('canReview: annotator no', p.canReview('annotator') === false);
ok('canReview: reviewer yes', p.canReview('reviewer') === true);
ok('canManage: reviewer no', p.canManage('reviewer') === false);
ok('canManage: manager yes', p.canManage('manager') === true);
ok('isOwner: manager no', p.isOwner('manager') === false);
ok('isOwner: owner yes', p.isOwner('owner') === true);

// 5. Team axis is independent of the project axis.
ok('teamAtLeast: member < manager', p.teamAtLeast('member', 'manager') === false);
ok('teamAtLeast: owner >= manager', p.teamAtLeast('owner', 'manager') === true);
ok('canManageTeam: member no', p.canManageTeam('member') === false);
ok('ownsTeam: manager no', p.ownsTeam('manager') === false);
ok('a project manager is not a team manager',
   p.canManage('manager') === true && p.canManageTeam('manager') === true &&
   p.canManageTeam('member') === false);

// 6. Nav filtering per the 04_UI_UX.md § 6.1 table.
const routesFor = (role) => nav.visibleNavItems(role).map((i) => i.route);

ok('viewer sees home/tasks/classes/exports',
   JSON.stringify(routesFor('viewer')) === JSON.stringify(['home', 'tasks', 'classes', 'exports']));
ok('viewer does not see imports', !routesFor('viewer').includes('imports'));
ok('viewer does not see access', !routesFor('viewer').includes('access'));
ok('annotator still does not see imports', !routesFor('annotator').includes('imports'));
ok('reviewer still does not see imports', !routesFor('reviewer').includes('imports'));
ok('manager sees imports', routesFor('manager').includes('imports'));
ok('manager does not see access', !routesFor('manager').includes('access'));
ok('owner sees access', routesFor('owner').includes('access'));
ok('owner sees every item', routesFor('owner').length === nav.NAV_ITEMS.length);

// Exports stay at viewer and imports at manager — the two minimums that look
// wrong and are deliberate (03_API.md § 4.1).
ok('exports readable by a viewer', routesFor('viewer').includes('exports'));
ok('imports withheld from a reviewer', !routesFor('reviewer').includes('imports'));

// 7. A user with no role sees nothing. The router falls back to Home, but the
//    nav itself must not offer routes to someone whose access has gone.
ok('no role renders no nav items', routesFor(null).length === 0);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

/**
 * Spec for the telemetry URL classifier (frontend/js/telemetry/classify.js).
 *
 * Run: node tests/js/telemetry_classify_spec.mjs
 *
 * Every statistic in the network report is grouped by these categories
 * (.devnotes/frontend-telemetry/01_TRAFFIC_INVENTORY.md §5), so a request in
 * the wrong bucket skews a headline number. The load-bearing cases are the
 * task-open chain (detail / lock / image) and the /uploads/ split between the
 * canvas and the Tasks-page list views.
 */
const url = new URL('../../frontend/js/telemetry/classify.js?v=1', import.meta.url);
const { classify, templatePath, routeOf } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};
const is = (m, p, want, opts) => {
  const got = classify(m, p, opts);
  ok(`${m} ${p}${opts ? ' ' + JSON.stringify(opts) : ''} -> ${want} (got ${got})`, got === want);
};

// The canvas task-open chain.
is('POST', '/api/tasks', 'task_save');
is('GET', '/api/tasks/8812', 'task_detail');
is('POST', '/api/tasks/8812/claim', 'lock');
is('DELETE', '/api/tasks/8812/claim', 'lock');
is('POST', '/api/tasks/8812/release-beacon', 'lock');
is('GET', '/api/tasks/lock-status', 'lock');
is('POST', '/api/tasks/8812/heartbeat', 'heartbeat');

// Images: same url, different meaning by page.
is('GET', '/uploads/0123456789abcdef0123456789abcdef.jpg', 'image', { onCanvas: true });
is('GET', '/uploads/0123456789abcdef0123456789abcdef.jpg', 'image_list', { onCanvas: false });
is('GET', '/uploads/0123456789abcdef0123456789abcdef.jpg', 'image_list');
is('GET', '/thumbs/0123456789abcdef0123456789abcdef.jpg', 'thumb');

// Lists and the rest of the inventory.
is('GET', '/api/tasks', 'tasks_list');
is('GET', '/api/tasks/order', 'tasks_order');
is('GET', '/api/tasks/search', 'tasks_search');
is('POST', '/api/tasks/8812/review', 'review');
is('GET', '/api/tasks/8812/reviews', 'review');
is('PATCH', '/api/tasks/8812/assignment', 'assign');
is('POST', '/api/tasks/bulk-assign', 'assign');
is('POST', '/api/tasks/bulk-move', 'bulk');
is('POST', '/api/tasks/bulk-delete', 'bulk');
is('POST', '/api/team/time', 'time_sync');
is('POST', '/api/time-logs/time', 'time_sync');
is('GET', '/api/auth/me', 'auth_me');
is('GET', '/api/labels', 'labels');
is('POST', '/api/labels/bulk', 'labels');
is('GET', '/api/projects', 'projects_list');
is('GET', '/api/projects/3', 'project_detail');
is('GET', '/api/projects/3/metrics', 'project_metrics');
is('GET', '/api/projects/3/grants', 'grants');
is('GET', '/api/projects/3/assignable-members', 'grants');
is('POST', '/api/projects/3/upload', 'import');
is('POST', '/api/exports', 'export');
is('GET', '/api/attendance/me', 'attendance');
is('GET', '/api/teams', 'teams');
is('POST', '/api/detect', 'ai');
is('POST', '/api/telemetry/batch', 'telemetry');
is('GET', '/js/init.js', 'static');
is('GET', '/styles.css', 'static');
is('GET', '/logo.png', 'static');
is('GET', '/manual/img/step1.jpg', 'static');
is('GET', '/uploads/0123456789abcdef0123456789abcdef.png', 'image_list');  // uploads win over the asset rule
is('GET', '/app.html', 'page');
is('GET', '/', 'page');
is('GET', '/api/something-new', 'other');
is('get', '/api/tasks/5', 'task_detail');   // method case-insensitive

// Templating.
const t1 = templatePath('/api/tasks/8812/claim');
ok('template numeric segment', t1.p === '/api/tasks/{id}/claim' && t1.id === '8812');
const t2 = templatePath('/api/projects/3/grants/17');
ok('first id kept, all templated', t2.p === '/api/projects/{id}/grants/{id}' && t2.id === '3');
const t3 = templatePath('/uploads/0123456789abcdef0123456789abcdef.jpg');
ok('upload filename kept verbatim', t3.p === '/uploads/0123456789abcdef0123456789abcdef.jpg' && t3.id === null);
ok('non-numeric api path untouched', templatePath('/api/auth/me').p === '/api/auth/me');

ok('route drops query', routeOf('#/tasks?status=Approved') === '#/tasks');
ok('route empty hash', routeOf('') === '');

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

/**
 * classify.js — URL → telemetry category. Pure: no DOM, no globals.
 *
 * Temporary, part of the network telemetry (.devnotes/frontend-telemetry/,
 * category table in 01_TRAFFIC_INVENTORY.md §5). The report groups every
 * statistic by these keys, so a request that lands in the wrong bucket skews
 * a headline number — tests/js/telemetry_classify_spec.mjs pins the table.
 */

// First match wins. `m` is the method, or '*' for any.
const RULES = [
  ['GET', /^\/api\/tasks\/order(\/|$)/, 'tasks_order'],
  ['GET', /^\/api\/tasks\/search(\/|$)/, 'tasks_search'],
  ['GET', /^\/api\/tasks\/\d+$/, 'task_detail'],
  ['GET', /^\/api\/tasks$/, 'tasks_list'],
  ['POST', /^\/api\/tasks$/, 'task_save'],
  ['POST', /^\/api\/tasks\/\d+\/heartbeat$/, 'heartbeat'],
  ['*', /^\/api\/tasks\/(\d+\/(claim|release-beacon|lock-status)|lock-status)$/, 'lock'],
  ['*', /^\/api\/tasks\/\d+\/reviews?$/, 'review'],
  ['*', /^\/api\/tasks\/(\d+\/assignment|bulk-assign)$/, 'assign'],
  ['*', /^\/api\/tasks\/bulk-/, 'bulk'],
  ['POST', /^\/api\/(team|time-logs)\/time$/, 'time_sync'],
  ['GET', /^\/api\/auth\/me$/, 'auth_me'],
  ['*', /^\/api\/labels/, 'labels'],
  ['GET', /^\/api\/projects$/, 'projects_list'],
  ['GET', /^\/api\/projects\/\d+\/metrics$/, 'project_metrics'],
  ['GET', /^\/api\/projects\/\d+$/, 'project_detail'],
  ['*', /^\/api\/projects\/\d+\/(grants|assignable-members)/, 'grants'],
  ['*', /^\/api\/exports/, 'export'],
  ['*', /^\/api\/(imports?|projects\/\d+\/upload)/, 'import'],
  ['*', /^\/api\/attendance/, 'attendance'],
  ['*', /^\/api\/teams/, 'teams'],
  ['*', /^\/api\/detect/, 'ai'],
  // Probes are measurements, not telemetry's own traffic: keep them.
  ['GET', /^\/api\/telemetry\/probe\/down$/, 'probe_down'],
  ['POST', /^\/api\/telemetry\/probe\/up$/, 'probe_up'],
  ['*', /^\/api\/telemetry/, 'telemetry'],
  ['GET', /^\/thumbs\//, 'thumb'],
  // App assets, including images outside /uploads (e.g. the 341 KB logo.png).
  ['GET', /\.(js|css|png|jpe?g|gif|svg|ico|webp|woff2?)$/, 'static'],
  ['GET', /(\.html|^\/)$/, 'page'],
];

/**
 * The category for one request.
 *
 * `onCanvas` splits /uploads/ in two: a Tasks-page thumbnail and a canvas
 * image are currently the SAME url, and only where it was loaded tells them
 * apart (01_TRAFFIC_INVENTORY.md §3).
 */
export function classify(method, pathname, { onCanvas = false } = {}) {
  const m = String(method || 'GET').toUpperCase();
  const path = String(pathname || '/');
  if (m === 'GET' && path.startsWith('/uploads/')) {
    return onCanvas ? 'image' : 'image_list';
  }
  for (const [rm, re, cat] of RULES) {
    if ((rm === '*' || rm === m) && re.test(path)) return cat;
  }
  return 'other';
}

/**
 * Group a path for statistics: numeric /api/ segments become {id}, and the
 * first one is returned separately so heavy tasks stay identifiable. Upload
 * filenames are kept verbatim (a uuid, no content) so image size can be
 * correlated with download time.
 */
export function templatePath(pathname) {
  const path = String(pathname || '/');
  if (!path.startsWith('/api/')) return { p: path, id: null };
  let id = null;
  const p = path.replace(/\/(\d+)(?=\/|$)/g, (_, digits) => {
    if (id === null) id = digits;
    return '/{id}';
  });
  return { p, id };
}

/** The hash route without its query: "#/tasks?status=x" → "#/tasks". */
export function routeOf(hash) {
  return String(hash || '').split('?')[0];
}

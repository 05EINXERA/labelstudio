/**
 * Spec for the telemetry collector (frontend/js/telemetry/collector.js).
 *
 * Run: node tests/js/telemetry_collector_spec.mjs
 *
 * The collector replaces window.fetch for the whole app. That is only
 * acceptable if it is transparent, so transparency gets the most assertions:
 * the caller receives the very same promise, rejections still reach the
 * caller, the response body is never touched, and uninstall restores the
 * native function. Then the measurement itself: the wrapper/Resource Timing
 * join, error classification, and the /uploads/ canvas-vs-list split.
 *
 * The browser is faked: `env` stands in for window.
 */
const url = new URL('../../frontend/js/telemetry/collector.js?v=1', import.meta.url);
const { install } = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};
const tick = () => new Promise((r) => setTimeout(r, 0));

function makeEnv({ pathname = '/app.html', hash = '' } = {}) {
  let now = 1000;
  const observers = [];
  const env = {
    location: { origin: 'http://lan:8000', href: `http://lan:8000${pathname}${hash}`, pathname, hash },
    document: { visibilityState: 'visible' },
    performance: { now: () => now, timeOrigin: 1_700_000_000_000 },
    setInterval: () => 1,
    clearInterval: () => {},
    PerformanceObserver: class {
      constructor(cb) { this.cb = cb; observers.push(this); }
      observe(opts) { this.type = opts.type; }
      disconnect() { this.gone = true; }
    },
    calls: [],
  };
  env.advance = (ms) => { now += ms; };
  env.deliver = (type, entries) => {
    for (const o of observers) if (o.type === type && !o.gone) o.cb({ getEntries: () => entries });
  };
  env.observers = observers;
  return env;
}

function response(status, headers = {}) {
  const h = new Map(Object.entries(headers).map(([k, v]) => [k.toLowerCase(), v]));
  let bodyRead = false;
  return {
    status,
    headers: { get: (k) => (h.has(k.toLowerCase()) ? h.get(k.toLowerCase()) : null) },
    json() { bodyRead = true; return Promise.resolve({}); },
    clone() { bodyRead = true; return this; },
    get bodyRead() { return bodyRead; },
  };
}

function entry(name, over = {}) {
  return {
    name, initiatorType: 'fetch', startTime: 1000, fetchStart: 1000,
    domainLookupStart: 1000, domainLookupEnd: 1000, connectStart: 1000, connectEnd: 1000,
    requestStart: 1002, responseStart: 1052, responseEnd: 1080, duration: 80,
    transferSize: 5000, encodedBodySize: 4700, decodedBodySize: 20000,
    nextHopProtocol: 'http/1.1', ...over,
  };
}

// --- 1. transparency --------------------------------------------------------
{
  const env = makeEnv();
  const res = response(200, { 'X-Server-Ms': '12.5', 'X-Request-Id': 'r1' });
  const nativePromise = Promise.resolve(res);
  const native = function () { env.calls.push([...arguments]); return nativePromise; };
  env.fetch = native;
  const recs = [];
  const c = install({ onRecord: (r) => recs.push(r), env });

  ok('fetch is replaced', env.fetch !== native);
  const init = { method: 'POST', body: 'x'.repeat(10), headers: { A: '1' } };
  const p = env.fetch('/api/tasks', init);
  ok('caller receives the ORIGINAL promise', p === nativePromise);
  ok('arguments pass through untouched', env.calls[0][0] === '/api/tasks' && env.calls[0][1] === init);
  const got = await p;
  await tick();
  ok('response object is the same one', got === res);
  ok('body never read or cloned', res.bodyRead === false);

  ok('origFetch is the native function', c.origFetch === native);
  c.uninstall();
  ok('uninstall restores native fetch', env.fetch === native);
  ok('observers disconnected', env.observers.every((o) => o.gone));
}
{
  const env = makeEnv();
  const boom = new TypeError('Failed to fetch');
  env.fetch = () => Promise.reject(boom);
  const recs = [];
  install({ onRecord: (r) => recs.push(r), env });
  let caught = null;
  try { await env.fetch('/api/tasks/5'); } catch (e) { caught = e; }
  await tick();
  ok('rejection propagates to caller unchanged', caught === boom);
  ok('network error recorded', recs.length === 1 && recs[0].err === 'neterr' && recs[0].cat === 'task_detail');
}
{
  // A throwing sink must not break the request.
  const env = makeEnv();
  const res = response(200);
  env.fetch = () => Promise.resolve(res);
  install({ onRecord: () => { throw new Error('sink'); }, env });
  const got = await env.fetch('/api/tasks/5');
  env.deliver('resource', [entry('http://lan:8000/api/tasks/5')]);
  ok('throwing sink is contained', got === res);
}
{
  // Cross-origin requests are neither recorded nor disturbed.
  const env = makeEnv();
  const res = response(200);
  env.fetch = () => Promise.resolve(res);
  const recs = [];
  install({ onRecord: (r) => recs.push(r), env });
  await env.fetch('https://cdn.example.com/x.js');
  env.deliver('resource', [entry('https://cdn.example.com/x.js')]);
  await tick();
  ok('cross-origin ignored', recs.length === 0);
}

// --- 2. join ----------------------------------------------------------------
{
  const env = makeEnv();
  const res = response(200, { 'X-Server-Ms': '41.2', 'X-Request-Id': 'abc' });
  env.fetch = () => Promise.resolve(res);
  const recs = [];
  install({ onRecord: (r) => recs.push(r), env });
  const body = new Uint8Array(900);
  await env.fetch('/api/tasks', { method: 'POST', body });
  await tick();
  env.deliver('resource', [entry('http://lan:8000/api/tasks', { startTime: 1003 })]);
  ok('joined into ONE record', recs.length === 1);
  const r = recs[0];
  ok('method and category from the wrapper', r.m === 'POST' && r.cat === 'task_save');
  ok('server time and request id', r.srv === 41.2 && r.rid === 'abc' && r.st === 200);
  ok('compressed request size', r.req === 900);
  ok('phases from resource timing', r.ttfb === 50 && r.dl === 28 && r.stall === 2 && r.tx === 5000);
  ok('epoch timestamp', r.t === 1_700_000_001_003);
}
{
  // Resource entry outside the join window stays separate.
  const env = makeEnv();
  env.fetch = () => Promise.resolve(response(200));
  const recs = [];
  const c = install({ onRecord: (r) => recs.push(r), env });
  await env.fetch('/api/tasks/9');
  env.deliver('resource', [entry('http://lan:8000/api/tasks/9', { startTime: 5000 })]);
  ok('unmatched entry emitted alone', recs.length === 1 && recs[0].srv === undefined);
  env.advance(91_000);
  c.sweep();
  ok('stale pending swept as wrapper-only record', recs.length === 2 && recs[1].srv === null && recs[1].st === 200);
}

// --- 3. errors --------------------------------------------------------------
for (const [elapsed, want] of [[100, 'abort'], [45_000, 'timeout']]) {
  const env = makeEnv();
  const recs = [];
  let rejectIt;
  env.fetch = () => new Promise((_, rej) => { rejectIt = rej; });
  install({ onRecord: (r) => recs.push(r), env });
  const p = env.fetch('/api/tasks/1').catch(() => {});
  env.advance(elapsed);
  const err = new Error('aborted'); err.name = 'AbortError';
  rejectIt(err);
  await p; await tick();
  ok(`AbortError after ${elapsed} ms is ${want}`, recs[0] && recs[0].err === want);
}

// --- 4. images and routes ---------------------------------------------------
{
  const env = makeEnv({ pathname: '/project.html', hash: '#/tasks?status=New' });
  env.fetch = () => Promise.resolve(response(200));
  const recs = [];
  install({ onRecord: (r) => recs.push(r), env });
  env.deliver('resource', [entry('http://lan:8000/uploads/abc.jpg', { initiatorType: 'img', transferSize: 9_900_000 })]);
  ok('image on Tasks page is image_list', recs[0].cat === 'image_list' && recs[0].it === 'img');
  ok('route recorded without query', recs[0].rt === '#/tasks');
  ok('upload filename kept', recs[0].p === '/uploads/abc.jpg');
}
{
  const env = makeEnv({ pathname: '/app.html' });
  env.fetch = () => Promise.resolve(response(200));
  const recs = [];
  install({ onRecord: (r) => recs.push(r), env });
  env.deliver('resource', [entry('http://lan:8000/uploads/abc.jpg', { initiatorType: 'img' })]);
  env.deliver('resource', [entry('http://lan:8000/api/telemetry/batch')]);
  ok('image on canvas is image', recs[0].cat === 'image');
  ok('telemetry\'s own requests never recorded', recs.length === 1);
}
{
  const env = makeEnv();
  env.fetch = () => Promise.resolve(response(200));
  const recs = [];
  install({ onRecord: (r) => recs.push(r), env });
  env.deliver('navigation', [entry('http://lan:8000/app.html', {
    initiatorType: 'navigation', domContentLoadedEventEnd: 900, loadEventEnd: 1500 })]);
  ok('navigation recorded as page boot', recs[0].it === 'nav' && recs[0].cat === 'page' && recs[0].load === 1500);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

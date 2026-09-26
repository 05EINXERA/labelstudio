/**
 * Spec for the telemetry flusher (frontend/js/telemetry/flush.js).
 *
 * Run: node tests/js/telemetry_flush_spec.mjs
 *
 * What must hold: a batch that did not reach the server is kept (bounded) for
 * the next interval; a server that says "no" stops telemetry rather than
 * being retried forever; a 401 never navigates; the unload beacon stays under
 * the keepalive quota and carries the CSRF token in the query string.
 */
globalThis.document = { cookie: 'csrf_token=tok123' };   // read by api.js withCsrfParam

const { startFlushing, newestThatFit } = await import(
  new URL('../../frontend/js/telemetry/flush.js?v=1', import.meta.url));
const { createRing } = await import(
  new URL('../../frontend/js/telemetry/buffer.js?v=1', import.meta.url));

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

function makeEnv() {
  const listeners = {};
  const docListeners = {};
  const env = {
    location: { pathname: '/app.html', hash: '#/x?y=1', href: 'http://lan/app.html' },
    document: {
      cookie: 'a=b; csrf_token=tok123',
      visibilityState: 'visible',
      addEventListener: (t, f) => { docListeners[t] = f; },
      removeEventListener: (t) => { delete docListeners[t]; },
    },
    navigator: { beacons: [], sendBeacon(url, blob) { this.beacons.push({ url, blob }); return env.beaconResult; } },
    beaconResult: true,
    performance: { clearResourceTimings() { env.cleared = true; } },
    setTimeout: (fn, ms) => { env.lastDelay = ms; return 1; },
    clearTimeout: () => {},
    addEventListener: (t, f) => { listeners[t] = f; },
    removeEventListener: (t) => { delete listeners[t]; },
    Blob, Response, CompressionStream,
    listeners, docListeners,
  };
  return env;
}

function setup(fetchImpl, flushSeconds = 300) {
  let stopHook = 0;
  const env = makeEnv();
  const ring = createRing(100);
  const posts = [];
  const origFetch = async function (url, init) { posts.push({ url, init, self: this }); return fetchImpl(); };
  const f = startFlushing({
    ring, env, origFetch,
    config: { flush_seconds: flushSeconds },
    meta: { client: 'c-1', seat: 'A1', getEnv: () => ({ ect: '4g' }) },
    onStop: () => { stopHook += 1; },
  });
  return { env, ring, posts, f, stopHooks: () => stopHook };
}

// Interval is jittered ±10%.
{
  const { env } = setup(() => ({ ok: true, status: 204 }));
  ok('first flush scheduled within 270-330 s', env.lastDelay >= 270_000 && env.lastDelay <= 330_000);
}

// A successful flush: gzipped, CSRF header, native fetch bound to window.
{
  const s = setup(() => ({ ok: true, status: 204 }));
  s.ring.push({ cat: 'heartbeat', dur: 3 });
  s.ring.push({ cat: 'image', dur: 300 });
  await s.f.flushNow();
  const p = s.posts[0];
  ok('posted to the batch endpoint', p && p.url === '/api/telemetry/batch' && p.init.method === 'POST');
  ok('CSRF header from cookie', p.init.headers['X-CSRF-Token'] === 'tok123');
  ok('gzipped', p.init.headers['Content-Encoding'] === 'gzip' && p.init.body instanceof Uint8Array);
  const text = await new Response(new Blob([p.init.body]).stream()
    .pipeThrough(new DecompressionStream('gzip'))).text();
  const batch = JSON.parse(text);
  ok('envelope carries client, seat, page route, env',
    batch.v === 1 && batch.client === 'c-1' && batch.seat === 'A1'
    && batch.page === '/app.html#/x' && batch.env.ect === '4g');
  ok('records shipped', batch.records.length === 2);
  ok('native fetch called with window as receiver', p.self === s.env);
  ok('buffer emptied', s.ring.size === 0);
  ok('resource timing buffer cleared', s.env.cleared === true);
}

// Nothing buffered: no request at all.
{
  const s = setup(() => ({ ok: true, status: 204 }));
  await s.f.flushNow();
  ok('empty buffer sends nothing', s.posts.length === 0);
}

// Unreachable server and 5xx keep the batch; 413 drops it and carries on.
{
  const s = setup(() => { throw new TypeError('Failed to fetch'); });
  s.ring.push({ n: 1 });
  await s.f.flushNow();
  ok('network error keeps the batch', s.ring.size === 1 && !s.f.stopped);
}
{
  const s = setup(() => ({ ok: false, status: 503 }));
  s.ring.push({ n: 1 });
  await s.f.flushNow();
  ok('5xx keeps the batch', s.ring.size === 1 && !s.f.stopped);
}
{
  const s = setup(() => ({ ok: false, status: 413 }));
  s.ring.push({ n: 1 }); s.ring.push({ n: 2 });
  await s.f.flushNow();
  ok('413 drops and counts the batch', s.ring.size === 0 && s.ring.dropped === 2 && !s.f.stopped);
}

// The server saying no stops telemetry; nothing navigates.
for (const status of [400, 401, 403, 404]) {
  const s = setup(() => ({ ok: false, status }));
  s.ring.push({ n: 1 });
  await s.f.flushNow();
  ok(`${status} stops telemetry`, s.f.stopped === true);
  ok(`${status} runs the stop hook once`, s.stopHooks() === 1);
  ok(`${status} removes unload listeners`, !s.env.listeners.pagehide && !s.env.docListeners.visibilitychange);
  ok(`${status} does not navigate`, s.env.location.href === 'http://lan/app.html');
}

// Unload beacon.
{
  const s = setup(() => ({ ok: true, status: 204 }));
  for (let i = 0; i < 5; i++) s.ring.push({ i });
  s.env.listeners.pagehide();
  const b = s.env.navigator.beacons[0];
  ok('pagehide sends a beacon', !!b);
  ok('beacon carries CSRF in the query', b.url === '/api/telemetry/batch?csrf_token=tok123');
  const body = JSON.parse(await b.blob.text());
  ok('beacon is plain JSON with all records', body.records.length === 5);
}
{
  const s = setup(() => ({ ok: true, status: 204 }));
  s.env.document.visibilityState = 'visible';
  s.ring.push({ i: 1 });
  s.env.docListeners.visibilitychange();
  ok('visible transition sends nothing', s.env.navigator.beacons.length === 0);
  s.env.document.visibilityState = 'hidden';
  s.env.beaconResult = false;
  s.env.docListeners.visibilitychange();
  ok('refused beacon puts records back', s.ring.size === 1);
}
{
  // Oversized buffer: only the newest that fit, the rest counted as dropped.
  const s = setup(() => ({ ok: true, status: 204 }));
  const big = 'x'.repeat(1000);
  for (let i = 0; i < 100; i++) s.ring.push({ i, big });
  s.env.listeners.pagehide();
  const text = await s.env.navigator.beacons[0].blob.text();
  const body = JSON.parse(text);
  ok('beacon under 60 KB', text.length < 60_000);
  ok('newest records kept', body.records[body.records.length - 1].i === 99);
  ok('the rest counted as dropped', body.dropped === 100 - body.records.length && body.dropped > 0);
}

const nf = newestThatFit([{ a: 1 }, { a: 2 }, { a: 3 }], 16);
ok('newestThatFit keeps the tail', nf.kept.length === 2 && nf.kept[0].a === 2 && nf.lost === 1);

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

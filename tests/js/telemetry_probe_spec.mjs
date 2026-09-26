/**
 * Spec for the bandwidth probes (frontend/js/telemetry/probe.js).
 *
 * Run: node tests/js/telemetry_probe_spec.mjs
 *
 * The probes put real bytes on the office network, so the rules that keep
 * them polite are what is pinned: never while the page is hidden or a real
 * request is in flight, a random first start, cache-proof and incompressible
 * payloads, CSRF on the upload, and a full stop when the server says off.
 */
const { startProbes, randomBytes } = await import(
  new URL('../../frontend/js/telemetry/probe.js?v=1', import.meta.url));

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

function makeEnv({ status = 200 } = {}) {
  const env = {
    document: { visibilityState: 'visible', cookie: 'csrf_token=tokX' },
    crypto: globalThis.crypto,
    delays: [],
    calls: [],
    setTimeout(fn, ms) { env.delays.push(ms); return env.delays.length; },
    clearTimeout() {},
    async fetch(url, init = {}) {
      env.calls.push({ url, init });
      return { status, arrayBuffer: async () => new ArrayBuffer(8) };
    },
  };
  return env;
}

const config = { probe_seconds: 900, probe_bytes: 4 * 1024 * 1024 };

// First probe: random, but never in the first minute nor after one interval.
{
  const env = makeEnv();
  startProbes({ config, activeRequests: () => 0, env });
  ok('first probe between 60 s and 900 s', env.delays[0] >= 60_000 && env.delays[0] <= 900_000);
}

// A normal probe: download then upload.
{
  const env = makeEnv();
  const p = startProbes({ config, activeRequests: () => 0, env });
  await p.fire();
  const [down, up] = env.calls;
  ok('download requested with full size and a nonce',
    /^\/api\/telemetry\/probe\/down\?n=4194304&r=\w+$/.test(down.url));
  ok('download bypasses the cache', down.init.cache === 'no-store');
  ok('upload posted to probe/up', up && up.url === '/api/telemetry/probe/up' && up.init.method === 'POST');
  ok('upload is a quarter of the download', up.init.body.byteLength === 1024 * 1024);
  ok('upload carries CSRF', up.init.headers['X-CSRF-Token'] === 'tokX');
  ok('upload not labelled gzip', !('Content-Encoding' in up.init.headers));
  ok('next probe in 720-1080 s', env.delays.at(-1) >= 720_000 && env.delays.at(-1) <= 1_080_000);
}

// Two probes never send the same URL (cache-proof).
{
  const env = makeEnv();
  const p = startProbes({ config, activeRequests: () => 0, env });
  await p.fire(); await p.fire();
  ok('nonce differs between probes', env.calls[0].url !== env.calls[2].url);
}

// Busy: in-flight requests or a hidden page defer the probe.
{
  const env = makeEnv();
  const p = startProbes({ config, activeRequests: () => 2, env });
  await p.fire();
  ok('in-flight work defers the probe', env.calls.length === 0 && env.delays.at(-1) === 30_000);
  for (let i = 0; i < 6; i++) await p.fire();
  ok('busy all along: the round is skipped, not forced', env.calls.length === 0 && env.delays.at(-1) >= 720_000);
}
{
  const env = makeEnv();
  env.document.visibilityState = 'hidden';
  const p = startProbes({ config, activeRequests: () => 0, env });
  await p.fire();
  ok('hidden page defers the probe', env.calls.length === 0);
}

// Work that starts during the download cancels the upload.
{
  const env = makeEnv();
  let active = 0;
  const realFetch = env.fetch;
  env.fetch = async (url, init) => { const r = await realFetch(url, init); active = 1; return r; };
  const p = startProbes({ config, activeRequests: () => active, env });
  await p.fire();
  ok('upload skipped when real work began', env.calls.length === 1);
}

// Server switched probes off: stop for good.
{
  const env = makeEnv({ status: 404 });
  const p = startProbes({ config, activeRequests: () => 0, env });
  const scheduled = env.delays.length;
  await p.fire();
  ok('404 stops the probes', p.stopped === true && env.delays.length === scheduled);
}

// Disabled by size: nothing scheduled.
{
  const env = makeEnv();
  startProbes({ config: { probe_seconds: 900, probe_bytes: 0 }, activeRequests: () => 0, env });
  ok('zero bytes schedules nothing', env.delays.length === 0);
}

// Payload is random across the 64 KB getRandomValues limit.
{
  const b = randomBytes({ crypto: globalThis.crypto }, 200_000);
  const tail = b.subarray(150_000);
  ok('random bytes fill past 64 KB', b.length === 200_000 && tail.some((x) => x !== 0));
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

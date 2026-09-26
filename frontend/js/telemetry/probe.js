/**
 * probe.js — optional bandwidth probes (TELEMETRY_PROBES_ENABLED).
 *
 * Temporary, part of the network telemetry (.devnotes/frontend-telemetry/
 * 02_DESIGN.md §3.5). Passive timings show what a seat got; these show what
 * it CAN get: a fixed download and a fixed upload of incompressible bytes.
 *
 * They go through the (wrapped) window.fetch on purpose, so the collector
 * records them like any request — Resource Timing phases plus X-Server-Ms —
 * under their own categories (probe_down / probe_up). For the upload,
 * X-Server-Ms covers reading the body, so throughput = bytes / srv.
 *
 * A probe must never compete with real work. It is skipped while the page is
 * hidden or any request is in flight, and retried shortly after; the first
 * one fires at a random point in the first interval so a room of browsers
 * opened together does not probe together.
 */
const RETRY_BUSY_MS = 30_000;
const MAX_BUSY_RETRIES = 6;
const MIN_FIRST_MS = 60_000;

function readCookie(doc, name) {
  const match = String(doc.cookie || '').match(new RegExp('(?:^|;\\s*)' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[1]) : null;
}

/** Incompressible bytes. getRandomValues fills at most 64 KB per call. */
export function randomBytes(env, n) {
  const out = new Uint8Array(n);
  for (let i = 0; i < n; i += 65536) {
    env.crypto.getRandomValues(out.subarray(i, Math.min(n, i + 65536)));
  }
  return out;
}

export function startProbes({ config, activeRequests, env = globalThis }) {
  const periodMs = Math.max(60, config.probe_seconds || 900) * 1000;
  const downBytes = config.probe_bytes || 0;
  const upBytes = Math.floor(downBytes / 4);
  let timer = null;
  let stopped = false;
  let busyRetries = 0;

  const busy = () =>
    env.document.visibilityState === 'hidden' || (activeRequests && activeRequests() > 0);

  function scheduleIn(ms) {
    if (!stopped) timer = env.setTimeout(fire, ms);
  }

  function nextInterval() {
    scheduleIn(periodMs * (0.8 + Math.random() * 0.4));
  }

  async function runOnce() {
    const nonce = `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 8)}`;
    const down = await env.fetch(`/api/telemetry/probe/down?n=${downBytes}&r=${nonce}`, {
      cache: 'no-store', credentials: 'same-origin',
    });
    if (down.status === 404) return 'off';
    await down.arrayBuffer();                 // the download IS the measurement
    if (busy()) return 'ok';                  // real work started: skip the upload
    const headers = { 'Content-Type': 'application/octet-stream' };
    const csrf = readCookie(env.document, 'csrf_token');
    if (csrf) headers['X-CSRF-Token'] = csrf;
    const up = await env.fetch('/api/telemetry/probe/up', {
      method: 'POST', headers, body: randomBytes(env, upBytes), credentials: 'same-origin',
    });
    return up.status === 404 ? 'off' : 'ok';
  }

  async function fire() {
    if (stopped) return;
    if (busy()) {
      if (busyRetries < MAX_BUSY_RETRIES) {
        busyRetries += 1;
        scheduleIn(RETRY_BUSY_MS);
      } else {                                // busy all along: skip this round
        busyRetries = 0;
        nextInterval();
      }
      return;
    }
    busyRetries = 0;
    try {
      if ((await runOnce()) === 'off') {
        stop();                               // server turned probes off
        return;
      }
    } catch (e) {
      // A failed probe is itself a data point (the collector recorded it).
    }
    nextInterval();
  }

  function stop() {
    stopped = true;
    if (timer !== null) env.clearTimeout(timer);
  }

  if (downBytes > 0) {
    scheduleIn(MIN_FIRST_MS + Math.random() * Math.max(0, periodMs - MIN_FIRST_MS));
  }
  return { fire, stop, get stopped() { return stopped; } };
}

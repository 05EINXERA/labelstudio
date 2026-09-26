/**
 * flush.js — ships buffered telemetry to POST /api/telemetry/batch.
 *
 * Temporary, part of the network telemetry (.devnotes/frontend-telemetry/
 * 02_DESIGN.md §3.4).
 *
 * - Every `flush_seconds` ±10% (jittered so a room of browsers does not flush
 *   together), in idle time, gzipped.
 * - Uses the NATIVE fetch, so the flush neither measures itself nor goes
 *   through apiFetch. That is a deliberate exception to CLAUDE.md rule 16:
 *   apiFetch redirects to the login page on a 401, and telemetry must never
 *   move the user anywhere. A 401 here just stops telemetry for the page.
 * - On unload, whatever fits in 60 KB goes by sendBeacon, uncompressed (a
 *   beacon cannot await a stream). boot.js registers these listeners only
 *   after /config has answered, i.e. after the app's own unload listeners, so
 *   the app's save beacon always queues first and, if the 64 KB keepalive
 *   quota is contested, telemetry's is the one refused.
 */
import { withCsrfParam } from '../api.js?v=5';
import { routeOf } from './classify.js?v=1';

const URL_BATCH = '/api/telemetry/batch';
const BEACON_MAX_BYTES = 60_000;

function readCookie(doc, name) {
  const match = String(doc.cookie || '').match(new RegExp('(?:^|;\\s*)' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[1]) : null;
}

async function gzip(env, text) {
  if (typeof env.CompressionStream === 'undefined') return null;
  try {
    const stream = new env.Blob([text]).stream().pipeThrough(new env.CompressionStream('gzip'));
    return new Uint8Array(await new env.Response(stream).arrayBuffer());
  } catch (e) {
    return null;   // send plain; the server accepts both
  }
}

/** The newest records whose JSON fits in `budget` bytes. */
export function newestThatFit(records, budget) {
  let used = 0;
  let i = records.length;
  while (i > 0) {
    const size = JSON.stringify(records[i - 1]).length + 1;
    if (used + size > budget) break;
    used += size;
    i -= 1;
  }
  return { kept: records.slice(i), lost: i };
}

export function startFlushing({ ring, config, origFetch, meta, onStop = null, env = globalThis }) {
  const periodMs = Math.max(5, config.flush_seconds || 300) * 1000;
  let timer = null;
  let inFlight = false;
  let stopped = false;

  function envelope(records, dropped) {
    const loc = env.location;
    return {
      v: 1,
      client: meta.client,
      seat: meta.seat || null,
      page: loc.pathname + routeOf(loc.hash),
      sent_at: Date.now(),
      env: meta.getEnv ? meta.getEnv() : null,
      dropped,
      records,
    };
  }

  function schedule() {
    if (stopped) return;
    const delay = periodMs * (0.9 + Math.random() * 0.2);
    timer = env.setTimeout(() => {
      const run = () => { flushNow().finally(schedule); };
      if (typeof env.requestIdleCallback === 'function') env.requestIdleCallback(run, { timeout: 5000 });
      else run();
    }, delay);
  }

  function stop(reason) {
    if (stopped) return;
    stopped = true;
    if (timer !== null) env.clearTimeout(timer);
    env.removeEventListener('pagehide', onPageHide);
    env.document.removeEventListener('visibilitychange', onVisibility);
    if (reason) console.warn('[telemetry] stopped:', reason);
    if (onStop) {
      try { onStop(); } catch (e) { console.warn('[telemetry] stop hook failed', e); }
    }
  }

  async function flushNow() {
    if (stopped || inFlight) return;
    if (ring.size === 0 && ring.dropped === 0) return;
    inFlight = true;
    const batch = ring.take();
    try {
      // Entries are already copied into our buffer; clearing keeps the
      // browser's 250-entry timing buffer from filling. Observers are fed
      // regardless of that buffer.
      try { env.performance.clearResourceTimings(); } catch (e) { /* optional API */ }
      const text = JSON.stringify(envelope(batch.records, batch.dropped));
      const gz = await gzip(env, text);
      const headers = { 'Content-Type': 'application/json' };
      const csrf = readCookie(env.document, 'csrf_token');
      if (csrf) headers['X-CSRF-Token'] = csrf;
      if (gz) headers['Content-Encoding'] = 'gzip';
      let res;
      try {
        res = await origFetch.call(env, URL_BATCH, {
          method: 'POST', headers, body: gz || text, credentials: 'same-origin',
        });
      } catch (e) {
        ring.putBack(batch);          // server unreachable: try next interval
        return;
      }
      if (res.ok) return;
      if (res.status === 413) {       // this batch is too big to ever succeed
        ring.noteDropped(batch.records.length);
      } else if (res.status >= 500) {
        ring.putBack(batch);
      } else {                         // 400/401/403/404: the server said no
        stop(`batch refused with ${res.status}`);
      }
    } finally {
      inFlight = false;
    }
  }

  function beaconFlush() {
    if (stopped || typeof env.navigator.sendBeacon !== 'function') return;
    if (ring.size === 0 && ring.dropped === 0) return;
    const batch = ring.take();
    const { kept, lost } = newestThatFit(batch.records, BEACON_MAX_BYTES - 1000);
    const body = JSON.stringify(envelope(kept, batch.dropped + lost));
    let sent = false;
    try {
      sent = env.navigator.sendBeacon(
        withCsrfParam(URL_BATCH),
        new env.Blob([body], { type: 'application/json' }),
      );
    } catch (e) {
      sent = false;
    }
    // Refused (quota) or failed: if the page survives (a tab switch), the
    // records go back for the next flush; on a real unload they die here.
    if (!sent) ring.putBack({ records: kept, dropped: batch.dropped + lost });
  }

  function onPageHide() { beaconFlush(); }
  function onVisibility() {
    if (env.document.visibilityState === 'hidden') beaconFlush();
  }

  env.addEventListener('pagehide', onPageHide);
  env.document.addEventListener('visibilitychange', onVisibility);
  schedule();

  return { flushNow, beaconFlush, stop, get stopped() { return stopped; } };
}

/**
 * collector.js — observes every same-origin request the page makes.
 *
 * Temporary, part of the network telemetry (.devnotes/frontend-telemetry/
 * 02_DESIGN.md §3.2). Two sources, joined:
 *
 *  - Resource Timing (PerformanceObserver): the browser's own phase timings
 *    and byte counts, for fetches, <img>, scripts, CSS and beacons alike. It
 *    needs no hook in app code, which is why no existing module was edited.
 *  - A thin window.fetch wrapper: the method, status, request id, the
 *    server's X-Server-Ms, the request body size, and — the only source of
 *    them — network errors and timeouts.
 *
 * THE WRAPPER'S CONTRACT: the caller gets the ORIGINAL promise, untouched.
 * Nothing here reads or clones a body (a 14 MB task detail would double in
 * memory), changes a request, or lets an exception reach the caller. Every
 * hook is try/caught; three internal errors uninstall the wrapper.
 *
 * Globals are injectable (`env`) so tests/js/telemetry_collector_spec.mjs can
 * drive it in node.
 */
import { classify, routeOf, templatePath } from './classify.js?v=1';

const JOIN_WINDOW_MS = 50;      // wrapper t0 vs resource startTime
const PENDING_MAX_AGE_MS = 90_000;
const SWEEP_MS = 30_000;
// apiFetch aborts at 45 s (api.js REQUEST_TIMEOUT_MS): an abort that late is
// a timeout, not a user cancel.
const TIMEOUT_THRESHOLD_MS = 44_000;
const MAX_WRAPPER_ERRORS = 3;

const r1 = (n) => (typeof n === 'number' && isFinite(n) ? Math.round(n * 10) / 10 : null);

function bodySize(body) {
  if (body == null) return 0;
  if (typeof body === 'string') return body.length;
  if (typeof body.byteLength === 'number') return body.byteLength;   // Uint8Array, ArrayBuffer
  if (typeof body.size === 'number') return body.size;               // Blob
  return null;                                                        // FormData, streams
}

export function install({ onRecord, env = globalThis } = {}) {
  const win = env;
  const perf = env.performance;
  const origin = env.location.origin;
  const origFetch = env.fetch;
  const pending = new Map();   // absolute url -> [meta]
  const observers = [];
  let wrapperErrors = 0;
  let wrapped = false;
  let sweepTimer = null;

  const onCanvas = () => /\/app\.html$/.test(env.location.pathname || '');
  const hidden = () => env.document && env.document.visibilityState === 'hidden';

  function base(method, absUrl) {
    const u = new URL(absUrl);
    const { p, id } = templatePath(u.pathname);
    const rec = {
      m: method,
      cat: classify(method, u.pathname, { onCanvas: onCanvas() }),
      p,
      rt: routeOf(env.location.hash),
    };
    if (id !== null) rec.id = id;
    if (hidden()) rec.h = 1;
    return rec;
  }

  function emit(rec) {
    if (rec.cat === 'telemetry') return;
    try { onRecord(rec); } catch (e) { /* the sink must never break a request */ }
  }

  // --- wrapper -------------------------------------------------------------

  function noteWrapperError(e) {
    wrapperErrors += 1;
    if (wrapperErrors === 1) console.warn('[telemetry] collector error', e);
    if (wrapperErrors >= MAX_WRAPPER_ERRORS) unwrap();
  }

  function begin(input, init) {
    const raw = typeof input === 'string' || input instanceof URL ? String(input) : input.url;
    const abs = new URL(raw, env.location.href);
    if (abs.origin !== origin) return null;
    const method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    const meta = {
      abs: abs.href,
      t0: perf.now(),
      rec: { ...base(method, abs.href), req: bodySize(init && init.body) },
      joined: false,
    };
    if (meta.rec.cat === 'telemetry') return null;
    const list = pending.get(meta.abs) || [];
    list.push(meta);
    pending.set(meta.abs, list);
    return meta;
  }

  function end(meta, res) {
    const h = res.headers;
    const srv = h && h.get('x-server-ms');
    Object.assign(meta.rec, {
      st: res.status,
      hdr: r1(perf.now() - meta.t0),
      srv: srv != null ? r1(parseFloat(srv)) : null,
      rid: (h && h.get('x-request-id')) || null,
      ce: (h && h.get('content-encoding')) || null,
    });
  }

  function fail(meta, err) {
    const elapsed = perf.now() - meta.t0;
    let kind = 'neterr';
    if (err && err.name === 'AbortError') kind = elapsed >= TIMEOUT_THRESHOLD_MS ? 'timeout' : 'abort';
    Object.assign(meta.rec, { err: kind, dur: r1(elapsed) });
    // Failed requests usually get no Resource Timing entry: emit now.
    removePending(meta);
    if (!meta.joined) {
      meta.rec.t = r1(perf.timeOrigin + meta.t0);
      meta.rec.it = 'fetch';
      emit(meta.rec);
    }
  }

  function removePending(meta) {
    const list = pending.get(meta.abs);
    if (!list) return;
    const i = list.indexOf(meta);
    if (i >= 0) list.splice(i, 1);
    if (!list.length) pending.delete(meta.abs);
  }

  function wrappedFetch(input, init) {
    let meta = null;
    try { meta = begin(input, init); } catch (e) { noteWrapperError(e); }
    // Always against the window: native fetch throws "Illegal invocation" for
    // any other receiver, and app code calls it unbound.
    const p = origFetch.apply(win, arguments);
    if (meta) {
      p.then(
        (res) => { try { end(meta, res); } catch (e) { noteWrapperError(e); } },
        (err) => { try { fail(meta, err); } catch (e) { noteWrapperError(e); } },
      );
    }
    return p;   // the ORIGINAL promise
  }

  function unwrap() {
    if (wrapped && win.fetch === wrappedFetch) win.fetch = origFetch;
    wrapped = false;
  }

  // --- resource timing -----------------------------------------------------

  function fromEntry(e) {
    const method = e.initiatorType === 'beacon' ? 'POST' : 'GET';
    const rec = base(method, e.name);
    const connectEnd = e.connectEnd || e.fetchStart;
    Object.assign(rec, {
      t: r1(perf.timeOrigin + e.startTime),
      it: e.initiatorType,
      dns: r1(e.domainLookupEnd - e.domainLookupStart),
      tcp: r1(e.connectEnd - e.connectStart),
      stall: e.requestStart ? r1(e.requestStart - Math.max(e.fetchStart, connectEnd)) : null,
      ttfb: e.requestStart ? r1(e.responseStart - e.requestStart) : null,
      dl: r1(e.responseEnd - e.responseStart),
      dur: r1(e.duration),
      tx: e.transferSize,
      enc: e.encodedBodySize,
      dec: e.decodedBodySize,
      proto: e.nextHopProtocol || null,
    });
    if (typeof e.responseStatus === 'number' && e.responseStatus) rec.st = e.responseStatus;
    return rec;
  }

  function onResource(e) {
    if (!e.name || !e.name.startsWith(origin)) return;   // cross-origin: phases are zeroed
    const rec = fromEntry(e);
    if (e.initiatorType === 'fetch') {
      const list = pending.get(e.name);
      const meta = list && list.find((m) => Math.abs(m.t0 - e.startTime) <= JOIN_WINDOW_MS);
      if (meta) {
        removePending(meta);
        meta.joined = true;
        // Keep the wrapper's method/category (the entry cannot know a POST),
        // then overlay the timing. `end` may still land later: it writes
        // into the same object, which is still in the buffer.
        const { m, cat, p, id, req } = meta.rec;
        meta.rec = Object.assign(meta.rec, rec, { m, cat, p, req });
        if (id !== undefined) meta.rec.id = id;
        emit(meta.rec);
        return;
      }
    }
    emit(rec);
  }

  function onNavigation(e) {
    const rec = fromEntry(e);
    Object.assign(rec, {
      it: 'nav',
      cat: 'page',
      dcl: r1(e.domContentLoadedEventEnd),
      load: r1(e.loadEventEnd),
    });
    emit(rec);
  }

  function observe(type, fn) {
    if (typeof env.PerformanceObserver !== 'function') return;
    try {
      const obs = new env.PerformanceObserver((list) => {
        for (const entry of list.getEntries()) {
          try { fn(entry); } catch (e) { noteWrapperError(e); }
        }
      });
      obs.observe({ type, buffered: true });
      observers.push(obs);
    } catch (e) {
      console.warn('[telemetry] cannot observe', type, e);
    }
  }

  // Wrapper-only records for anything Resource Timing never matched.
  function sweep() {
    const now = perf.now();
    for (const list of [...pending.values()]) {
      for (const meta of [...list]) {
        if (now - meta.t0 < PENDING_MAX_AGE_MS) continue;
        removePending(meta);
        meta.rec.t = r1(perf.timeOrigin + meta.t0);
        meta.rec.it = 'fetch';
        emit(meta.rec);
      }
    }
  }

  // --- lifecycle -----------------------------------------------------------

  win.fetch = wrappedFetch;
  wrapped = true;
  observe('resource', onResource);
  observe('navigation', onNavigation);
  sweepTimer = env.setInterval ? env.setInterval(sweep, SWEEP_MS) : null;

  return {
    /** The native fetch, for telemetry's own requests (never measured). */
    origFetch,
    sweep,
    uninstall() {
      unwrap();
      for (const obs of observers) {
        try { obs.disconnect(); } catch (e) { /* already gone */ }
      }
      observers.length = 0;
      if (sweepTimer !== null) env.clearInterval(sweepTimer);
      pending.clear();
    },
  };
}

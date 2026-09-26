/**
 * boot.js — entry point of the temporary network telemetry.
 *
 * Loaded by its own <script type="module"> tag, placed BEFORE each page's
 * entry module so it runs first and the page's first request is covered. It
 * imports nothing from the app except two leaf helpers, and no app module
 * imports it: deleting the tag and this folder removes the feature entirely.
 * Design: .devnotes/frontend-telemetry/02_DESIGN.md §3.
 *
 * Sequence:
 *   1. Install the collector at once (cheap; records into memory only).
 *   2. Ask GET /api/telemetry/config with the NATIVE fetch — never apiFetch,
 *      whose 401 handling navigates to the login page (rule 16 exception).
 *   3. Not 200 {enabled:true}: uninstall. window.fetch is native again and
 *      nothing else happens. That is the whole cost of a disabled deploy.
 *   4. Enabled: start flushing (and the optional probes).
 *
 * Escape hatches on one machine, no server change:
 *   localStorage.telemetry_off = '1'   → never installs
 *   ?seat=A12 on any page URL          → labels this browser's batches
 */
import { install } from './collector.js?v=1';
import { createRing } from './buffer.js?v=1';
import { startFlushing } from './flush.js?v=1';
import { clientId } from '../utils.js?v=2';

const SEAT_KEY = 'telemetry_seat';
const OFF_KEY = 'telemetry_off';

function storage(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch (e) {
    // Storage blocked: behave as unset.
  }
  return null;
}

function rememberSeat() {
  try {
    const seat = new URLSearchParams(location.search).get('seat');
    if (seat && /^[A-Za-z0-9_-]{1,32}$/.test(seat)) storage(SEAT_KEY, seat);
  } catch (e) {
    // Malformed URL: no seat label, nothing else affected.
  }
  return storage(SEAT_KEY);
}

function environment() {
  const c = navigator.connection || {};
  const ua = navigator.userAgent.match(/(Edg|Chrome|Firefox|Version)\/(\d+)/);
  return {
    ect: c.effectiveType || null,
    down: typeof c.downlink === 'number' ? c.downlink : null,
    rtt: typeof c.rtt === 'number' ? c.rtt : null,
    cpu: navigator.hardwareConcurrency || null,
    mem: navigator.deviceMemory || null,
    ua: ua ? `${ua[1] === 'Version' ? 'Safari' : ua[1]}/${ua[2]}` : null,
    vis: document.visibilityState,
  };
}

function boot() {
  if (storage(OFF_KEY) === '1') return;
  const seat = rememberSeat();
  const ring = createRing(3000);
  const collector = install({ onRecord: (r) => ring.push(r) });

  collector.origFetch.call(window, '/api/telemetry/config', { credentials: 'same-origin' })
    .then((res) => (res.ok ? res.json() : null))
    .then((config) => {
      if (!config || config.enabled !== true) {
        collector.uninstall();
        return;
      }
      startFlushing({
        ring,
        config,
        origFetch: collector.origFetch,
        meta: { client: clientId(), seat, getEnv: environment },
        // The server refused us: stop observing too, not just sending.
        onStop: () => collector.uninstall(),
      });
      if (config.probes) {
        // Loaded only when switched on: a deployment without probes never
        // downloads this module.
        // A probe failure must not take passive capture down with it.
        return import('./probe.js?v=1')
          .then(({ startProbes }) =>
            startProbes({ config, activeRequests: collector.activeRequests }))
          .catch((e) => console.warn('[telemetry] probes unavailable:', e));
      }
      return null;
    })
    .catch((e) => {
      collector.uninstall();
      console.warn('[telemetry] disabled for this page:', e);
    });
}

try {
  boot();
} catch (e) {
  // Never let telemetry stop the page: the app's own module still loads.
  console.warn('[telemetry] failed to start:', e);
}

/**
 * Connection monitor — tracks whether the workspace can reach the server.
 *
 * Annotation work is autosaved through `apiFetch`, and callers swallow network
 * failures so a blip never interrupts drawing. This module turns those outcomes
 * into an online/offline state that the save-status text consumes
 * (workspace.js shows "Not saved — offline"). The per-task localStorage draft
 * is what actually protects the work while disconnected.
 *
 * Two independent signals feed the state:
 *
 *  1. `navigator.onLine` + the online/offline events — instant, but only ever
 *     reports the local NIC. On the office LAN the machine stays "online" while
 *     the one PC running uvicorn is down, so this alone is not enough.
 *  2. Real request outcomes reported by `apiFetch`. A network-level failure
 *     (fetch reject / abort on timeout) is the authoritative sign the server is
 *     unreachable. HTTP error statuses are NOT failures here — a 403 or 500 is a
 *     server that answered, i.e. a live connection.
 *
 * The state flips to offline after FAILURE_THRESHOLD consecutive failures, or
 * immediately on an `offline` event. While down, an unauthenticated `/health`
 * probe runs on an interval so recovery is detected even if the user has
 * stopped interacting.
 *
 * There is intentionally no floating "Offline" badge: it was removed at the
 * user's request; the save-status text is the only visible signal.
 */

const FAILURE_THRESHOLD = 2;
const PROBE_INTERVAL_MS = 5000;
const PROBE_TIMEOUT_MS = 4000;

let consecutiveFailures = 0;
let online = true;
let probeTimer = null;
const listeners = new Set();

/** True while the server is believed reachable. */
export function isOnline() {
  return online;
}

/**
 * Subscribe to connection state changes. Called with `true` (recovered) or
 * `false` (lost). Returns an unsubscribe function.
 */
export function onConnectionChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

function emit() {
  for (const fn of listeners) {
    try {
      fn(online);
    } catch (e) {
      console.error('[connection] listener failed', e);
    }
  }
}

/**
 * Report a completed request. Any response at all — including 4xx/5xx — proves
 * the server is reachable, so only transport-level errors count against us.
 */
export function reportSuccess() {
  consecutiveFailures = 0;
  if (!online) setOnline(true);
}

export function reportFailure() {
  consecutiveFailures += 1;
  if (online && consecutiveFailures >= FAILURE_THRESHOLD) {
    setOnline(false);
  }
}

function setOnline(next) {
  if (online === next) return;
  online = next;
  if (next) {
    consecutiveFailures = 0;
    stopProbing();
  } else {
    startProbing();
  }
  emit();
}

/**
 * Active recovery probe. `/health` is unauthenticated and cheap, and is hit
 * with plain `fetch` rather than `apiFetch` so a probe never triggers the
 * 401 → login redirect and never feeds its own result back into the counters.
 */
async function probe() {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const res = await fetch('/health', {
      method: 'GET',
      cache: 'no-store',
      signal: controller.signal
    });
    // Any HTTP answer means the box is serving again. A degraded database is
    // the server's problem to report, not a lost connection.
    if (res) setOnline(true);
  } catch (e) {
    // Still down; the interval will try again.
  } finally {
    clearTimeout(timeoutId);
  }
}

function startProbing() {
  if (probeTimer) return;
  probeTimer = setInterval(probe, PROBE_INTERVAL_MS);
}

function stopProbing() {
  if (probeTimer) {
    clearInterval(probeTimer);
    probeTimer = null;
  }
}

/**
 * Wire up the browser-level network events. Safe to call more
 * than once; only the first call takes effect.
 */
let started = false;
export function initConnectionMonitor() {
  if (started || typeof window === 'undefined') return;
  started = true;

  // A browser 'offline' event is unambiguous — no request can succeed — so it
  // skips the failure threshold entirely.
  window.addEventListener('offline', () => setOnline(false));

  // 'online' only means the NIC is back; the server may still be down, so
  // confirm with a probe rather than flipping back to online on faith.
  window.addEventListener('online', () => { probe(); });

  if (navigator.onLine === false) setOnline(false);

  // Coming back to a backgrounded tab is when stale state is most likely.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && !online) probe();
  });
}

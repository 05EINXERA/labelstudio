/**
 * Connection monitor — surfaces a visible warning when the workspace loses its
 * link to the server.
 *
 * Why this exists: annotation work is autosaved through `apiFetch`, and every
 * caller in the workspace swallows network failures (console.warn / catch {}) so
 * that a blip never interrupts drawing. That is the right behaviour for the
 * canvas, but it left the annotator with no signal at all — they keep drawing
 * for ten minutes against a dead server and only find out when the tab closes.
 * The per-task localStorage draft (workspace.js) is what actually protects the
 * work; this module is what tells them the draft is currently all there is.
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
 * A single failure is not enough to alarm: one aborted request during a large
 * save is normal. The banner appears after FAILURE_THRESHOLD consecutive
 * failures, or immediately on an `offline` event. While down, an unauthenticated
 * `/health` probe runs on an interval so recovery is detected even if the user
 * has stopped interacting.
 *
 * Presentation is a single floating badge in the bottom-right corner. It is
 * deliberately NOT a top bar and NOT on a timer:
 *
 *  - Nothing is inserted into the document flow. An earlier version pushed the
 *    page down with a body padding-top, which reflowed and resized the canvas
 *    mid-annotation — unacceptable while someone is drawing. The badge is
 *    `position: fixed` and floats over the corner, so the canvas never moves.
 *  - It persists for as long as the connection is actually down. A warning that
 *    times out leaves a disconnected workspace looking identical to a healthy
 *    one, and the annotator keeps drawing against a dead server unaware.
 *
 * Clicking it toggles a one-line explanation that grows the badge upward,
 * still without touching layout. It clears the instant the connection is back.
 */

const FAILURE_THRESHOLD = 2;
const PROBE_INTERVAL_MS = 5000;
const PROBE_TIMEOUT_MS = 4000;

let consecutiveFailures = 0;
let online = true;
let probeTimer = null;
let bannerEl = null;
let offlineSince = null;
let elapsedTimer = null;
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
    offlineSince = null;
    stopProbing();
  } else {
    offlineSince = Date.now();
    startProbing();
  }
  renderBanner();
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

function formatElapsed(ms) {
  const total = Math.floor(ms / 1000);
  const mins = Math.floor(total / 60);
  const secs = total % 60;
  if (mins === 0) return `${secs}s`;
  return `${mins}m ${String(secs).padStart(2, '0')}s`;
}

function ensureBanner() {
  if (bannerEl) return bannerEl;
  bannerEl = document.createElement('div');
  bannerEl.className = 'connection-banner';
  bannerEl.setAttribute('role', 'alert');
  bannerEl.setAttribute('aria-live', 'assertive');
  bannerEl.hidden = true;
  bannerEl.title =
    'Connection lost — your work is kept in this browser only and is not saved ' +
    'to the server. Keep this tab open; it will save automatically when the ' +
    'connection returns.';
  bannerEl.innerHTML = `
    <span class="connection-banner-dot" aria-hidden="true"></span>
    <span class="connection-banner-badge-label">Offline</span>
    <span class="connection-banner-elapsed" id="connectionBannerElapsed"></span>
    <span class="connection-banner-detail">
      Work saved in this browser only — it will sync when the connection returns.
    </span>
  `;
  // Click reveals the full explanation inline, without moving anything around
  // it: the detail line is inside the same floating badge, so expanding it
  // grows the badge upward over the canvas rather than reflowing the page.
  bannerEl.addEventListener('click', () => {
    bannerEl.classList.toggle('show-detail');
  });
  document.body.appendChild(bannerEl);
  return bannerEl;
}

function renderBanner() {
  if (typeof document === 'undefined' || !document.body) return;
  const el = ensureBanner();

  if (online) {
    el.hidden = true;
    el.classList.remove('show-detail');
    if (elapsedTimer) {
      clearInterval(elapsedTimer);
      elapsedTimer = null;
    }
    return;
  }

  el.hidden = false;

  const elapsedEl = el.querySelector('#connectionBannerElapsed');
  const tick = () => {
    if (elapsedEl && offlineSince) {
      elapsedEl.textContent = `offline for ${formatElapsed(Date.now() - offlineSince)}`;
    }
  };
  tick();
  if (!elapsedTimer) elapsedTimer = setInterval(tick, 1000);
}

/**
 * Wire up the banner and the browser-level network events. Safe to call more
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
  // confirm with a probe rather than clearing the banner on faith.
  window.addEventListener('online', () => { probe(); });

  if (navigator.onLine === false) setOnline(false);

  // Coming back to a backgrounded tab is the moment a stale banner is most
  // likely, and the moment the annotator most needs the truth.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && !online) probe();
  });
}

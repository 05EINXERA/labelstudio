const CSRF_COOKIE = 'csrf_token';
const CSRF_HEADER = 'X-CSRF-Token';
const SAFE_METHODS = ['GET', 'HEAD', 'OPTIONS'];

/** Read a cookie set by the server. The CSRF cookie is deliberately not
 *  httpOnly precisely so this can read it and echo it back in a header —
 *  that round trip is what a cross-origin page cannot perform. */
function readCookie(name) {
  const match = document.cookie.match(new RegExp('(?:^|;\\s*)' + name + '=([^;]*)'));
  return match ? decodeURIComponent(match[1]) : null;
}

/**
 * Append the CSRF token to a URL as a query parameter.
 *
 * Strictly for `navigator.sendBeacon`, which cannot set request headers — so
 * the header used everywhere else is simply unavailable on that path, and
 * every beacon was rejected 403. Beacons carry the `pagehide` /
 * `visibilitychange` flush of unsaved annotations and session time, and
 * sendBeacon reports only that the request was queued, never that it was
 * accepted, so those rejections were invisible to the client.
 *
 * The server accepts the token here as a fallback (api/auth.py require_csrf)
 * and still requires it to match the cookie, so the double-submit guarantee is
 * unchanged. Everything that can send a header must keep using apiFetch.
 */
export function withCsrfParam(url) {
  const csrf = readCookie(CSRF_COOKIE);
  if (!csrf) return url;
  return url + (url.includes('?') ? '&' : '?') + 'csrf_token=' + encodeURIComponent(csrf);
}

/**
 * How long to wait for the server before treating a request as failed.
 *
 * `fetch()` has NO default timeout. A request that is never answered — the
 * server accepted the connection but the reply never comes (process wedged
 * mid-restart, a dropped LAN link that never RSTs, a stalled proxy) — leaves
 * its promise pending forever: it neither resolves nor rejects, so `await`
 * never returns and no `catch` can fire.
 *
 * That is not theoretical. It is what produced the stuck "Saving Task"
 * overlay: manualSaveWithUI awaited a promise that never settled, so the
 * overlay was never removed and the annotator sat looking at a save that
 * appeared to be in progress indefinitely. Their canvas kept showing local
 * state the server had never received, which on reload looked exactly like
 * annotations had been wiped — the count dropped back to whatever the last
 * *completed* save had stored.
 *
 * 45s is deliberately generous: a large annotation blob over a busy LAN with
 * ~25 clients can legitimately take several seconds, and a false timeout would
 * queue a write that actually succeeded. It only has to be shorter than "the
 * user gives up and reloads", which is the behaviour that made this look like
 * data loss.
 */
const REQUEST_TIMEOUT_MS = 45_000;

/**
 * Below this many bytes a request body is sent as-is.
 *
 * Gzip framing costs ~20 bytes plus a little latency, so compressing the small
 * time-only saves and lock pings that dominate by count would be a loss. The
 * payloads this exists for — annotation sets — are two to three orders of
 * magnitude above it.
 */
const COMPRESS_MIN_BYTES = 1024;

/**
 * Gzip a request body, or return it untouched.
 *
 * Why this exists: every save uploads the task's complete annotation set, and a
 * large task is ~1.8 MB of JSON that gzips to about 9 KB — the same keys repeat
 * once per object, so it is close to ideal input for a compressor. Sending it
 * raw is what made saves take 25-30 s and let weak links drop them mid-upload
 * (.devnotes/network-lag/01_AUDIT.md C1). Responses were already compressed;
 * this is the other direction.
 *
 * Every failure path returns the original body. Compression is an optimisation,
 * and a browser without `CompressionStream`, or a stream that errors, must cost
 * the annotator a slower save and never a lost one. The server accepts
 * plaintext permanently for exactly this reason — it is not a migration step.
 *
 * NOTE: deliberately never used for `navigator.sendBeacon`. A beacon dispatches
 * synchronously and cannot await a stream; the unload flush is the last chance
 * to persist unsaved work, so a beacon that might not be sent at all is far
 * worse than a large one that is. See components/timer.js.
 */
async function maybeCompress(body) {
  if (typeof body !== 'string') return null;
  if (typeof CompressionStream === 'undefined') return null;

  const bytes = new TextEncoder().encode(body);
  if (bytes.length < COMPRESS_MIN_BYTES) return null;

  try {
    const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream('gzip'));
    return new Uint8Array(await new Response(stream).arrayBuffer());
  } catch (e) {
    // Fall back to the uncompressed body rather than failing the save.
    console.warn('[api] request compression failed; sending uncompressed', e);
    return null;
  }
}

export async function apiFetch(url, options = {}) {
  const logged_in = localStorage.getItem('logged_in');
  if (!logged_in) {
    window.location.href = '/';
    return;
  }

  options.headers = { ...options.headers };

  // State-changing requests must carry the CSRF token; the backend rejects
  // them with 403 otherwise.
  const method = (options.method || 'GET').toUpperCase();
  if (!SAFE_METHODS.includes(method)) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) options.headers[CSRF_HEADER] = csrf;
  }

  // Bound every request so a hung connection surfaces as a rejection the
  // caller can handle, instead of a promise that never settles. A caller that
  // passes its own `signal` keeps it — the queue's own cancellation must win.
  let timer = null;
  if (!options.signal && typeof AbortController !== 'undefined') {
    const controller = new AbortController();
    options.signal = controller.signal;
    timer = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  }

  // Compress large write bodies. Applied here rather than at each call site so
  // every authenticated write inherits it — including the offline queue's
  // replay, which is the path that runs when the network is already bad and so
  // needs it most.
  if (!SAFE_METHODS.includes(method) && typeof options.body === 'string') {
    const compressed = await maybeCompress(options.body);
    if (compressed) {
      options.body = compressed;
      options.headers['Content-Encoding'] = 'gzip';
    }
  }

  let res;
  try {
    res = await fetch(url, options);
  } finally {
    if (timer !== null) clearTimeout(timer);
  }

  if (res.status === 401) {
    localStorage.removeItem('logged_in');
    localStorage.removeItem('dataset_username');
    window.location.href = '/';
    return res;
  }

  // A missing or stale CSRF cookie means the session is no longer usable for
  // writes. Sending the user back to log in re-issues both cookies, which is
  // the only way to recover; failing silently would look like lost work.
  if (res.status === 403) {
    let detail = '';
    try {
      detail = (await res.clone().json()).detail || '';
    } catch (err) {
      // A 403 without a JSON body is not a CSRF rejection; fall through and
      // let the caller handle it.
    }
    if (detail.toLowerCase().includes('csrf')) {
      localStorage.removeItem('logged_in');
      window.location.href = '/';
    }
  }

  return res;
}

export async function pollJob(jobId, controller) {
  while (true) {
    if (controller && controller.signal.aborted) throw new Error("Aborted");
    const res = await apiFetch(`${window.location.origin}/api/detect/status/${jobId}`);
    if (res.status === 404) throw new Error("Job not found or expired");
    if (!res.ok) throw new Error(`Polling failed (${res.status})`);

    const data = await res.json();
    if (data.status === "completed") return data.result;
    if (data.status === "failed") throw new Error(data.error);

    await new Promise(r => setTimeout(r, 1000));
  }
}

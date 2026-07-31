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

export async function apiFetch(url, options = {}) {
  const logged_in = localStorage.getItem('logged_in');
  if (!logged_in) {
    window.location.href = '/';
    return;
  }

  options.headers = { ...options.headers };
  const datasetUsername = localStorage.getItem('dataset_username');
  if (datasetUsername) {
    options.headers['X-Annotator-Name'] = datasetUsername;
  }

  // State-changing requests must carry the CSRF token; the backend rejects
  // them with 403 otherwise.
  const method = (options.method || 'GET').toUpperCase();
  if (!SAFE_METHODS.includes(method)) {
    const csrf = readCookie(CSRF_COOKIE);
    if (csrf) options.headers[CSRF_HEADER] = csrf;
  }

  const res = await fetch(url, options);

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

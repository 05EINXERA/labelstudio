/**
 * import-job.js — upload an annotation import, then poll its job to the end.
 *
 * Why this exists: the import page sent preview and apply as one request each
 * through apiFetch, which aborts every request at 45 s. That budget covered the
 * upload AND the server-side job, so a large zip failed in the browser — while
 * the job, which nothing cancels, went on to commit. A retry in merge mode then
 * duplicated every imported shape (.devnotes/fix-import-timeout/01_PLAN.md).
 *
 * Now:
 *  - the upload carries its own AbortController, which switches off apiFetch's
 *    45 s timer; its deadline scales with the file size instead (60 s plus a
 *    2 Mbit/s floor), and the page's own signal still cancels it on unmount;
 *  - the server answers 202 {job_id} as soon as the file is saved, and the job
 *    is polled with the exports' scheduler (one request in flight, 1→5 s
 *    backoff, paused while hidden);
 *  - a 200 with a full body is accepted as the result, so a server still on
 *    the synchronous code (mid-rollout) keeps working.
 *
 * No DOM. Every collaborator is injected so tests/js/import_job_spec.mjs can
 * drive it in node.
 */
import { createPoller } from "./export-poll.js?v=1";

/** Upload deadline: 60 s plus the file at a 2 Mbit/s floor (250,000 B/s). */
export function uploadTimeoutMs(bytes) {
  return 60_000 + Math.ceil((Math.max(0, bytes || 0) / 250_000) * 1000);
}

/** sessionStorage key holding an in-flight apply job for a project. */
export function pendingJobKey(projectId) {
  return `importJob:${projectId}`;
}

/** Human progress text for a pending job status. */
export function describePending(status) {
  if (!status || status.state === "running") return "Processing on the server…";
  const ahead = Number(status.position) || 0;
  return ahead > 0
    ? `Waiting for the server — ${ahead} other import${ahead === 1 ? "" : "s"}/export${ahead === 1 ? "" : "s"} ahead…`
    : "Waiting for the server…";
}

function withAsyncFlag(url) {
  return url + (url.includes("?") ? "&" : "?") + "async=1";
}

async function errorDetail(res, fallback) {
  const body = await res.json().catch(() => null);
  return (body && body.detail) || fallback;
}

/**
 * Poll one import job until it ends.
 * Resolves { ok:true, result } | { ok:false, error, status } |
 *          { ok:false, lost:true } (404: expired, or the server restarted) |
 *          { ok:false, aborted:true }.
 */
export function pollImportJob(jobId, {
  apiFetch, signal = null, onProgress = () => {}, onPoller = () => {},
  isHidden = () => false, setTimer = setTimeout, clearTimer = clearTimeout,
} = {}) {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (outcome) => {
      if (settled) return;
      settled = true;
      poller.stop();
      if (signal) signal.removeEventListener("abort", onAbort);
      resolve(outcome);
    };
    const onAbort = () => finish({ ok: false, aborted: true });

    const poller = createPoller({
      isHidden, setTimer, clearTimer,
      poll: async () => {
        const res = await apiFetch(`/api/imports/jobs/${encodeURIComponent(jobId)}`);
        if (!res) { finish({ ok: false, aborted: true }); return false; }   // redirected to login
        if (res.status === 404) { finish({ ok: false, lost: true }); return false; }
        if (!res.ok) return true;                      // a transient 5xx: keep polling
        const body = await res.json();
        if (body.status === "completed") { finish({ ok: true, result: body.result }); return false; }
        if (body.status === "failed") {
          finish({ ok: false, error: body.error || "Import failed.", status: body.error_status || 500 });
          return false;
        }
        onProgress({ phase: body.state || "running", position: body.position ?? null });
        return true;
      },
    });

    if (signal) {
      if (signal.aborted) { resolve({ ok: false, aborted: true }); return; }
      signal.addEventListener("abort", onAbort);
    }
    // Handed to the page so its visibilitychange handler can resume() it.
    onPoller(poller);
    poller.start();
  });
}

/**
 * Upload `file` to `url` (an import endpoint) and follow the job to its end.
 * `onSubmitted(jobId)` fires once the server has accepted the upload, so the
 * caller can remember the job across a reload. Resolves like pollImportJob,
 * plus { ok:false, error, timedOut:true } when the upload itself ran out of time.
 */
export async function runImportJob({
  apiFetch, url, file, signal = null,
  onProgress = () => {}, onSubmitted = () => {}, onPoller = () => {},
  isHidden, setTimer = setTimeout, clearTimer = clearTimeout,
}) {
  const controller = new AbortController();
  const forward = () => controller.abort();
  if (signal) {
    if (signal.aborted) return { ok: false, aborted: true };
    signal.addEventListener("abort", forward);
  }
  let timedOut = false;
  const timer = setTimer(() => { timedOut = true; controller.abort(); }, uploadTimeoutMs(file && file.size));

  const formData = new FormData();
  formData.append("file", file);
  onProgress({ phase: "uploading" });

  let res;
  try {
    res = await apiFetch(withAsyncFlag(url), { method: "POST", body: formData, signal: controller.signal });
  } catch (err) {
    if (timedOut) {
      return { ok: false, timedOut: true,
               error: "The upload took too long and was stopped. Check the network, or split the file." };
    }
    if (signal && signal.aborted) return { ok: false, aborted: true };
    return { ok: false, error: "Could not reach the server to upload the file." };
  } finally {
    clearTimer(timer);
    if (signal) signal.removeEventListener("abort", forward);
  }

  if (!res) return { ok: false, aborted: true };                 // redirected to login
  if (res.status === 202) {
    const { job_id: jobId } = await res.json();
    onSubmitted(jobId);
    return pollImportJob(jobId, { apiFetch, signal, onProgress, onPoller, isHidden, setTimer, clearTimer });
  }
  if (res.ok) return { ok: true, result: await res.json() };     // server on the synchronous path
  return { ok: false, status: res.status, error: await errorDetail(res, `Import failed (${res.status}).`) };
}

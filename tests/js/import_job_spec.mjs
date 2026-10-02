/**
 * Spec for the annotation-import job client (frontend/js/pages/project/import-job.js).
 *
 * Run: node tests/js/import_job_spec.mjs (or via tests/test_import_job_js.py)
 *
 * The bug: preview and apply were one request each under apiFetch's 45 s
 * abort, which covered the upload too. Large imports failed in the browser
 * while the server job still committed, and a merge-mode retry duplicated
 * every shape (.devnotes/fix-import-timeout/01_PLAN.md). The load-bearing
 * properties pinned here:
 *   - the upload passes its own signal (which is what switches off apiFetch's
 *     45 s timer) and asks for ?async=1;
 *   - a 202 is followed to the job's real outcome, whatever it is;
 *   - a lost job (404) is reported as lost, never as a plain failure, because
 *     an apply may have committed;
 *   - leaving the page stops everything; nothing polls forever.
 */
const { runImportJob, pollImportJob, uploadTimeoutMs, describePending, pendingJobKey } =
  await import(new URL('../../frontend/js/pages/project/import-job.js?v=1', import.meta.url));

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};
const tick = () => new Promise((r) => setTimeout(r, 0));

/** Timers the test fires by hand. */
function fakeTimers() {
  const pending = new Map();
  let n = 0;
  return {
    setTimer: (fn, ms) => { n += 1; pending.set(n, { fn, ms }); return n; },
    clearTimer: (id) => { pending.delete(id); },
    /** Fire every pending timer whose delay is below `maxMs` (poll timers by default). */
    async fire(maxMs = 10_000) {
      for (const [id, t] of [...pending]) {
        if (t.ms >= maxMs) continue;
        pending.delete(id);
        t.fn();
      }
      await tick(); await tick();
    },
    async fireAll() { return this.fire(Infinity); },
    get count() { return pending.size; },
    get delays() { return [...pending.values()].map((t) => t.ms); },
  };
}

const json = (status, body) => ({ status, ok: status >= 200 && status < 300, json: async () => body });

/** apiFetch fake: answers by URL from a script of responses. */
function scripted(responses) {
  const calls = [];
  const fn = async (url, init = {}) => {
    calls.push({ url, init });
    const key = url.includes('/api/imports/jobs/') ? 'poll' : 'post';
    const next = responses[key].shift();
    if (typeof next === 'function') return next(init);
    return next;
  };
  fn.calls = calls;
  return fn;
}

const file = { size: 5_000_000, name: 'big.zip' };
globalThis.FormData = class { append(k, v) { this[k] = v; } };

// --- pure helpers -------------------------------------------------------------
ok('timeout floor is 60 s', uploadTimeoutMs(0) === 60_000);
ok('300 MB gets 60 s + 1,258 s at 2 Mbit/s', uploadTimeoutMs(300 * 1024 * 1024) === 60_000 + 1_258_292);
ok('queued text names how many are ahead', /2 other imports\/exports ahead/.test(describePending({ state: 'queued', position: 2 })));
ok('queued first in line', describePending({ state: 'queued', position: 0 }) === 'Waiting for the server…');
ok('running text', describePending({ state: 'running' }) === 'Processing on the server…');
ok('pending key is per project', pendingJobKey(7) === 'importJob:7');

// --- 202 → followed to completion ----------------------------------------------
{
  const t = fakeTimers();
  const progress = [];
  let submitted = null;
  const apiFetch = scripted({
    post: [json(202, { job_id: 'j1', deduplicated: false })],
    poll: [
      json(200, { status: 'pending', state: 'queued', position: 2 }),
      json(200, { status: 'pending', state: 'running' }),
      json(200, { status: 'completed', result: { tasks_updated: 3, annotations_imported: 40 } }),
    ],
  });
  const p = runImportJob({
    apiFetch, url: '/api/imports/annotations?projectId=7&mode=merge', file,
    onProgress: (s) => progress.push(s.phase), onSubmitted: (id) => { submitted = id; },
    setTimer: t.setTimer, clearTimer: t.clearTimer,
  });
  await tick(); await tick();
  await t.fire(); await t.fire();
  const out = await p;
  const post = apiFetch.calls[0];
  ok('upload asks for ?async=1', post.url === '/api/imports/annotations?projectId=7&mode=merge&async=1');
  ok('upload passes its own signal (disables the 45 s cap)', post.init.signal instanceof AbortSignal);
  ok('upload is a POST with the file', post.init.method === 'POST' && post.init.body.file === file);
  ok('job id handed back for persistence', submitted === 'j1');
  ok('polls the job endpoint', apiFetch.calls[1].url === '/api/imports/jobs/j1');
  ok('progress: uploading → queued → running', JSON.stringify(progress) === '["uploading","queued","running"]');
  ok('result is the completed body', out.ok && out.result.annotations_imported === 40);
  ok('no timers left behind', t.count === 0);
}

// --- a server still on the synchronous code -------------------------------------
{
  const t = fakeTimers();
  const apiFetch = scripted({ post: [json(200, { matched: [{ filename: 'a.png' }] })], poll: [] });
  const out = await runImportJob({ apiFetch, url: '/x?projectId=1', file, setTimer: t.setTimer, clearTimer: t.clearTimer });
  ok('200 body accepted as the result', out.ok && out.result.matched.length === 1);
  ok('no polling for a sync answer', apiFetch.calls.length === 1);
}

// --- errors -----------------------------------------------------------------------
{
  const t = fakeTimers();
  const apiFetch = scripted({ post: [json(413, { detail: 'Upload exceeds the 300 MB limit.' })], poll: [] });
  const out = await runImportJob({ apiFetch, url: '/x?a=1', file, setTimer: t.setTimer, clearTimer: t.clearTimer });
  ok('upload rejection carries the server detail', !out.ok && out.status === 413 && /300 MB/.test(out.error));
}
{
  const t = fakeTimers();
  const apiFetch = scripted({
    post: [json(202, { job_id: 'j2' })],
    poll: [json(200, { status: 'failed', error: 'No recognizable annotations.', error_status: 422 })],
  });
  const out = await runImportJob({ apiFetch, url: '/x?a=1', file, setTimer: t.setTimer, clearTimer: t.clearTimer });
  ok('a failed job reports error and status', !out.ok && out.status === 422 && /recognizable/.test(out.error));
}
{
  const t = fakeTimers();
  const apiFetch = scripted({ post: [json(202, { job_id: 'j3' })], poll: [json(404, { detail: 'gone' })] });
  const out = await runImportJob({ apiFetch, url: '/x?a=1', file, setTimer: t.setTimer, clearTimer: t.clearTimer });
  ok('a 404 while polling is "lost", not "failed"', !out.ok && out.lost === true && !out.error);
}
{
  const t = fakeTimers();
  const apiFetch = scripted({
    post: [json(202, { job_id: 'j4' })],
    poll: [json(503, {}), async () => { throw new TypeError('Failed to fetch'); },
           json(200, { status: 'completed', result: { ok: 1 } })],
  });
  const p = runImportJob({ apiFetch, url: '/x?a=1', file, setTimer: t.setTimer, clearTimer: t.clearTimer });
  await tick(); await tick(); await t.fire(); await t.fire();
  const out = await p;
  ok('a 5xx or network blip keeps polling', out.ok && out.result.ok === 1 && apiFetch.calls.length === 4);
}
{
  const t = fakeTimers();
  const apiFetch = scripted({ post: [undefined], poll: [] });
  const out = await runImportJob({ apiFetch, url: '/x?a=1', file, setTimer: t.setTimer, clearTimer: t.clearTimer });
  ok('a login redirect (apiFetch → undefined) is an abort', out.aborted === true);
}

// --- the upload's own deadline ----------------------------------------------------
{
  const t = fakeTimers();
  const apiFetch = scripted({
    post: [(init) => new Promise((_, reject) => init.signal.addEventListener('abort',
      () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))))],
    poll: [],
  });
  const p = runImportJob({ apiFetch, url: '/x?a=1', file, setTimer: t.setTimer, clearTimer: t.clearTimer });
  await tick();
  ok('upload deadline scales with size', t.delays[0] === uploadTimeoutMs(file.size));
  await t.fireAll();
  const out = await p;
  ok('a stalled upload times out with its own message', !out.ok && out.timedOut && /took too long/.test(out.error));
}

// --- leaving the page ---------------------------------------------------------------
{
  const t = fakeTimers();
  const page = new AbortController();
  const apiFetch = scripted({
    post: [(init) => new Promise((_, reject) => init.signal.addEventListener('abort',
      () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' }))))],
    poll: [],
  });
  const p = runImportJob({ apiFetch, url: '/x?a=1', file, signal: page.signal, setTimer: t.setTimer, clearTimer: t.clearTimer });
  await tick();
  page.abort();
  const out = await p;
  ok('unmount during upload aborts it', out.aborted === true && !out.timedOut);
}
{
  const t = fakeTimers();
  const page = new AbortController();
  const apiFetch = scripted({
    post: [json(202, { job_id: 'j5' })],
    poll: [json(200, { status: 'pending', state: 'running' }), json(200, { status: 'pending', state: 'running' })],
  });
  let poller = null;
  const p = runImportJob({ apiFetch, url: '/x?a=1', file, signal: page.signal, onPoller: (x) => { poller = x; },
    setTimer: t.setTimer, clearTimer: t.clearTimer });
  await tick(); await tick();
  page.abort();
  const out = await p;
  const callsAtAbort = apiFetch.calls.length;
  await t.fire(); await t.fire();
  ok('unmount during polling resolves aborted', out.aborted === true);
  ok('no polls after unmount', apiFetch.calls.length === callsAtAbort);
  ok('the poller was handed to the page and stopped', poller && poller.active === false);
}

// --- resuming a remembered job after a reload ----------------------------------------
{
  const t = fakeTimers();
  const apiFetch = scripted({ post: [], poll: [json(200, { status: 'completed', result: { tasks_updated: 1 } })] });
  const out = await pollImportJob('j6', { apiFetch, setTimer: t.setTimer, clearTimer: t.clearTimer });
  ok('pollImportJob alone follows an existing job', out.ok && out.result.tasks_updated === 1);
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);

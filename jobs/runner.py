"""Schedules and supervises heavy jobs from inside the web process.

Why this exists: on 2026-09-24 five COCO exports ran as BackgroundTasks
threads inside the single uvicorn process. They held the GIL for 40+ minutes,
and every request in the office queued behind them (trivial endpoints went
from ~40 ms to 2-5 s). A thread cannot be stopped, so the only cure was a
restart. See .devnotes/fix-exports-imports/.

What the runner guarantees:

- **Isolation.** In `process` mode each job is its own `python -m jobs.worker`
  child at below-normal priority. The web process spends no CPU on the job,
  only a 0.2 s poll of the child's exit code.
- **A kill switch.** A job past its timeout is killed and reported failed,
  and its slot is freed.
- **Bounded concurrency.** At most `slots` jobs run at once, and jobs sharing
  a `lane` (one project's exports) run one at a time. The rest queue.
- **No duplicates.** A job submitted with a `key` identical to one still
  queued or running gets that job back instead of a second copy (five clicks
  on Export make one job).
- **No RAM retention.** Results are files under `<DATA_DIR>/jobs/`, served
  from disk and deleted after `result_ttl`, on download, or at startup.

Rule 9 still applies: this job table is in-process state, so the app stays a
single uvicorn worker. The children never touch it; they report through their
result file and exit code.
"""
import asyncio
import collections
import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from typing import Dict, Optional, Tuple

import database
from config import DATA_DIR, EXPORT_RESULT_TTL_S, HEAVY_JOB_MODE, HEAVY_JOB_SLOTS
from jobs import worker

logger = logging.getLogger(__name__)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_POLL_S = 0.2

QUEUED, RUNNING, COMPLETED, FAILED = "queued", "running", "completed", "failed"
_ACTIVE = (QUEUED, RUNNING)


class Job:
    __slots__ = (
        "id", "kind", "lane", "key", "status", "spec", "timeout", "queue_deadline",
        "created", "started", "finished", "handle", "thread_result", "result",
        "error", "error_status", "meta", "body_path", "timed_out",
    )

    def __init__(self, job_id, kind, spec, timeout, lane, key, queue_deadline):
        self.id = job_id
        self.kind = kind
        self.lane = lane
        self.key = key
        self.status = QUEUED
        self.spec = spec
        self.timeout = timeout
        self.queue_deadline = queue_deadline
        self.created = time.monotonic()
        self.started = None
        self.finished = None
        self.handle = None          # Popen or Thread
        self.thread_result = None   # set by a thread-mode job when it ends
        self.result = None
        self.error = None
        self.error_status = None
        self.meta = {}
        self.body_path = spec.get("body_path")
        self.timed_out = False


class HeavyJobRunner:
    def __init__(self, mode: str, slots: int, work_dir: str, result_ttl: float):
        if mode not in ("process", "thread", "inline"):
            raise ValueError(f"Unknown heavy-job mode {mode!r}")
        self.mode = mode
        self.slots = max(1, slots)
        self.work_dir = work_dir
        self.result_ttl = result_ttl
        self._lock = threading.RLock()
        self._jobs: Dict[str, Job] = {}
        self._queue = collections.deque()
        self._running: Dict[str, Job] = {}
        self._keys: Dict[str, str] = {}
        self._supervisor: Optional[threading.Thread] = None

    # --- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Clear what a previous process left behind. No job survives a restart."""
        os.makedirs(self.work_dir, exist_ok=True)
        for name in os.listdir(self.work_dir):
            path = os.path.join(self.work_dir, name)
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError as exc:
                logger.warning("Could not remove stale job file %s: %s", path, exc)

    def shutdown(self) -> None:
        """Kill running children; a server stopping must not leave orphans.

        Not a permanent stop: the supervisor is a daemon thread that ends with
        the process, and a runner that is shut down and used again (the test
        suite starts and stops the app per test) must still work.
        """
        with self._lock:
            for job in list(self._running.values()):
                if isinstance(job.handle, subprocess.Popen) and job.handle.poll() is None:
                    job.handle.kill()

    # --- submitting and reading ---------------------------------------------

    def submit(self, kind: str, payload: dict, *, timeout: float, lane: Optional[str] = None,
               key: Optional[str] = None, queue_wait: Optional[float] = None) -> Tuple[Job, bool]:
        """Queue a job. Returns (job, deduplicated)."""
        with self._lock:
            self._sweep()
            if key is not None:
                existing = self._jobs.get(self._keys.get(key, ""))
                if existing is not None and existing.status in _ACTIVE:
                    return existing, True

            job_id = str(uuid.uuid4())
            base = os.path.join(self.work_dir, job_id)
            spec = dict(payload, kind=kind, job_id=job_id,
                        result_path=f"{base}.result.json", body_path=f"{base}.body")
            deadline = time.monotonic() + queue_wait if queue_wait is not None else None
            job = Job(job_id, kind, spec, timeout, lane, key, deadline)
            self._jobs[job_id] = job
            if key is not None:
                self._keys[key] = job_id

            if self.mode == "inline":
                job.status, job.started = RUNNING, time.monotonic()
                self._finish(job, worker.execute(spec))
                return job, False

            self._queue.append(job)
            self._pump()
            self._ensure_supervisor()
            return job, False

    def get(self, job_id: str) -> Optional[Job]:
        with self._lock:
            return self._jobs.get(job_id)

    def queue_position(self, job: Job) -> int:
        """How many queued jobs are ahead of this one (0 = next)."""
        with self._lock:
            for i, queued in enumerate(self._queue):
                if queued is job:
                    return i
            return 0

    def take(self, job_id: str) -> Optional[Job]:
        """Remove a completed job from the table and return it (one-shot download).

        The caller serves `job.body_path` and then calls `discard_file`.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != COMPLETED:
                return None
            del self._jobs[job_id]
            return job

    @staticmethod
    def discard_file(path: Optional[str]) -> None:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError as exc:
                logger.warning("Could not remove job file %s: %s", path, exc)

    async def wait(self, job: Job) -> Job:
        """Await a job's end without holding a thread: the loop sleeps between checks."""
        while job.status in _ACTIVE:
            await asyncio.sleep(0.1)
        return job

    def health(self) -> dict:
        with self._lock:
            now = time.monotonic()
            oldest = max((now - j.started for j in self._running.values() if j.started), default=0)
            return {
                "mode": self.mode,
                "slots": self.slots,
                "running": len(self._running),
                "queued": len(self._queue),
                "oldest_running_s": int(oldest),
            }

    # --- scheduling -----------------------------------------------------------

    def _pump(self) -> None:
        """Start queued jobs while there are free slots (caller holds the lock)."""
        now = time.monotonic()
        busy_lanes = {j.lane for j in self._running.values() if j.lane}
        for job in list(self._queue):
            if job.queue_deadline is not None and now > job.queue_deadline:
                self._queue.remove(job)
                self._finish(job, {"ok": False, "status": 503,
                                   "error": "The server is busy with other exports or imports. Try again in a few minutes."})
                continue
            if len(self._running) >= self.slots:
                continue
            if job.lane and job.lane in busy_lanes:
                continue
            self._queue.remove(job)
            self._start(job)
            if job.lane:
                busy_lanes.add(job.lane)

    def _start(self, job: Job) -> None:
        job.status, job.started = RUNNING, time.monotonic()
        self._running[job.id] = job
        if self.mode == "process":
            spec_path = os.path.join(self.work_dir, f"{job.id}.spec.json")
            with open(spec_path, "w", encoding="utf-8") as fh:
                json.dump(job.spec, fh)
            env = dict(os.environ)
            # The child must use exactly the database and data directory this
            # process is serving, not whatever it would re-derive from the
            # environment and .env on its own.
            env["DATABASE_URL"] = database.engine.url.render_as_string(hide_password=False)
            env["DATA_DIR"] = DATA_DIR
            # One connection is all a job uses; don't let it open the web
            # process's whole pool against Postgres.
            env["DB_POOL_SIZE"] = "1"
            env["DB_MAX_OVERFLOW"] = "0"
            flags = subprocess.BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0
            job.handle = subprocess.Popen(
                [sys.executable, "-m", "jobs.worker", spec_path],
                cwd=_REPO_ROOT, env=env, creationflags=flags,
            )
        else:
            thread = threading.Thread(target=self._thread_body, args=(job,),
                                      name=f"heavy-job-{job.id[:8]}", daemon=True)
            job.handle = thread
            thread.start()
        logger.info("Heavy job %s started: kind=%s lane=%s mode=%s", job.id, job.kind, job.lane, self.mode)

    def _thread_body(self, job: Job) -> None:
        job.thread_result = worker.execute(job.spec)

    def _ensure_supervisor(self) -> None:
        if self._supervisor is None or not self._supervisor.is_alive():
            self._supervisor = threading.Thread(target=self._supervise, name="heavy-job-supervisor",
                                                daemon=True)
            self._supervisor.start()

    def _supervise(self) -> None:
        while True:
            try:
                with self._lock:
                    self._check_running()
                    self._pump()
                    self._sweep()
                    idle = not self._running and not self._queue
            except Exception:
                logger.exception("Heavy-job supervisor iteration failed")
                idle = False
            if idle and not self._has_retained_results():
                return   # restarted by the next submit
            time.sleep(_POLL_S)

    def _has_retained_results(self) -> bool:
        with self._lock:
            return any(j.status in (COMPLETED, FAILED) for j in self._jobs.values())

    def _check_running(self) -> None:
        now = time.monotonic()
        for job in list(self._running.values()):
            if isinstance(job.handle, subprocess.Popen):
                code = job.handle.poll()
                if code is not None:
                    self._finish(job, self._read_result(job, code))
                elif now - job.started > job.timeout:
                    job.handle.kill()
                    job.handle.wait()
                    job.timed_out = True
                    self._finish(job, self._timeout_result(job))
            else:
                if job.thread_result is not None:
                    if job.timed_out:
                        # Already reported failed; the thread has now ended, so
                        # the slot is free and its late output is dropped.
                        self._running.pop(job.id, None)
                        self.discard_file(job.body_path)
                        self.discard_file(job.spec.get("upload_path"))
                    else:
                        self._finish(job, job.thread_result)
                elif not job.timed_out and now - job.started > job.timeout:
                    # A thread cannot be killed. Report the failure now, but keep
                    # the slot until the thread really ends, or timed-out threads
                    # would pile up without bound.
                    job.timed_out = True
                    self._record(job, self._timeout_result(job))

    def _timeout_result(self, job: Job) -> dict:
        minutes = max(1, int(round(job.timeout / 60)))
        noun = "Export" if job.kind == "export" else "Import" if job.kind == "import" else "Job"
        logger.warning("Heavy job %s (%s) exceeded its %ss timeout and was stopped", job.id, job.kind, job.timeout)
        return {"ok": False, "status": 504,
                "error": f"{noun} took longer than {minutes} minute(s) and was stopped. Nothing was changed."}

    def _read_result(self, job: Job, code: int) -> dict:
        path = job.spec["result_path"]
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError) as exc:
            logger.error("Heavy job %s exited %s without a readable result: %s", job.id, code, exc)
            return {"ok": False, "status": 500, "error": "The job stopped unexpectedly."}

    # --- completion -----------------------------------------------------------

    def _record(self, job: Job, result: dict) -> None:
        """Set the job's final state (without releasing its slot)."""
        job.result = result
        job.finished = time.monotonic()
        if result.get("ok"):
            job.status = COMPLETED
            job.meta = result.get("meta") or {}
        else:
            job.status = FAILED
            job.error = result.get("error") or "Job failed."
            job.error_status = result.get("status") or 500
            self.discard_file(job.body_path)
        if job.key is not None and self._keys.get(job.key) == job.id:
            del self._keys[job.key]

    def _finish(self, job: Job, result: dict) -> None:
        self._record(job, result)
        self._running.pop(job.id, None)
        for path in (job.spec.get("result_path"), os.path.join(self.work_dir, f"{job.id}.spec.json"),
                     job.spec.get("upload_path")):
            self.discard_file(path)
        logger.info("Heavy job %s %s: kind=%s duration_ms=%s%s", job.id, job.status, job.kind,
                    result.get("duration_ms"), f" error={job.error!r}" if job.error else "")

    def _sweep(self) -> None:
        """Drop finished jobs, and their files, `result_ttl` after they end."""
        now = time.monotonic()
        for job in list(self._jobs.values()):
            if job.status in _ACTIVE or job.id in self._running or job.finished is None:
                continue
            if now - job.finished > self.result_ttl:
                del self._jobs[job.id]
                self.discard_file(job.body_path)


_runner: Optional[HeavyJobRunner] = None
_runner_lock = threading.Lock()


def get_runner() -> HeavyJobRunner:
    """The process-wide runner, built from config on first use."""
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = HeavyJobRunner(HEAVY_JOB_MODE, HEAVY_JOB_SLOTS,
                                     os.path.join(DATA_DIR, "jobs"), EXPORT_RESULT_TTL_S)
            _runner.start()
        return _runner

"""The heavy-job runner in `process` mode: isolation, kill switch, queueing.

The rest of the suite runs jobs `inline` (conftest.py), which proves the job
code but not the process machinery. These tests build their own runner in
`process` mode and drive real `python -m jobs.worker` children. See
.devnotes/fix-exports-imports/04_PLAN.md Phase 2.
"""
import json
import os
import subprocess
import sys
import time

import pytest

from jobs import runner as runner_module
from jobs.runner import COMPLETED, FAILED, QUEUED, RUNNING, HeavyJobRunner

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def proc_runner(tmp_path):
    runner = HeavyJobRunner("process", slots=2, work_dir=str(tmp_path / "jobs"), result_ttl=3600)
    runner.start()
    yield runner
    runner.shutdown()


def _wait(job, timeout=60):
    deadline = time.monotonic() + timeout
    while job.status in (QUEUED, RUNNING):
        assert time.monotonic() < deadline, f"job {job.id} still {job.status}"
        time.sleep(0.05)
    return job


def test_worker_module_does_not_import_the_web_app():
    """A spawned child re-imports its module graph. If that graph reaches
    `main`, a router or `detector`, every job pays for the app (and the ML
    weights) and could touch rule-9 state. Checked in a fresh interpreter."""
    code = ("import sys, jobs.worker; "
            "bad = [m for m in ('main', 'detector', 'api.routers.exports', 'api.routers.imports', "
            "'api.routers.tasks', 'api.routers.detect') if m in sys.modules]; print(bad)")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().splitlines()[-1] == "[]"


def test_a_job_runs_in_a_child_and_completes(proc_runner):
    job, dup = proc_runner.submit("sleep", {"seconds": 0.2}, timeout=60)
    assert dup is False
    assert isinstance(job.handle, subprocess.Popen)
    assert job.handle.pid != os.getpid()
    _wait(job)
    assert job.status == COMPLETED
    # Its scratch files are gone once it is done.
    leftovers = [n for n in os.listdir(proc_runner.work_dir) if n.startswith(job.id)]
    assert leftovers == []


def test_a_job_past_its_timeout_is_killed(proc_runner):
    job, _ = proc_runner.submit("spin", {"seconds": 60}, timeout=1)
    started = time.monotonic()
    _wait(job, timeout=30)
    assert job.status == FAILED
    assert job.error_status == 504
    assert "stopped" in job.error
    assert job.handle.poll() is not None, "the child must actually be dead"
    assert time.monotonic() - started < 15
    assert proc_runner.health()["running"] == 0, "the slot is freed"


def test_slots_bound_concurrency(tmp_path):
    runner = HeavyJobRunner("process", slots=1, work_dir=str(tmp_path / "j"), result_ttl=3600)
    runner.start()
    try:
        first, _ = runner.submit("sleep", {"seconds": 1.5}, timeout=60)
        second, _ = runner.submit("sleep", {"seconds": 0.1}, timeout=60)
        assert second.status == QUEUED
        assert runner.queue_position(second) == 0
        _wait(second)
        assert first.status == COMPLETED and second.status == COMPLETED
        assert second.started >= first.finished - 0.5
    finally:
        runner.shutdown()


def test_one_lane_runs_one_job_at_a_time(proc_runner):
    """One project's exports queue behind each other even with free slots."""
    a, _ = proc_runner.submit("sleep", {"seconds": 1.0}, timeout=60, lane="export:1")
    b, _ = proc_runner.submit("sleep", {"seconds": 0.1}, timeout=60, lane="export:1")
    c, _ = proc_runner.submit("sleep", {"seconds": 0.1}, timeout=60, lane="export:2")
    assert b.status == QUEUED
    assert c.status == RUNNING, "another project's export is not held up"
    _wait(b)
    assert b.started >= a.finished - 0.5


def test_identical_submissions_share_one_job(proc_runner):
    a, dup_a = proc_runner.submit("sleep", {"seconds": 0.5}, timeout=60, key="k1")
    b, dup_b = proc_runner.submit("sleep", {"seconds": 0.5}, timeout=60, key="k1")
    c, dup_c = proc_runner.submit("sleep", {"seconds": 0.5}, timeout=60, key="k2")
    assert (dup_a, dup_b, dup_c) == (False, True, False)
    assert b is a and c is not a
    _wait(a)
    # Once finished, the same key starts a fresh job rather than reusing it.
    d, dup_d = proc_runner.submit("sleep", {"seconds": 0.1}, timeout=60, key="k1")
    assert dup_d is False and d is not a


def test_a_job_that_waits_too_long_for_a_slot_is_refused(tmp_path):
    runner = HeavyJobRunner("process", slots=1, work_dir=str(tmp_path / "q"), result_ttl=3600)
    runner.start()
    try:
        runner.submit("sleep", {"seconds": 3}, timeout=60)
        waiting, _ = runner.submit("sleep", {"seconds": 0.1}, timeout=60, queue_wait=0.3)
        _wait(waiting, timeout=30)
        assert waiting.status == FAILED
        assert waiting.error_status == 503
    finally:
        runner.shutdown()


def test_the_api_stays_fast_while_a_job_burns_cpu(client, proc_runner):
    """The point of the whole change. A CPU-bound job in a child process must
    not slow the web process: before, the same load in a thread shared the GIL
    and trivial requests went from ~40 ms to seconds."""
    job, _ = proc_runner.submit("spin", {"seconds": 4}, timeout=60)
    time.sleep(0.5)   # the child is up and spinning
    timings = []
    while job.status == RUNNING and len(timings) < 40:
        started = time.perf_counter()
        assert client.get("/health").status_code == 200
        timings.append(time.perf_counter() - started)
    assert len(timings) >= 10
    timings.sort()
    p95 = timings[int(len(timings) * 0.95) - 1]
    assert p95 < 0.25, f"/health p95 {p95 * 1000:.0f} ms while a job ran"
    _wait(job)


def test_an_export_runs_end_to_end_in_a_child_process(client, alice, tmp_path, monkeypatch):
    """The real export path through a real child: create, poll, download."""
    runner = HeavyJobRunner("process", slots=2, work_dir=str(tmp_path / "e2e"), result_ttl=3600)
    runner.start()
    monkeypatch.setattr(runner_module, "_runner", runner)
    try:
        pid = client.post("/api/projects", json={"name": "hj-e2e", "slug": "hj-e2e", "creator": "x"},
                          headers=alice).json()["id"]
        client.post(f"/api/tasks?projectId={pid}", json={"description": "a.png", "status": "New"}, headers=alice)

        res = client.post("/api/exports", json={"projectId": pid, "format": "coco"}, headers=alice)
        assert res.status_code == 200, res.text
        job_id = res.json()["job_id"]
        again = client.post("/api/exports", json={"projectId": pid, "format": "coco"}, headers=alice).json()
        assert again["deduplicated"] is True and again["job_id"] == job_id

        deadline = time.monotonic() + 60
        while True:
            status = client.get(f"/api/exports/{job_id}", headers=alice).json()
            if status["status"] != "pending":
                break
            assert status["state"] in ("queued", "running")
            assert time.monotonic() < deadline, "export did not finish"
            time.sleep(0.1)
        assert status["status"] == "completed", status
        assert status["task_count"] == 1

        download = client.get(f"/api/exports/{job_id}/download", headers=alice)
        assert download.status_code == 200
        assert "attachment" in download.headers["content-disposition"]
        body = json.loads(download.content)
        assert [img["file_name"] for img in body["images"]] == ["a.png"]
        # One-shot, and the file is gone from disk.
        assert client.get(f"/api/exports/{job_id}/download", headers=alice).status_code == 404
        assert os.listdir(runner.work_dir) == []
    finally:
        runner.shutdown()


def test_health_reports_heavy_jobs(client):
    body = client.get("/health").json()
    assert set(body["heavy_jobs"]) >= {"mode", "running", "queued", "oldest_running_s"}


def test_an_import_runs_end_to_end_in_a_child_process(client, alice, tmp_path, monkeypatch):
    """Preview then apply, each parsed in a child; the request just awaits."""
    runner = HeavyJobRunner("process", slots=2, work_dir=str(tmp_path / "imp"), result_ttl=3600)
    runner.start()
    monkeypatch.setattr(runner_module, "_runner", runner)
    try:
        pid = client.post("/api/projects", json={"name": "hj-imp", "slug": "hj-imp", "creator": "x"},
                          headers=alice).json()["id"]
        client.post(f"/api/tasks?projectId={pid}", json={"description": "cat.png", "status": "New"}, headers=alice)
        coco = json.dumps({
            "images": [{"id": 1, "file_name": "cat.png", "width": 100, "height": 100}],
            "categories": [{"id": 1, "name": "cat"}],
            "annotations": [{"id": 1, "image_id": 1, "category_id": 1, "bbox": [10, 10, 20, 30],
                             "segmentation": []}],
        }).encode()
        files = {"file": ("coco.json", coco, "application/json")}

        preview = client.post(f"/api/imports/annotations/preview?projectId={pid}", files=files, headers=alice)
        assert preview.status_code == 200, preview.text
        assert preview.json()["new_labels"] == ["cat"]

        applied = client.post(f"/api/imports/annotations?projectId={pid}&mode=merge", files=files, headers=alice)
        assert applied.status_code == 200, applied.text
        assert applied.json()["annotations_imported"] == 1

        bad = client.post(f"/api/imports/annotations/preview?projectId={pid}",
                          files={"file": ("x.json", b"not json", "application/json")}, headers=alice)
        assert bad.status_code == 422
        assert "Invalid JSON" in bad.json()["detail"]
        # Uploads and job scratch files are cleaned up.
        assert os.listdir(runner.work_dir) == []
    finally:
        runner.shutdown()

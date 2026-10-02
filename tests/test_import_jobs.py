"""Annotation import as submit-then-poll (`?async=1` + `GET /api/imports/jobs/{id}`).

The import page used to send preview and apply as one request each through
`apiFetch`, which aborts at 45 s. That budget covered the upload and the job,
so a large zip failed in the browser while the job, which nothing cancels,
went on to commit; a retry in merge mode then duplicated every imported shape.
See .devnotes/fix-import-timeout/01_PLAN.md.

These tests pin the replacement: the POST answers as soon as the upload is
saved, the status endpoint reports the job's progress and outcome with the
same body as the synchronous path, it is MANAGER-gated like the endpoints that
create jobs, and an identical submission while one is active joins it instead
of importing twice.
"""
import json
import os
import threading

import pytest

import models
from database import SessionLocal
from jobs import runner as runner_module
from jobs import worker as worker_module
from jobs.runner import HeavyJobRunner

COCO_PAYLOAD = json.dumps({
    "images": [{"id": 1, "file_name": "cat.png", "width": 100, "height": 100}],
    "categories": [{"id": 1, "name": "cat"}],
    "annotations": [{"id": 1, "image_id": 1, "category_id": 1,
                     "bbox": [10, 10, 20, 30], "segmentation": []}],
}).encode()


def _project_with_task(client, auth, name, description="cat.png"):
    pid = client.post("/api/projects", json={"name": name, "slug": name, "creator": "x"},
                      headers=auth).json()["id"]
    tid = client.post("/api/tasks", json={"description": description},
                      params={"projectId": pid}, headers=auth).json()["id"]
    return pid, tid


def _annotation_count(task_id):
    db = SessionLocal()
    try:
        return db.query(models.Annotation).filter(models.Annotation.task_id == task_id).count()
    finally:
        db.close()


def _submit(client, auth, pid, *, mode=None, payload=COCO_PAYLOAD, preview=False):
    path = "/api/imports/annotations/preview" if preview else "/api/imports/annotations"
    params = {"projectId": pid, "async": "1"}
    if mode:
        params["mode"] = mode
    return client.post(path, params=params, headers=auth,
                       files={"file": ("coco.json", payload, "application/json")})


def _status(client, auth, job_id):
    return client.get(f"/api/imports/jobs/{job_id}", headers=auth)


# --- the async contract --------------------------------------------------------

def test_async_preview_answers_202_and_reports_the_same_body(client, alice):
    pid, _ = _project_with_task(client, alice, "ij-prev")
    sync_body = client.post(
        "/api/imports/annotations/preview", params={"projectId": pid}, headers=alice,
        files={"file": ("coco.json", COCO_PAYLOAD, "application/json")},
    ).json()

    res = _submit(client, alice, pid, preview=True)
    assert res.status_code == 202, res.text
    job_id = res.json()["job_id"]
    assert res.json()["deduplicated"] is False

    status = _status(client, alice, job_id).json()
    assert status["status"] == "completed"
    assert status["mode"] == "preview"
    assert status["result"] == sync_body          # identical to the synchronous path


def test_async_apply_imports_once_and_reports_counts(client, alice):
    pid, tid = _project_with_task(client, alice, "ij-apply")
    res = _submit(client, alice, pid, mode="merge")
    assert res.status_code == 202, res.text

    status = _status(client, alice, res.json()["job_id"]).json()
    assert status["status"] == "completed" and status["mode"] == "merge"
    assert status["result"]["tasks_updated"] == 1
    assert status["result"]["annotations_imported"] == 1
    assert _annotation_count(tid) == 1


def test_a_failed_job_reports_the_error_and_its_http_status(client, alice):
    pid, tid = _project_with_task(client, alice, "ij-fail")
    res = _submit(client, alice, pid, mode="merge", payload=b"not json at all")
    assert res.status_code == 202, "the upload itself is accepted; the job fails"

    status = _status(client, alice, res.json()["job_id"]).json()
    assert status["status"] == "failed"
    assert status["error"]
    assert status["error_status"] in (400, 422)
    assert _annotation_count(tid) == 0


def test_the_synchronous_path_is_unchanged(client, alice):
    """Without ?async=1 the request still returns the result body: the test
    suite and any tab still holding the old bundle depend on it."""
    pid, tid = _project_with_task(client, alice, "ij-sync")
    res = client.post("/api/imports/annotations", params={"projectId": pid}, headers=alice,
                      files={"file": ("coco.json", COCO_PAYLOAD, "application/json")})
    assert res.status_code == 200, res.text
    assert res.json()["annotations_imported"] == 1
    assert _annotation_count(tid) == 1


# --- access ---------------------------------------------------------------------

def test_status_of_an_unknown_job_is_404(client, alice):
    assert _status(client, alice, "no-such-job").status_code == 404


def test_status_needs_a_role_on_the_jobs_project(client, alice, bob):
    pid, _ = _project_with_task(client, alice, "ij-acl")
    job_id = _submit(client, alice, pid, preview=True).json()["job_id"]
    # bob has no role on alice's project: indistinguishable from an unknown id.
    assert _status(client, bob, job_id).status_code == 404
    client.cookies.clear()
    assert client.get(f"/api/imports/jobs/{job_id}").status_code == 401


def test_async_submit_keeps_the_manager_check(client, alice, bob):
    pid, _ = _project_with_task(client, alice, "ij-acl2")
    assert _submit(client, bob, pid, mode="merge").status_code == 404


def test_an_export_job_id_is_not_readable_as_an_import(client, alice, tmp_path, monkeypatch):
    runner = HeavyJobRunner("inline", slots=1, work_dir=str(tmp_path / "jobs"), result_ttl=3600)
    runner.start()
    monkeypatch.setattr(runner_module, "_runner", runner)
    job, _ = runner.submit("sleep", {"seconds": 0}, timeout=10)
    assert _status(client, alice, job.id).status_code == 404


# --- dedupe: the duplicate-import guard ----------------------------------------------

@pytest.fixture
def gated_runner(tmp_path, monkeypatch):
    """A real runner whose import jobs wait on a gate, so a job is provably
    still running when the duplicate arrives. Thread mode: same process, real
    worker code, real database."""
    gate = threading.Event()
    real_execute = worker_module.execute

    def gated_execute(spec):
        if spec.get("kind") == "import" and spec.get("mode") != "preview":
            gate.wait(timeout=30)
        return real_execute(spec)

    monkeypatch.setattr(worker_module, "execute", gated_execute)
    runner = HeavyJobRunner("thread", slots=2, work_dir=str(tmp_path / "gated"), result_ttl=3600)
    runner.start()
    monkeypatch.setattr(runner_module, "_runner", runner)
    yield runner, gate
    gate.set()


def _wait_done(client, auth, job_id, timeout=30.0):
    import time
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        body = _status(client, auth, job_id).json()
        if body["status"] != "pending":
            return body
        time.sleep(0.05)
    raise AssertionError("import job did not finish")


def test_an_identical_import_while_one_runs_joins_it(client, alice, gated_runner):
    runner, gate = gated_runner
    pid, tid = _project_with_task(client, alice, "ij-dedupe")

    first = _submit(client, alice, pid, mode="merge")
    second = _submit(client, alice, pid, mode="merge")          # the retry / double click
    assert first.status_code == second.status_code == 202
    assert second.json()["job_id"] == first.json()["job_id"]
    assert second.json()["deduplicated"] is True

    pending = _status(client, alice, first.json()["job_id"]).json()
    assert pending["status"] == "pending" and pending["state"] in ("queued", "running")
    # The joining request's own upload copy was removed; only the job's remains.
    uploads = [n for n in os.listdir(runner.work_dir) if n.endswith(".upload")]
    assert len(uploads) == 1

    gate.set()
    done = _wait_done(client, alice, first.json()["job_id"])
    assert done["status"] == "completed"
    assert _annotation_count(tid) == 1, "imported once, not twice"


def test_different_content_or_mode_is_not_deduplicated(client, alice, gated_runner):
    _runner, gate = gated_runner
    pid, _ = _project_with_task(client, alice, "ij-nodedupe")
    other = COCO_PAYLOAD.replace(b"[10, 10, 20, 30]", b"[11, 11, 20, 30]")

    a = _submit(client, alice, pid, mode="merge").json()
    b = _submit(client, alice, pid, mode="merge", payload=other).json()
    c = _submit(client, alice, pid, mode="replace").json()
    assert len({a["job_id"], b["job_id"], c["job_id"]}) == 3
    assert not (b["deduplicated"] or c["deduplicated"])
    gate.set()
    for j in (a, b, c):
        _wait_done(client, alice, j["job_id"])


def test_after_a_job_finishes_the_same_file_can_be_imported_again(client, alice, gated_runner):
    """Dedupe only collapses *concurrent* submissions; a deliberate second
    import after the first finished is the user's call."""
    _runner, gate = gated_runner
    gate.set()
    pid, tid = _project_with_task(client, alice, "ij-again")
    a = _submit(client, alice, pid, mode="merge").json()
    _wait_done(client, alice, a["job_id"])
    b = _submit(client, alice, pid, mode="merge").json()
    assert b["job_id"] != a["job_id"] and b["deduplicated"] is False
    _wait_done(client, alice, b["job_id"])
    assert _annotation_count(tid) == 2

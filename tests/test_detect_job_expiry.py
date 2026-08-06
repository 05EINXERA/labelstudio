"""Tests for the detect-job TTL sweep (T8 / finding F9).

`JOBS` is an in-process dict of inference results. A client normally deletes its
own entry the instant it polls a terminal state (GET /status/{job_id}), so this
sweep only matters for the client that never does: a closed tab, a reload, a
request that never arrives. Without a ceiling those entries — including
segmentation mask data for SAM jobs — would accumulate for the process's
lifetime.

These tests manipulate `detect.JOBS` directly rather than triggering real
inference, since detect/classify/segment call the actual ML models
(detector.py) and are exercised by the manual scripts (test_sam_mask.py,
test_upload.py) and by hand, not by this suite. What is tested here is the
sweep and the response contract, both of which are pure dict logic independent
of what produced a job.
"""
import time

import pytest

import api.routers.detect as detect_router
from tests.conftest import _register


@pytest.fixture(autouse=True)
def _clean_jobs():
    """JOBS is module-global and shared across tests in a session.

    Without this, a job left behind by one test (or by the TTL boundary tests
    below, which plant fabricated timestamps) would be visible to the next
    test's sweep and make a real bug look like flakiness.
    """
    detect_router.JOBS.clear()
    yield
    detect_router.JOBS.clear()


def _submit(client, headers):
    """POST /api/detect with a payload that will fail fast and predictably.

    A real detection call would hit the actual model; a description image
    is not the point of this test, so the point is only that a job gets
    created and the sweep runs. `_sweep_expired_jobs()` runs before the job id
    is even minted, so what happens to *this* job afterward does not matter for
    the sweep tests themselves.
    """
    return client.post("/api/detect", json={"image": "not-a-real-image"}, headers=headers)


def _plant_terminal_job(job_id: str, age_seconds: float, status: str = "completed"):
    """Insert a fabricated finished job at a given age, bypassing real inference."""
    detect_router.JOBS[job_id] = {
        "status": status,
        "result" if status == "completed" else "error": "x",
        "finished_at": time.monotonic() - age_seconds,
    }


def _plant_pending_job(job_id: str):
    detect_router.JOBS[job_id] = {"status": "pending"}


# --- the sweep --------------------------------------------------------------


def test_expired_terminal_job_is_swept_on_next_submission(client, alice):
    _plant_terminal_job("old-job", age_seconds=detect_router.JOB_TTL_SECONDS + 5)

    _submit(client, alice)

    assert "old-job" not in detect_router.JOBS


def test_fresh_terminal_job_survives_the_sweep(client, alice):
    """A job finished moments ago must not be swept just because a new one
    was submitted — only age matters, not submission events."""
    _plant_terminal_job("recent-job", age_seconds=5)

    _submit(client, alice)

    assert "recent-job" in detect_router.JOBS


def test_pending_job_is_never_swept_regardless_of_age(client, alice):
    """A pending job has no finished_at and must survive indefinitely.

    This is the correctness-critical case: a slow model call (SAM can take
    several seconds) must never have its result vanish out from under a client
    that is still legitimately polling, no matter how old the submission is or
    how many other jobs are submitted while it runs.
    """
    _plant_pending_job("slow-job")

    for _ in range(3):
        _submit(client, alice)

    assert "slow-job" in detect_router.JOBS
    assert detect_router.JOBS["slow-job"]["status"] == "pending"


def test_sweep_only_removes_the_expired_entry(client, alice):
    """A mixed dict of expired, fresh and pending jobs is swept selectively."""
    _plant_terminal_job("expired", age_seconds=detect_router.JOB_TTL_SECONDS + 1)
    _plant_terminal_job("fresh", age_seconds=1)
    _plant_pending_job("pending")

    _submit(client, alice)

    assert "expired" not in detect_router.JOBS
    assert "fresh" in detect_router.JOBS
    assert "pending" in detect_router.JOBS


def test_sweep_runs_on_classify_and_segment_endpoints_too(client, alice):
    """All three job-creating endpoints share one JOBS dict and must all sweep it."""
    _plant_terminal_job("old", age_seconds=detect_router.JOB_TTL_SECONDS + 5)

    client.post("/api/detect/classify", json={"image": "x"}, headers=alice)
    assert "old" not in detect_router.JOBS

    _plant_terminal_job("old-2", age_seconds=detect_router.JOB_TTL_SECONDS + 5)
    client.post(
        "/api/detect/segment",
        json={"image": "x", "points": [{"x": 1, "y": 1}], "labels": [1]},
        headers=alice,
    )
    assert "old-2" not in detect_router.JOBS


def test_failed_jobs_expire_the_same_as_completed_jobs(client, alice):
    _plant_terminal_job("failed-old", age_seconds=detect_router.JOB_TTL_SECONDS + 1, status="failed")

    _submit(client, alice)

    assert "failed-old" not in detect_router.JOBS


# --- the response contract ---------------------------------------------------


def test_status_endpoint_does_not_leak_the_ttl_bookkeeping_field(client, alice):
    """`finished_at` is internal to the sweep and was never part of the API.

    Guards against a future edit that forgets to strip it before returning —
    which would be a harmless but permanent change to the response shape that
    nothing in the frontend expects (frontend/js/api.js's pollJob only reads
    status/result/error).
    """
    _plant_terminal_job("visible-job", age_seconds=1)

    res = client.get("/api/detect/status/visible-job", headers=alice)
    assert res.status_code == 200
    body = res.json()
    assert "finished_at" not in body
    assert body["status"] == "completed"
    assert body["result"] == "x"


def test_polling_a_terminal_job_still_deletes_it_immediately(client, alice):
    """The normal cleanup path (a client polls once) must be unaffected by the
    TTL sweep -- it was always immediate and must stay immediate."""
    _plant_terminal_job("poll-me", age_seconds=1)

    client.get("/api/detect/status/poll-me", headers=alice)

    assert "poll-me" not in detect_router.JOBS


def test_polling_an_unknown_job_is_404(client, alice):
    res = client.get("/api/detect/status/does-not-exist", headers=alice)
    assert res.status_code == 404


def test_pending_job_reports_pending_without_deleting(client, alice):
    _plant_pending_job("still-running")

    res = client.get("/api/detect/status/still-running", headers=alice)
    assert res.status_code == 200
    assert res.json() == {"status": "pending"}
    assert "still-running" in detect_router.JOBS


def test_detect_endpoints_require_authentication(client):
    """The router-level auth dependency must still be in force."""
    res = client.post("/api/detect", json={"image": "x"})
    assert res.status_code == 401

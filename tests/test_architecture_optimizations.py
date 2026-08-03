"""Architecture optimization tests: lifespan handler, job queue TTL eviction, and lock sweeping."""
import datetime
import time

import pytest
from starlette.testclient import TestClient

from api.routers import detect, tasks
from main import app, lifespan


def test_job_queue_ttl_eviction():
    """Verify that stale inference jobs (>600s or custom age) are evicted from memory."""
    detect.JOBS.clear()

    # Insert an expired job and an active job
    now = time.time()
    detect.JOBS["job-old"] = {
        "status": "completed",
        "result": {"boxes": []},
        "created_at": now - 700.0,
    }
    detect.JOBS["job-fresh"] = {
        "status": "pending",
        "created_at": now - 10.0,
    }

    # Run cleanup
    evicted_count = detect._cleanup_stale_jobs(max_age_seconds=600.0)
    assert evicted_count == 1
    assert "job-old" not in detect.JOBS
    assert "job-fresh" in detect.JOBS


def test_job_status_retrieval_and_deletion(client, alice):
    """Verify that completed jobs return results and are deleted on status fetch."""
    detect.JOBS.clear()
    detect.JOBS["test-job-123"] = {
        "status": "completed",
        "result": {"labels": ["car"]},
        "created_at": time.time(),
    }

    res = client.get("/api/detect/status/test-job-123", headers=alice)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "completed"
    assert data["result"] == {"labels": ["car"]}
    assert "created_at" not in data  # internal metadata stripped

    # Subsequent fetch should be 404 since it was consumed
    res2 = client.get("/api/detect/status/test-job-123", headers=alice)
    assert res2.status_code == 404


def test_soft_lock_stale_sweep():
    """Verify that expired task locks are proactively swept."""
    tasks._TASK_LOCKS.clear()

    now = datetime.datetime.now(datetime.timezone.utc)
    # Stale lock (90s old, default TTL is 60s)
    tasks._TASK_LOCKS[101] = {
        "client_id": "tab-expired",
        "claimed_at": now - datetime.timedelta(seconds=90),
    }
    # Active lock (10s old)
    tasks._TASK_LOCKS[102] = {
        "client_id": "tab-active",
        "claimed_at": now - datetime.timedelta(seconds=10),
    }

    swept = tasks._sweep_stale_locks(ttl_seconds=60)
    assert swept == 1
    assert 101 not in tasks._TASK_LOCKS
    assert 102 in tasks._TASK_LOCKS


@pytest.mark.anyio
async def test_fastapi_lifespan_handler():
    """Verify that FastAPI lifespan startup and shutdown hooks execute cleanly."""
    async with lifespan(app):
        # Startup succeeded
        pass
    # Shutdown succeeded without exceptions

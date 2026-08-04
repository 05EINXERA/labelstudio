"""Architecture optimization tests: lifespan handler, job queue TTL eviction, and lock sweeping."""
import datetime
import json
import time

import pytest
from starlette.testclient import TestClient

from api.routers import detect, tasks
from database import SessionLocal
import models
from main import app, lifespan


def test_job_queue_ttl_eviction():
    """Verify that stale inference jobs (>600s or custom age) are evicted from DB."""
    db = SessionLocal()
    try:
        db.query(models.AIJob).delete()
        db.commit()

        now = datetime.datetime.now(datetime.timezone.utc)
        # Insert an expired job and an active job
        job_old = models.AIJob(
            id="job-old",
            status="completed",
            result=json.dumps({"boxes": []}),
            created_at=now - datetime.timedelta(seconds=700),
        )
        job_fresh = models.AIJob(
            id="job-fresh",
            status="pending",
            created_at=now - datetime.timedelta(seconds=10),
        )
        db.add_all([job_old, job_fresh])
        db.commit()

        # Run cleanup
        evicted_count = detect._cleanup_stale_jobs(db, max_age_seconds=600.0)
        assert evicted_count == 1
        assert db.query(models.AIJob).filter(models.AIJob.id == "job-old").first() is None
        assert db.query(models.AIJob).filter(models.AIJob.id == "job-fresh").first() is not None
    finally:
        db.close()


def test_job_status_retrieval_and_deletion(client, alice):
    """Verify that completed jobs return results and are deleted from DB on status fetch."""
    db = SessionLocal()
    try:
        db.query(models.AIJob).filter(models.AIJob.id == "test-job-123").delete()
        job = models.AIJob(
            id="test-job-123",
            status="completed",
            result=json.dumps({"labels": ["car"]}),
            created_at=datetime.datetime.now(datetime.timezone.utc),
        )
        db.add(job)
        db.commit()
    finally:
        db.close()

    res = client.get("/api/detect/status/test-job-123", headers=alice)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "completed"
    assert data["result"] == {"labels": ["car"]}
    assert "created_at" not in data  # internal metadata stripped

    # Subsequent fetch should be 404 since it was consumed and deleted from DB
    res2 = client.get("/api/detect/status/test-job-123", headers=alice)
    assert res2.status_code == 404


def test_soft_lock_stale_sweep(client, alice):
    """Verify that expired task locks are proactively swept from DB."""
    # Create project and tasks
    p_res = client.post("/api/projects", json={"name": "sweep-p", "slug": "sweep-p", "creator": "tester"}, headers=alice)
    pid = p_res.json()["id"]
    t1_res = client.post(f"/api/tasks?projectId={pid}", json={"description": "t1"}, headers=alice)
    t2_res = client.post(f"/api/tasks?projectId={pid}", json={"description": "t2"}, headers=alice)
    tid1, tid2 = t1_res.json()["id"], t2_res.json()["id"]

    db = SessionLocal()
    try:
        db.query(models.TaskLock).filter(models.TaskLock.task_id.in_([tid1, tid2])).delete()
        now = datetime.datetime.now(datetime.timezone.utc)

        # Stale lock (90s old, default TTL is 60s)
        lock1 = models.TaskLock(
            task_id=tid1,
            client_id="tab-expired",
            claimed_at=now - datetime.timedelta(seconds=90),
        )
        # Active lock (10s old)
        lock2 = models.TaskLock(
            task_id=tid2,
            client_id="tab-active",
            claimed_at=now - datetime.timedelta(seconds=10),
        )
        db.add_all([lock1, lock2])
        db.commit()

        swept = tasks._sweep_stale_locks(db, ttl_seconds=60)
        assert swept >= 1
        assert db.query(models.TaskLock).filter(models.TaskLock.task_id == tid1).first() is None
        assert db.query(models.TaskLock).filter(models.TaskLock.task_id == tid2).first() is not None
    finally:
        db.close()


@pytest.mark.anyio
async def test_fastapi_lifespan_handler():
    """Verify that FastAPI lifespan startup and shutdown hooks execute cleanly."""
    async with lifespan(app):
        # Startup succeeded
        pass
    # Shutdown succeeded without exceptions


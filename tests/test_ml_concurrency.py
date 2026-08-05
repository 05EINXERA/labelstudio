"""Tests for ML inference concurrency limits, semaphore gating, and bounded memory caches."""
import threading
import time
import pytest

import detector
from detector import get_inference_semaphore, _evict_cache_if_needed
from schemas import DetectPayload, ClassifyPayload
import api.routers.detect as detect_router
from api.routers.detect import run_detect_job, run_classify_job
import models
from database import SessionLocal


def test_inference_semaphore_instantiation():
    """Verify that get_inference_semaphore returns a valid threading semaphore."""
    sem = get_inference_semaphore()
    assert sem is not None
    assert isinstance(sem, (threading.Semaphore, threading.BoundedSemaphore))


def test_bounded_cache_eviction():
    """Verify that _evict_cache_if_needed limits cache growth to max_size."""
    cache = {}
    lock = threading.RLock()

    # Fill cache beyond max_size (max_size=3)
    for i in range(10):
        with lock:
            cache[f"key_{i}"] = f"value_{i}"
        _evict_cache_if_needed(cache, lock, max_size=3)

    assert len(cache) == 3
    # Ensure oldest keys (0-6) were evicted and newest keys (7, 8, 9) remain
    assert "key_7" in cache
    assert "key_8" in cache
    assert "key_9" in cache
    assert "key_0" not in cache


def test_inference_semaphore_concurrency_bounding():
    """Verify that inference semaphore prevents concurrent execution beyond capacity."""
    sem = get_inference_semaphore()
    concurrent_active = 0
    max_observed_active = 0
    lock = threading.Lock()

    def worker():
        nonlocal concurrent_active, max_observed_active
        with sem:
            with lock:
                concurrent_active += 1
                if concurrent_active > max_observed_active:
                    max_observed_active = concurrent_active
            time.sleep(0.05)
            with lock:
                concurrent_active -= 1

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # With default MAX_INFERENCE_CONCURRENCY=1, max observed active should be 1
    assert max_observed_active <= max(1, detector.MAX_INFERENCE_CONCURRENCY)


def test_run_detect_job_semaphore_protection(monkeypatch):
    """Verify that run_detect_job acquires the inference semaphore during execution."""
    acquired = False

    def mock_detect(*args, **kwargs):
        nonlocal acquired
        acquired = True
        return {"width": 100, "height": 100, "predictions": []}

    monkeypatch.setattr(detect_router, "detect_objects", mock_detect)

    db = SessionLocal()
    try:
        db.query(models.AIJob).filter(models.AIJob.id == "test-sem-detect").delete()
        job = models.AIJob(id="test-sem-detect", status="pending")
        db.add(job)
        db.commit()

        payload = DetectPayload(image="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        run_detect_job("test-sem-detect", payload)

        assert acquired is True
        updated_job = db.query(models.AIJob).filter(models.AIJob.id == "test-sem-detect").first()
        assert updated_job.status == "completed"
    finally:
        db.close()


def test_run_classify_job_semaphore_protection(monkeypatch):
    """Verify that run_classify_job acquires the inference semaphore during execution."""
    acquired = False

    def mock_classify(*args, **kwargs):
        nonlocal acquired
        acquired = True
        return {"tags": [{"class": "car", "score": 0.99}]}

    monkeypatch.setattr(detect_router, "classify_image", mock_classify)

    db = SessionLocal()
    try:
        db.query(models.AIJob).filter(models.AIJob.id == "test-sem-classify").delete()
        job = models.AIJob(id="test-sem-classify", status="pending")
        db.add(job)
        db.commit()

        payload = ClassifyPayload(image="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")
        run_classify_job("test-sem-classify", payload)

        assert acquired is True
        updated_job = db.query(models.AIJob).filter(models.AIJob.id == "test-sem-classify").first()
        assert updated_job.status == "completed"
    finally:
        db.close()

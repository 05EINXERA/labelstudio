import datetime
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import models
from api.auth import get_current_user, require_csrf
import config
from database import SessionLocal, commit_with_retry, get_db
from ml import (
    DetectionClientError,
    classify_image,
    detect_objects,
    embed_image,
    segment_point,
    get_inference_semaphore,
)
from schemas import ClassifyPayload, DetectPayload, EmbedPayload, SegmentPayload

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/detect",
    tags=["detect"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)

# Maximum age (in seconds) for unpolled jobs before they are automatically evicted from storage.
JOB_TTL_SECONDS = 600.0  # 10 minutes


def _cleanup_stale_jobs(db: Optional[Session] = None, max_age_seconds: float = JOB_TTL_SECONDS) -> int:
    """Evict jobs older than max_age_seconds to prevent database bloat from abandoned polls."""
    close_on_exit = False
    if db is None:
        db = SessionLocal()
        close_on_exit = True
    try:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=max_age_seconds)
        deleted = db.query(models.AIJob).filter(models.AIJob.created_at < cutoff).delete(synchronize_session=False)
        if deleted:
            commit_with_retry(db)
        return deleted
    finally:
        if close_on_exit:
            db.close()


def _create_job(db: Optional[Session] = None) -> str:
    close_on_exit = False
    if db is None:
        db = SessionLocal()
        close_on_exit = True
    try:
        _cleanup_stale_jobs(db)
        job_id = str(uuid.uuid4())
        job = models.AIJob(
            id=job_id,
            status="pending",
            created_at=datetime.datetime.now(datetime.timezone.utc),
        )
        db.add(job)
        commit_with_retry(db)
        return job_id
    finally:
        if close_on_exit:
            db.close()


def _record_job_result(job_id: str, *, status: str, result: Optional[str] = None,
                       error: Optional[str] = None) -> None:
    """Write a finished job's outcome, holding a pool connection only for the write.

    Deliberately opens its own short-lived session instead of receiving one: the
    caller runs inference first, and a session held across that wait is a
    connection the save path cannot have.
    """
    db = SessionLocal()
    try:
        job = db.query(models.AIJob).filter(models.AIJob.id == job_id).first()
        if job:
            job.status = status
            job.result = result
            job.error = error
            commit_with_retry(db)
    except SQLAlchemyError:
        # The job outcome is best-effort bookkeeping; it expires via JOB_TTL_SECONDS
        # and the client's poll falls back to a timeout. Never let it mask the
        # inference result or kill the worker thread.
        logger.exception("Could not record outcome for job %s", job_id)
    finally:
        db.close()


def _run_inference_job(job_id: str, label: str, work) -> None:
    """Run `work()` under the inference semaphore, then record the outcome.

    No database session is held while queueing for the semaphore or during
    inference itself. MAX_INFERENCE_CONCURRENCY is 1, so a job can wait for
    minutes behind others; holding a pooled connection across that wait
    exhausted the pool and made concurrent task saves fail (see
    .devnotes/deployment-hardening/08_POOL_EXHAUSTION.md).
    """
    try:
        with get_inference_semaphore():
            response = work()
        _record_job_result(job_id, status="completed", result=json.dumps(response))
    except DetectionClientError as error:
        _record_job_result(job_id, status="failed", error=str(error))
    except Exception as exc:
        logger.error("%s job %s failed: %s", label, job_id, exc, exc_info=True)
        _record_job_result(job_id, status="failed", error=f"{label} failed.")


def run_detect_job(job_id: str, payload: DetectPayload):
    _run_inference_job(job_id, "Object detection", lambda: detect_objects(
        payload.image,
        selection=payload.selection,
        prompts=payload.prompts,
        model_size=payload.model_size,
        confidence=payload.confidence,
        nms_threshold=payload.nms_threshold,
    ))


def run_classify_job(job_id: str, payload: ClassifyPayload):
    _run_inference_job(job_id, "Image classification", lambda: classify_image(
        payload.image, selection=payload.selection,
    ))


def run_segment_job(job_id: str, payload: SegmentPayload):
    _run_inference_job(job_id, "Image segmentation", lambda: segment_point(
        payload.image,
        points=[{"x": p.x, "y": p.y} for p in payload.points],
        labels=payload.labels,
        prompt=payload.prompt,
        precision=payload.precision,
        bbox=payload.bbox,
        sam_model=payload.sam_model,
    ))


def run_embed_job(job_id: str, payload: EmbedPayload):
    _run_inference_job(job_id, "Image embedding", lambda: embed_image(
        payload.image,
        sam_model=payload.sam_model,
    ))


@router.get("/status/{job_id}")
def get_job_status(job_id: str, db: Session = Depends(get_db)):
    _cleanup_stale_jobs(db)
    job = db.query(models.AIJob).filter(models.AIJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or expired")

    if job.status in ["completed", "failed"]:
        result_payload = {"status": job.status}
        if job.result:
            try:
                result_payload["result"] = json.loads(job.result)
            except Exception:
                result_payload["result"] = job.result
        if job.error:
            result_payload["error"] = job.error
        db.delete(job)
        commit_with_retry(db)
        return result_payload

    return {"status": "pending"}


def _require_ai_enabled() -> None:
    if not config.AI_FEATURES_ENABLED:
        raise HTTPException(
            status_code=503,
            detail="AI features are temporarily disabled. Manual annotation is unaffected.",
        )


@router.get("/availability")
def get_ai_availability():
    """Whether the AI job endpoints below will accept work.

    The frontend gates its whole AI toolbar on this (toolAvailability.ai), so a
    deployment with AI switched off shows the controls disabled rather than
    letting every click fail with a 503 — and the per-task preload in
    ai/detect.js can skip two requests it knows would be refused.
    """
    return {"enabled": config.AI_FEATURES_ENABLED}


@router.post("")
def detect(payload: DetectPayload, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    _require_ai_enabled()
    job_id = _create_job(db)
    background_tasks.add_task(run_detect_job, job_id, payload)
    return {"job_id": job_id}


@router.post("/classify")
def classify(payload: ClassifyPayload, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    _require_ai_enabled()
    job_id = _create_job(db)
    background_tasks.add_task(run_classify_job, job_id, payload)
    return {"job_id": job_id}


@router.post("/segment")
def segment(payload: SegmentPayload, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    _require_ai_enabled()
    job_id = _create_job(db)
    background_tasks.add_task(run_segment_job, job_id, payload)
    return {"job_id": job_id}


@router.post("/embed")
def embed(payload: EmbedPayload, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    _require_ai_enabled()
    job_id = _create_job(db)
    background_tasks.add_task(run_embed_job, job_id, payload)
    return {"job_id": job_id}



import datetime
import json
import logging
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

import models
from api.auth import get_current_user, require_csrf
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


def run_detect_job(job_id: str, payload: DetectPayload):
    db = SessionLocal()
    try:
        with get_inference_semaphore():
            response = detect_objects(
                payload.image,
                selection=payload.selection,
                prompts=payload.prompts,
                model_size=payload.model_size,
                confidence=payload.confidence,
                nms_threshold=payload.nms_threshold,
            )
        job = db.query(models.AIJob).filter(models.AIJob.id == job_id).first()
        if job:
            job.status = "completed"
            job.result = json.dumps(response)
            commit_with_retry(db)
    except DetectionClientError as error:
        job = db.query(models.AIJob).filter(models.AIJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(error)
            commit_with_retry(db)
    except Exception as e:
        logger.error("Object detection job %s failed: %s", job_id, e, exc_info=True)
        job = db.query(models.AIJob).filter(models.AIJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = "Object detection failed."
            commit_with_retry(db)
    finally:
        db.close()


def run_classify_job(job_id: str, payload: ClassifyPayload):
    db = SessionLocal()
    try:
        with get_inference_semaphore():
            response = classify_image(payload.image, selection=payload.selection)
        job = db.query(models.AIJob).filter(models.AIJob.id == job_id).first()
        if job:
            job.status = "completed"
            job.result = json.dumps(response)
            commit_with_retry(db)
    except DetectionClientError as error:
        job = db.query(models.AIJob).filter(models.AIJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(error)
            commit_with_retry(db)
    except Exception as e:
        logger.error("Image classification job %s failed: %s", job_id, e, exc_info=True)
        job = db.query(models.AIJob).filter(models.AIJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = "Image classification failed."
            commit_with_retry(db)
    finally:
        db.close()


def run_segment_job(job_id: str, payload: SegmentPayload):
    db = SessionLocal()
    try:
        with get_inference_semaphore():
            response = segment_point(
                payload.image,
                points=[{"x": p.x, "y": p.y} for p in payload.points],
                labels=payload.labels,
                prompt=payload.prompt,
                precision=payload.precision,
                bbox=payload.bbox,
                sam_model=payload.sam_model,
            )
        job = db.query(models.AIJob).filter(models.AIJob.id == job_id).first()
        if job:
            job.status = "completed"
            job.result = json.dumps(response)
            commit_with_retry(db)
    except DetectionClientError as error:
        job = db.query(models.AIJob).filter(models.AIJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(error)
            commit_with_retry(db)
    except Exception as e:
        logger.error("Image segmentation job %s failed: %s", job_id, e, exc_info=True)
        job = db.query(models.AIJob).filter(models.AIJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = "Image segmentation failed."
            commit_with_retry(db)
    finally:
        db.close()


def run_embed_job(job_id: str, payload: EmbedPayload):
    db = SessionLocal()
    try:
        with get_inference_semaphore():
            response = embed_image(
                payload.image,
                sam_model=payload.sam_model,
            )
        job = db.query(models.AIJob).filter(models.AIJob.id == job_id).first()
        if job:
            job.status = "completed"
            job.result = json.dumps(response)
            commit_with_retry(db)
    except DetectionClientError as error:
        job = db.query(models.AIJob).filter(models.AIJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(error)
            commit_with_retry(db)
    except Exception as e:
        logger.error("Image embedding job %s failed: %s", job_id, e, exc_info=True)
        job = db.query(models.AIJob).filter(models.AIJob.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = "Image embedding failed."
            commit_with_retry(db)
    finally:
        db.close()


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


@router.post("")
def detect(payload: DetectPayload, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    job_id = _create_job(db)
    background_tasks.add_task(run_detect_job, job_id, payload)
    return {"job_id": job_id}


@router.post("/classify")
def classify(payload: ClassifyPayload, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    job_id = _create_job(db)
    background_tasks.add_task(run_classify_job, job_id, payload)
    return {"job_id": job_id}


@router.post("/segment")
def segment(payload: SegmentPayload, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    job_id = _create_job(db)
    background_tasks.add_task(run_segment_job, job_id, payload)
    return {"job_id": job_id}


@router.post("/embed")
def embed(payload: EmbedPayload, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    job_id = _create_job(db)
    background_tasks.add_task(run_embed_job, job_id, payload)
    return {"job_id": job_id}



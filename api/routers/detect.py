import logging
import time
import uuid
from typing import Any, Dict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from api.auth import get_current_user, require_csrf
from detector import (
    DetectionClientError,
    classify_image,
    detect_objects,
    embed_image,
    segment_point,
)
from schemas import ClassifyPayload, DetectPayload, EmbedPayload, SegmentPayload

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/detect",
    tags=["detect"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)

# Maximum age (in seconds) for unpolled jobs before they are automatically evicted from memory.
JOB_TTL_SECONDS = 600.0  # 10 minutes

# In-process dict: {job_id: {"status": str, "result": ..., "error": ..., "created_at": float}}
JOBS: Dict[str, Dict[str, Any]] = {}


def _cleanup_stale_jobs(max_age_seconds: float = JOB_TTL_SECONDS) -> int:
    """Evict jobs older than max_age_seconds to prevent memory leaks from abandoned polls."""
    now = time.time()
    stale_ids = [
        jid
        for jid, info in JOBS.items()
        if (now - info.get("created_at", now)) > max_age_seconds
    ]
    for jid in stale_ids:
        JOBS.pop(jid, None)
    return len(stale_ids)


def _create_job() -> str:
    _cleanup_stale_jobs()
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "pending", "created_at": time.time()}
    return job_id


def run_detect_job(job_id: str, payload: DetectPayload):
    try:
        response = detect_objects(
            payload.image,
            selection=payload.selection,
            prompts=payload.prompts,
            model_size=payload.model_size,
            confidence=payload.confidence,
            nms_threshold=payload.nms_threshold,
        )
        if job_id in JOBS:
            JOBS[job_id]["status"] = "completed"
            JOBS[job_id]["result"] = response
    except DetectionClientError as error:
        if job_id in JOBS:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["error"] = str(error)
    except Exception as e:
        logger.error("Object detection job %s failed: %s", job_id, e, exc_info=True)
        if job_id in JOBS:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["error"] = "Object detection failed."


def run_classify_job(job_id: str, payload: ClassifyPayload):
    try:
        response = classify_image(payload.image, selection=payload.selection)
        if job_id in JOBS:
            JOBS[job_id]["status"] = "completed"
            JOBS[job_id]["result"] = response
    except DetectionClientError as error:
        if job_id in JOBS:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["error"] = str(error)
    except Exception as e:
        logger.error("Image classification job %s failed: %s", job_id, e, exc_info=True)
        if job_id in JOBS:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["error"] = "Image classification failed."


def run_segment_job(job_id: str, payload: SegmentPayload):
    try:
        response = segment_point(
            payload.image,
            points=[{"x": p.x, "y": p.y} for p in payload.points],
            labels=payload.labels,
            prompt=payload.prompt,
            precision=payload.precision,
            bbox=payload.bbox,
            sam_model=payload.sam_model,
        )
        if job_id in JOBS:
            JOBS[job_id]["status"] = "completed"
            JOBS[job_id]["result"] = response
    except DetectionClientError as error:
        if job_id in JOBS:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["error"] = str(error)
    except Exception as e:
        logger.error("Image segmentation job %s failed: %s", job_id, e, exc_info=True)
        if job_id in JOBS:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["error"] = "Image segmentation failed."


def run_embed_job(job_id: str, payload: EmbedPayload):
    try:
        response = embed_image(
            payload.image,
            sam_model=payload.sam_model,
        )
        if job_id in JOBS:
            JOBS[job_id]["status"] = "completed"
            JOBS[job_id]["result"] = response
    except DetectionClientError as error:
        if job_id in JOBS:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["error"] = str(error)
    except Exception as e:
        logger.error("Image embedding job %s failed: %s", job_id, e, exc_info=True)
        if job_id in JOBS:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["error"] = "Image embedding failed."


@router.get("/status/{job_id}")
def get_job_status(job_id: str):
    _cleanup_stale_jobs()
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job not found or expired")

    job = JOBS[job_id]
    if job["status"] in ["completed", "failed"]:
        result = {k: v for k, v in job.items() if k != "created_at"}
        del JOBS[job_id]
        return result

    return {"status": "pending"}


@router.post("")
def detect(payload: DetectPayload, background_tasks: BackgroundTasks):
    job_id = _create_job()
    background_tasks.add_task(run_detect_job, job_id, payload)
    return {"job_id": job_id}


@router.post("/classify")
def classify(payload: ClassifyPayload, background_tasks: BackgroundTasks):
    job_id = _create_job()
    background_tasks.add_task(run_classify_job, job_id, payload)
    return {"job_id": job_id}


@router.post("/segment")
def segment(payload: SegmentPayload, background_tasks: BackgroundTasks):
    job_id = _create_job()
    background_tasks.add_task(run_segment_job, job_id, payload)
    return {"job_id": job_id}


@router.post("/embed")
def embed(payload: EmbedPayload, background_tasks: BackgroundTasks):
    job_id = _create_job()
    background_tasks.add_task(run_embed_job, job_id, payload)
    return {"job_id": job_id}


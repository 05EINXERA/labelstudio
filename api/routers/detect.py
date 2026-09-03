import time
import traceback
import uuid

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks

from detector import DetectionClientError, detect_objects, classify_image
from schemas import DetectPayload, ClassifyPayload, SegmentPayload
from api.auth import get_current_user
from logging_service import log_event

router = APIRouter(prefix="/api/detect", tags=["detect"], dependencies=[Depends(get_current_user)])

JOBS = {}

# How long a finished (or abandoned) job's result is kept before it is swept.
# A client normally deletes its own entry the moment it polls a terminal state
# (get_job_status below) — this only catches the case where it never does: the
# tab is closed, the page is reloaded, or the request that would have polled it
# never arrives. Without a ceiling those entries — including segmentation mask
# data — accumulate for the lifetime of the process.
# See .devnotes/server-optimization/04_INFERENCE.md (F9).
JOB_TTL_SECONDS = 600


def _sweep_expired_jobs() -> None:
    """Drop terminal jobs older than JOB_TTL_SECONDS.

    Called opportunistically from the three POST endpoints below rather than on
    a timer thread — this project has no background scheduler and one is not
    worth adding for a cleanup pass this cheap. A submission is exactly the
    moment new entries are about to be added, so it is also the natural place
    to reclaim old ones.

    Only ever removes `completed`/`failed` jobs with a `finished_at` — a
    `pending` job has no such timestamp and must never be swept out from under
    a client that is still legitimately polling it, no matter how long the
    underlying model call is taking.

    Iterates a snapshot of the keys, not the live dict, because this can run
    concurrently with a background job thread writing a new terminal result
    into JOBS (dict item assignment is atomic under the GIL, but mutating the
    dict while iterating it directly would still raise).
    """
    cutoff = time.monotonic() - JOB_TTL_SECONDS
    for job_id in list(JOBS.keys()):
        job = JOBS.get(job_id)
        if not job:
            continue
        finished_at = job.get("finished_at")
        if finished_at is not None and finished_at < cutoff:
            JOBS.pop(job_id, None)


def run_detect_job(job_id: str, payload: DetectPayload):
    try:
        response = detect_objects(
            payload.image,
            selection=payload.selection,
            prompts=payload.prompts,
            model_size=payload.model_size,
            confidence=payload.confidence,
            nms_threshold=payload.nms_threshold
        )
        JOBS[job_id] = {"status": "completed", "result": response, "finished_at": time.monotonic()}
    except DetectionClientError as error:
        JOBS[job_id] = {"status": "failed", "error": str(error), "finished_at": time.monotonic()}
    except Exception as e:
        traceback.print_exc()
        JOBS[job_id] = {"status": "failed", "error": "Object detection failed.", "finished_at": time.monotonic()}

def run_classify_job(job_id: str, payload: ClassifyPayload):
    try:
        response = classify_image(payload.image, selection=payload.selection)
        JOBS[job_id] = {"status": "completed", "result": response, "finished_at": time.monotonic()}
    except DetectionClientError as error:
        JOBS[job_id] = {"status": "failed", "error": str(error), "finished_at": time.monotonic()}
    except Exception as e:
        traceback.print_exc()
        JOBS[job_id] = {"status": "failed", "error": "Image classification failed.", "finished_at": time.monotonic()}

def run_segment_job(job_id: str, payload: SegmentPayload):
    from detector import segment_point
    try:
        response = segment_point(
            payload.image,
            points=[{"x": p.x, "y": p.y} for p in payload.points],
            labels=payload.labels,
            prompt=payload.prompt,
            precision=payload.precision,
            bbox=payload.bbox,
            sam_model=payload.sam_model
        )
        JOBS[job_id] = {"status": "completed", "result": response, "finished_at": time.monotonic()}
    except DetectionClientError as error:
        JOBS[job_id] = {"status": "failed", "error": str(error), "finished_at": time.monotonic()}
    except Exception as e:
        traceback.print_exc()
        JOBS[job_id] = {"status": "failed", "error": "Image segmentation failed.", "finished_at": time.monotonic()}

@router.get("/status/{job_id}")
def get_job_status(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job not found or expired")

    job = JOBS[job_id]
    if job["status"] in ["completed", "failed"]:
        # The client polled a terminal state: this is the normal, overwhelmingly
        # common path back to an empty dict, unrelated to the TTL sweep above.
        # `finished_at` is internal bookkeeping for that sweep and was never
        # part of the response contract, so it is dropped rather than returned.
        result = {k: v for k, v in job.items() if k != "finished_at"}
        del JOBS[job_id]
        return result

    return {"status": "pending"}

@router.post("")
def detect(payload: DetectPayload, background_tasks: BackgroundTasks):
    _sweep_expired_jobs()
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "pending"}
    log_event("ai.job", job=job_id, kind="detect")
    background_tasks.add_task(run_detect_job, job_id, payload)
    return {"job_id": job_id}

@router.post("/classify")
def classify(payload: ClassifyPayload, background_tasks: BackgroundTasks):
    _sweep_expired_jobs()
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "pending"}
    log_event("ai.job", job=job_id, kind="classify")
    background_tasks.add_task(run_classify_job, job_id, payload)
    return {"job_id": job_id}

@router.post("/segment")
def segment(payload: SegmentPayload, background_tasks: BackgroundTasks):
    _sweep_expired_jobs()
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "pending"}
    log_event("ai.job", job=job_id, kind="segment")
    background_tasks.add_task(run_segment_job, job_id, payload)
    return {"job_id": job_id}

from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
import traceback
import uuid

from detector import DetectionClientError, detect_objects, classify_image
from schemas import DetectPayload, ClassifyPayload, SegmentPayload
from api.auth import get_current_user

router = APIRouter(prefix="/api/detect", tags=["detect"], dependencies=[Depends(get_current_user)])

JOBS = {}

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
        JOBS[job_id] = {"status": "completed", "result": response}
    except DetectionClientError as error:
        JOBS[job_id] = {"status": "failed", "error": str(error)}
    except Exception as e:
        traceback.print_exc()
        JOBS[job_id] = {"status": "failed", "error": "Object detection failed."}

def run_classify_job(job_id: str, payload: ClassifyPayload):
    try:
        response = classify_image(payload.image, selection=payload.selection)
        JOBS[job_id] = {"status": "completed", "result": response}
    except DetectionClientError as error:
        JOBS[job_id] = {"status": "failed", "error": str(error)}
    except Exception as e:
        traceback.print_exc()
        JOBS[job_id] = {"status": "failed", "error": "Image classification failed."}

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
        JOBS[job_id] = {"status": "completed", "result": response}
    except DetectionClientError as error:
        JOBS[job_id] = {"status": "failed", "error": str(error)}
    except Exception as e:
        traceback.print_exc()
        JOBS[job_id] = {"status": "failed", "error": "Image segmentation failed."}

@router.get("/status/{job_id}")
def get_job_status(job_id: str):
    if job_id not in JOBS:
        raise HTTPException(status_code=404, detail="Job not found or expired")
    
    job = JOBS[job_id]
    if job["status"] in ["completed", "failed"]:
        result = job
        del JOBS[job_id]
        return result
    
    return {"status": "pending"}

@router.post("")
def detect(payload: DetectPayload, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "pending"}
    background_tasks.add_task(run_detect_job, job_id, payload)
    return {"job_id": job_id}

@router.post("/classify")
def classify(payload: ClassifyPayload, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "pending"}
    background_tasks.add_task(run_classify_job, job_id, payload)
    return {"job_id": job_id}

@router.post("/segment")
def segment(payload: SegmentPayload, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "pending"}
    background_tasks.add_task(run_segment_job, job_id, payload)
    return {"job_id": job_id}

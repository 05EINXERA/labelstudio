"""Annotation export (tracker P4.4, G6).

Filters tasks by status, builds a COCO-style JSON or a flat CSV, and returns
it as a downloadable file. Uses the in-process background-job pattern from
detect.py (JOBS dict + BackgroundTasks) even though JSON/CSV generation here
is fast enough to be synchronous, because the mask-rendering and image-
bundling formats that will land later (see the TODOs below) will not be, and
building the job plumbing once now avoids reshaping the frontend contract
later.

Rule 9 applies: this JOBS dict is in-process state, same constraint as
detect.py's — the app must stay a single uvicorn worker.

Not implemented (left as explicit rejections, not silent no-ops):
- include=with_mask_colors / mask_index_color / mask_binary: bounding-box and
  polygon annotations have no inherent raster mask; rendering one is a
  separate feature, not a format flag. See REFACTOR_MANAGEMENT.md open
  question 4.
- format=yolo / pascal_voc
- bundling original images into the export archive
"""
import json
import logging
import traceback
import uuid
from typing import List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

import models
from database import get_db
from schemas import ExportRequest, EXPORT_FORMATS, EXPORT_INCLUDE_OPTIONS, TASK_STATUSES
from api.auth import get_current_user
from api.routers.projects import get_owned_project

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/exports", tags=["exports"], dependencies=[Depends(get_current_user)])

JOBS = {}


def _round(v: float) -> float:
    return round(v, 2)


def _points_of(ann: dict) -> List[dict]:
    pts = ann.get("points") or []
    if pts and isinstance(pts[0], dict):
        return pts
    # x/y/width/height-only annotations (a plain box with no polygon points).
    x, y, w, h = ann.get("x", 0), ann.get("y", 0), ann.get("width", 0), ann.get("height", 0)
    return [{"x": x, "y": y}, {"x": x + w, "y": y}, {"x": x + w, "y": y + h}, {"x": x, "y": y + h}]


def _build_coco(tasks: List[models.Task], labels: List[models.Label]) -> dict:
    categories = [{"id": i + 1, "name": l.name, "supercategory": "none"} for i, l in enumerate(labels)]
    label_to_cat = {l.id: i + 1 for i, l in enumerate(labels)}

    images, annotations = [], []
    ann_id = 1
    for image_id, task in enumerate(tasks, start=1):
        images.append({"id": image_id, "file_name": task.description or f"task-{task.id}", "width": 0, "height": 0})
        try:
            anns = json.loads(task.annotations) if task.annotations else []
        except (ValueError, TypeError) as exc:
            logger.warning("Task %s has unparseable annotations, skipping in export: %s", task.id, exc)
            anns = []
        for ann in anns:
            if not isinstance(ann, dict) or ann.get("type") == "comment":
                continue
            category_id = label_to_cat.get(ann.get("labelId"))
            if category_id is None:
                continue  # annotation references a label that no longer exists
            points = _points_of(ann)
            xs = [p["x"] for p in points]
            ys = [p["y"] for p in points]
            bbox = [_round(min(xs)), _round(min(ys)), _round(max(xs) - min(xs)), _round(max(ys) - min(ys))]
            annotations.append({
                "id": ann_id, "image_id": image_id, "category_id": category_id,
                "segmentation": [[_round(c) for p in points for c in (p["x"], p["y"])]],
                "bbox": bbox, "area": _round(bbox[2] * bbox[3]), "iscrowd": 0,
            })
            ann_id += 1

    return {"images": images, "categories": categories, "annotations": annotations}


def _build_csv(tasks: List[models.Task], labels_by_id: dict) -> str:
    import csv
    import io

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["image", "label", "x", "y", "width", "height", "status"])
    for task in tasks:
        try:
            anns = json.loads(task.annotations) if task.annotations else []
        except (ValueError, TypeError) as exc:
            logger.warning("Task %s has unparseable annotations, skipping in export: %s", task.id, exc)
            anns = []
        for ann in anns:
            if not isinstance(ann, dict) or ann.get("type") == "comment":
                continue
            label = labels_by_id.get(ann.get("labelId"))
            points = _points_of(ann)
            xs = [p["x"] for p in points]
            ys = [p["y"] for p in points]
            writer.writerow([
                task.description or f"task-{task.id}",
                label.name if label else "unknown",
                _round(min(xs)), _round(min(ys)),
                _round(max(xs) - min(xs)), _round(max(ys) - min(ys)),
                task.status or "New",
            ])
    return buf.getvalue()


def _run_export_job(job_id: str, req: ExportRequest, project_id: int):
    from database import SessionLocal
    db = SessionLocal()
    try:
        query = db.query(models.Task).filter(models.Task.project_id == project_id)
        if req.statusFilter:
            query = query.filter(models.Task.status.in_(req.statusFilter))
        tasks = query.all()
        labels = db.query(models.Label).filter(models.Label.project_id == project_id).all()

        if req.format == "csv":
            labels_by_id = {l.id: l for l in labels}
            body = _build_csv(tasks, labels_by_id)
            media_type = "text/csv"
        else:
            body = json.dumps(_build_coco(tasks, labels), indent=2)
            media_type = "application/json"

        JOBS[job_id] = {
            "status": "completed", "body": body, "media_type": media_type,
            "task_count": len(tasks), "format": req.format,
        }
    except Exception:
        traceback.print_exc()
        JOBS[job_id] = {"status": "failed", "error": "Export failed."}
    finally:
        db.close()


@router.post("")
def create_export(req: ExportRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    get_owned_project(req.projectId, user, db)

    if req.format not in EXPORT_FORMATS:
        raise HTTPException(status_code=422, detail=f"format must be one of {EXPORT_FORMATS}.")
    if req.include not in EXPORT_INCLUDE_OPTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"include='{req.include}' is not implemented yet. Supported: {EXPORT_INCLUDE_OPTIONS}. "
                   "Mask rendering and image bundling are tracked but not built (see REFACTOR_MANAGEMENT.md Phase 4).",
        )
    if req.statusFilter:
        bad = [s for s in req.statusFilter if s not in TASK_STATUSES]
        if bad:
            raise HTTPException(status_code=422, detail=f"Unknown status filter values: {bad}. Valid: {TASK_STATUSES}.")

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "pending"}
    background_tasks.add_task(_run_export_job, job_id, req, req.projectId)
    return {"job_id": job_id}


@router.get("/{job_id}")
def get_export_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found or expired")
    if job["status"] == "completed":
        return {"status": "completed", "task_count": job["task_count"], "format": job["format"]}
    if job["status"] == "failed":
        return {"status": "failed", "error": job["error"]}
    return {"status": "pending"}


@router.get("/{job_id}/download")
def download_export(job_id: str):
    job = JOBS.get(job_id)
    if not job or job["status"] != "completed":
        raise HTTPException(status_code=404, detail="Export not ready or expired")
    body = job["body"]
    media_type = job["media_type"]
    del JOBS[job_id]  # one-shot download, consistent with detect.py's job cleanup
    ext = "csv" if media_type == "text/csv" else "json"
    return PlainTextResponse(
        body, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="export.{ext}"'},
    )

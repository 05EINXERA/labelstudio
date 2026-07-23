"""Annotation export (tracker P4.4, G6).

Filters tasks by status, builds a COCO-style JSON, a flat CSV, or a ZIP of
per-task JSON files, and returns it as a downloadable file.

The ZIP is deliberately multi-folder: `_build_zip` owns the archive and asks
each registered builder (ZIP_BUILDERS) for its (arcname, content) entries,
prefixing them into that format's own folder. Today only `pertask` is
registered, writing `jsons/<image>.json`; COCO, CSV and bundled images can be
added as further folders without reshaping the container or the download
handler. Format builders must never construct a ZIP themselves.

Uses the in-process background-job pattern from
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
import csv
import io
import json
import logging
import os
import traceback
import uuid
import zipfile
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from sqlalchemy.orm import Session

import models
from config import DATA_DIR
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
    # Enhanced T1: Add color, skeleton, keypoints, keypoint_colors to categories
    # Use label name for supercategory instead of "none"
    categories = [
        {
            "id": i + 1,
            "name": l.name,
            "supercategory": l.name,  # T1.3: use label name instead of "none"
            "color": l.color,  # T1.1: add color from Label
            "skeleton": [],  # T1.2: add empty arrays (FastLabel compatibility)
            "keypoints": [],
            "keypoint_colors": [],
        }
        for i, l in enumerate(labels)
    ]
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


def _image_size(task: models.Task) -> Tuple[int, int]:
    """Pixel dimensions of a task's image, or (0, 0) if unreadable.

    Task rows store no dimensions, but the reference per-task format requires
    them. Pillow only parses the header for `.size`, so this is a small read
    per task rather than a full decode.

    A missing or corrupt image must never fail the whole export — it degrades
    to the (0, 0) this format used to emit unconditionally.
    """
    if not task.image_path:
        return 0, 0
    # image_path is stored relative to DATA_DIR as "uploads/<name>" with a
    # forward slash (see projects._save_upload).
    path = os.path.join(DATA_DIR, *task.image_path.split("/"))
    try:
        with Image.open(path) as im:
            return im.size
    except (OSError, UnidentifiedImageError) as exc:
        logger.warning("Could not read image size for task %s (%s): %s", task.id, path, exc)
        return 0, 0


def _safe_stem(task: models.Task) -> str:
    """Archive-safe base name for a task, without extension.

    `task.description` is the raw client-supplied filename, so it can carry
    directory components (from either OS) or traversal segments. Anything that
    is not a plain name is discarded rather than sanitised piecemeal.
    """
    raw = task.description or ""
    # Strip both separators: a Windows-uploaded name can reach a POSIX server.
    base = os.path.basename(raw.replace("\\", "/").rstrip("/"))
    stem = os.path.splitext(base)[0].strip()
    if not stem or stem in (".", ".."):
        return f"task-{task.id}"
    return stem


def _pertask_object(task: models.Task, labels_by_id: dict) -> dict:
    """One task as a FastLabel per-task JSON object.

    Points are flattened to [x1,y1,x2,y2,...] per FastLabel convention, and
    label info (title, value, color) is embedded in each annotation because
    the source project's label ids mean nothing to an importer.

    `externalStatus` and `url` from the reference file are deliberately
    omitted: both are FastLabel-hosting artifacts with no analogue here.
    """
    try:
        anns = json.loads(task.annotations) if task.annotations else []
    except (ValueError, TypeError) as exc:
        logger.warning("Task %s has unparseable annotations, skipping in export: %s", task.id, exc)
        anns = []

    fastlabel_anns = []
    for i, ann in enumerate(anns, start=1):
        if not isinstance(ann, dict) or ann.get("type") == "comment":
            continue
        label = labels_by_id.get(ann.get("labelId"))
        if not label:
            continue  # annotation references a deleted label

        points = _points_of(ann)
        flat_points = [_round(coord) for p in points for coord in (p["x"], p["y"])]

        # Generate value from label name (strip spaces/special chars)
        value = label.name.replace(" ", "").replace("/", "").replace("(", "").replace(")", "").replace(",", "")

        fastlabel_anns.append({
            "id": ann.get("id") or uuid.uuid4().hex,
            "type": "polygon",
            "title": label.name,
            "value": value,
            "color": label.color,
            "order": i,
            "attributes": [],
            "points": flat_points,
            "rotation": 0,
            "keypoints": [],
            "confidenceScore": -1,
        })

    width, height = _image_size(task)
    return {
        "id": str(task.id),
        "name": task.description or f"task-{task.id}",
        "status": task.status or "New",
        "width": width,
        "height": height,
        "secondsToAnnotate": task.time_spent or 0,
        # Assignment/review fields: only `assignee` is a real column here. The
        # reviewer/approver and external* pair are emitted empty rather than
        # dropped so the object stays shape-compatible with the reference for
        # consumers that index them; they are not workflow state we track.
        "assignee": task.assignee or "",
        "reviewer": "",
        "approver": "",
        "externalAssignee": "",
        "externalReviewer": "",
        "externalApprover": "",
        "tags": [],
        "metadatas": [],
        "relations": [],
        "updatedAt": task.updated_at.isoformat() if task.updated_at else None,
        "annotations": fastlabel_anns,
    }


def _entries_pertask(tasks: List[models.Task], labels_by_id: dict) -> List[Tuple[str, bytes]]:
    """Per-task format's contribution to the export archive: one file per task.

    Returns (arcname, content) pairs whose names are relative to this format's
    own folder — the caller prepends the prefix (see ZIP_BUILDERS). Builders
    never touch the ZIP itself, so a second format can be added later without
    reshaping the container.
    """
    return [
        (f"{_safe_stem(task)}.json",
         json.dumps(_pertask_object(task, labels_by_id), indent=2).encode("utf-8"))
        for task in tasks
    ]


# Arcname prefix per format — the archive's directory contract. Adding a format
# is a row here plus a builder returning (arcname, content) pairs.
#   "coco": ("coco/", _entries_coco),   # future
#   "csv":  ("csv/",  _entries_csv),    # future
ZIP_BUILDERS = {
    "pertask": ("jsons/", _entries_pertask),
}

# Files allowed at the archive root rather than under a format prefix.
ZIP_ROOT_ALLOWED = {"classes.json", "manifest.json"}


def _build_zip(formats: List[str], tasks: List[models.Task], labels_by_id: dict) -> bytes:
    """Assemble one archive from the selected formats' entries.

    The container lives here, not in any builder, so several formats can share
    a single archive later. Takes a list today even though callers pass one
    format, so that widening the request needs no change at this layer.

    Collisions are resolved on the *full* arcname: `jsons/a.json` and a future
    `coco/a.json` must not false-collide, while a genuine duplicate within one
    folder is suffixed rather than silently overwritten.
    """
    buf = io.BytesIO()
    seen = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fmt in formats:
            prefix, builder = ZIP_BUILDERS[fmt]
            for name, content in builder(tasks, labels_by_id):
                arcname = f"{prefix}{name}"
                if prefix == "" and name not in ZIP_ROOT_ALLOWED:
                    raise ValueError(f"Builder '{fmt}' wrote unnamespaced entry '{name}'.")
                if arcname in seen:
                    stem, ext = os.path.splitext(arcname)
                    # Duplicate image names are legal in a project, so this is a
                    # real case, not a defensive branch. Suffix until unique.
                    n = 2
                    candidate = f"{stem}-{n}{ext}"
                    while candidate in seen:
                        n += 1
                        candidate = f"{stem}-{n}{ext}"
                    arcname = candidate
                seen.add(arcname)
                zf.writestr(arcname, content)
    return buf.getvalue()


def _build_csv(tasks: List[models.Task], labels_by_id: dict) -> str:
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
        labels_by_id = {l.id: l for l in labels}

        if req.format == "csv":
            body = _build_csv(tasks, labels_by_id)
            media_type = "text/csv"
            filename = f"export-{project_id}.csv"
        elif req.format == "pertask":
            # A ZIP of one JSON file per task, under jsons/. The container is
            # multi-folder by design so later formats can share one archive.
            body = _build_zip(["pertask"], tasks, labels_by_id)
            media_type = "application/zip"
            filename = f"export-pertask-{project_id}.zip"
        else:  # json (COCO)
            body = json.dumps(_build_coco(tasks, labels), indent=2)
            media_type = "application/json"
            filename = f"export-{project_id}.json"

        JOBS[job_id] = {
            "status": "completed", "body": body, "media_type": media_type,
            "filename": filename, "task_count": len(tasks), "format": req.format,
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
    filename = job["filename"]
    del JOBS[job_id]  # one-shot download, consistent with detect.py's job cleanup
    # Response, not PlainTextResponse: the per-task format is a binary ZIP that
    # a text response would UTF-8 encode and corrupt. Response takes str or
    # bytes, so the CSV and COCO branches are unaffected.
    return Response(
        content=body, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

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

Format logic lives in `formats/`, not here: this module owns request
validation, the job queue and the download handler. See
.devnotes/data-refactor/01_PLAN.md § 0 for the package layout.

A format that owns its whole directory layout (YOLO: classes.txt at the root
plus annotations/) uses `_zip_entries` instead, supplying complete arcnames.

Not implemented yet (left as explicit rejections, not silent no-ops):
- mask rendering, bundling original images into the archive
- format=pascal_voc
"""
import csv
import inspect
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
from sqlalchemy.orm import Session

import models
from database import get_db
from schemas import ExportRequest, EXPORT_FORMATS, EXPORT_INCLUDE_OPTIONS, TASK_STATUSES
from api.auth import get_current_user
from api.routers.projects import get_owned_project
from formats import annotations_json
from formats import coco as coco_format
from formats import masks as masks_format
from formats import yolo as yolo_format
from formats.common import image_size, points_of, round2, safe_stem, values_for_labels

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/exports", tags=["exports"], dependencies=[Depends(get_current_user)])

JOBS = {}


# COCO building lives in formats/coco.py. The shared helpers moved to
# formats/common.py; aliased here so the per-task and CSV builders below keep
# reading the same way until they move too.
_round = round2
_points_of = points_of
_image_size = image_size
_safe_stem = safe_stem


# The per-task object and its archive entries live in
# formats/annotations_json.py, alongside the single-file builder that emits the
# identical object shape.
_pertask_object = annotations_json.task_object
_entries_pertask = annotations_json.build_entries


# Arcname prefix per format — the archive's directory contract. Adding a format
# is a row here plus a builder returning (arcname, content) pairs.
#   "coco": ("coco/", _entries_coco),   # future
#   "csv":  ("csv/",  _entries_csv),    # future
ZIP_BUILDERS = {
    "annotations_pertask": ("jsons/", _entries_pertask),
}

# Files allowed at the archive root rather than under a format prefix.
ZIP_ROOT_ALLOWED = {"classes.json", "manifest.json"}


def _zip_entries(entries: List[Tuple[str, bytes]]) -> bytes:
    """Pack (arcname, content) pairs into an archive, as given.

    Unlike `_build_zip`, no prefix is applied: a format that owns its whole
    directory layout (YOLO's root classes.txt plus annotations/) supplies
    complete arcnames. Duplicates are suffixed rather than overwritten, since
    two tasks can legitimately share an image name.
    """
    buf = io.BytesIO()
    seen = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, content in entries:
            if arcname in seen:
                stem, ext = os.path.splitext(arcname)
                n = 2
                candidate = f"{stem}-{n}{ext}"
                while candidate in seen:
                    n += 1
                    candidate = f"{stem}-{n}{ext}"
                arcname = candidate
            seen.add(arcname)
            zf.writestr(arcname, content)
    return buf.getvalue()


def _call_builder(builder, tasks, labels_by_id, values, db):
    """Invoke a ZIP builder, passing only the arguments it declares.

    The contract is `builder(tasks, labels_by_id)`; the project-wide value map
    and a Session are optional extras. Inspecting the signature keeps both a
    minimal builder and a full one valid, so registering a format never
    requires accepting parameters it has no use for.
    """
    params = inspect.signature(builder).parameters
    kwargs = {}
    if "values" in params:
        kwargs["values"] = values
    if "db" in params:
        kwargs["db"] = db
    return builder(tasks, labels_by_id, **kwargs)


def _build_zip(formats: List[str], tasks: List[models.Task], labels_by_id: dict,
               values: Optional[Dict[str, str]] = None, db=None) -> bytes:
    """Assemble one archive from the selected formats' entries.

    The container lives here, not in any builder, so several formats can share
    a single archive later. Takes a list today even though callers pass one
    format, so that widening the request needs no change at this layer.

    Collisions are resolved on the *full* arcname: `jsons/a.json` and a future
    `coco/a.json` must not false-collide, while a genuine duplicate within one
    folder is suffixed rather than silently overwritten.

    The builder contract is `builder(tasks, labels_by_id)`. A builder may
    additionally accept `values` (the project-wide {label_id: value} map) and
    `db`; those are passed only when its signature declares them, so a minimal
    two-argument builder stays valid.
    """
    buf = io.BytesIO()
    seen = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fmt in formats:
            prefix, builder = ZIP_BUILDERS[fmt]
            for name, content in _call_builder(builder, tasks, labels_by_id, values, db):
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

        # Tasks a format could not represent, reported with the finished job
        # so a silently short export is visible to the user.
        skipped: List[dict] = []

        # One collision-free {label_id: value} map for the whole export, so
        # every format in it agrees on the class identifiers.
        values = values_for_labels(labels)

        # `db` is passed to the builders so image dimensions recovered from
        # disk are written back to the Task — this is a POST-initiated
        # background job with its own session, not a GET handler (rule 4).
        if req.format == "csv":
            body = _build_csv(tasks, labels_by_id)
            media_type = "text/csv"
            filename = f"export-{project_id}.csv"
        elif req.format == "annotations_pertask":
            # A ZIP of one JSON file per task, under jsons/. The container is
            # multi-folder by design so later formats can share one archive.
            body = _build_zip(["annotations_pertask"], tasks, labels_by_id, values, db)
            media_type = "application/zip"
            filename = f"export-pertask-{project_id}.zip"
        elif req.format == "annotations_json":
            # The same task objects as the per-task ZIP, in one JSON array.
            body = annotations_json.build_single(tasks, labels, db=db)
            media_type = "application/json"
            filename = f"export-annotations-{project_id}.json"
        elif req.format == "yolo":
            # classes.txt lives at the archive root and the label files under
            # annotations/, so this format owns its whole layout rather than
            # contributing into a single prefixed folder.
            entries, skipped = yolo_format.build(tasks, labels, db=db)
            body = _zip_entries(entries)
            media_type = "application/zip"
            filename = f"export-yolo-{project_id}.zip"
        elif req.format in ("masks_direct", "masks_index"):
            # Both variants emit semantic_segmentations/ and
            # instance_segmentations/, mirroring the reference archive.
            entries, skipped = masks_format.build(
                tasks, labels, indexed=req.format == "masks_index", db=db)
            body = _zip_entries(entries)
            media_type = "application/zip"
            filename = f"export-{req.format}-{project_id}.zip"
        else:  # coco
            body = json.dumps(coco_format.build(tasks, labels, db=db), indent=2)
            media_type = "application/json"
            filename = f"export-{project_id}.json"

        db.commit()

        JOBS[job_id] = {
            "status": "completed", "body": body, "media_type": media_type,
            "filename": filename, "task_count": len(tasks), "format": req.format,
            "skipped": skipped,
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

    if req.format in ("masks_direct", "masks_index"):
        # Counted before the job starts so an oversized request fails fast
        # rather than holding the single worker (rule 9) for minutes and
        # looking like a hang. Rasterizing is the one genuinely slow export:
        # a 20-megapixel image yields two full-size masks per task.
        count_query = db.query(models.Task).filter(models.Task.project_id == req.projectId)
        if req.statusFilter:
            count_query = count_query.filter(models.Task.status.in_(req.statusFilter))
        task_count = count_query.count()
        if task_count > masks_format.MAX_MASK_TASKS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Mask export is limited to {masks_format.MAX_MASK_TASKS} tasks per "
                    f"request; this project has {task_count}. Narrow the status filter "
                    "and export in batches."
                ),
            )

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
        return {
            "status": "completed",
            "task_count": job["task_count"],
            "format": job["format"],
            # Tasks a format could not represent (YOLO without image
            # dimensions, for example). Reported so a short export is visible
            # rather than silently missing files.
            "skipped": job.get("skipped", []),
        }
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

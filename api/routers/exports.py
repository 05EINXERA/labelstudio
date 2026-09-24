"""Annotation export endpoints (tracker P4.4, G6).

This module owns request validation, the job submission, the status poll and
the download. Building the export (formats, image outputs, archive layout)
lives in `api/export_service.py`, and it runs as a heavy job
(`jobs/runner.py`): in a child process in production, so a large export
never shares the web process's CPU with anyone's request. See
.devnotes/fix-exports-imports/.

A builder's `skipped` list (YOLO and the masks need image dimensions) is
threaded through the job status to the UI so a short export is never silent.

Duplicates are collapsed: an export identical to one still queued or running
(same project, format, image output, include and status filter) returns that
job's id instead of starting a second one. One project's exports run one at a
time; other projects' can run alongside, up to HEAVY_JOB_SLOTS.

Deprecated single-axis codes (`json` → coco, `pertask` → annotations_pertask,
`masks_direct`/`masks_index` → coco + that mask image output) resolve in
`resolve_export_request`; see GOTCHAS.md § 16.

Not implemented (left as an explicit rejection, not a silent no-op):
- format=pascal_voc
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

import models
from api import export_service
from api.auth import get_current_user, require_csrf
from api.permissions import ProjectRole, require_project
from config import EXPORT_TIMEOUT_S
from database import get_db
from formats import masks as masks_format
from jobs.runner import COMPLETED, FAILED, QUEUED, get_runner
from logging_service import log_event
from schemas import (
    ExportRequest,
    EXPORT_FORMATS,
    EXPORT_INCLUDE_OPTIONS,
    IMAGE_OUTPUTS,
    TASK_STATUSES,
    resolve_export_request,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/exports", tags=["exports"], dependencies=[Depends(get_current_user), Depends(require_csrf)])

# Archive assembly moved to api/export_service.py; re-exported for callers
# (and tests) that reach it through this module.
_zip_entries = export_service._zip_entries
_prefixed = export_service._prefixed
FORMAT_FOLDERS = export_service.FORMAT_FOLDERS
IMAGE_FOLDERS = export_service.IMAGE_FOLDERS


def _dedupe_key(project_id: int, fmt: str, image_output: str, include: str, status_filter) -> str:
    statuses = ",".join(sorted(status_filter)) if status_filter else "*"
    return f"export:{project_id}:{fmt}:{image_output}:{include}:{statuses}"


@router.post("")
def create_export(req: ExportRequest, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    # Reviewer+, matching the frontend nav gate (project-nav.js) — a one-click
    # full-dataset download is not meant for annotators. See
    # .devnotes/teams/03_API.md § 4.1.
    require_project(req.projectId, user, db, minimum=ProjectRole.REVIEWER)

    # Resolve deprecated single-axis codes before validating, so an old client
    # sending format=masks_index or format=json still passes.
    fmt, image_output = resolve_export_request(req.format, req.imageOutput)

    if fmt not in EXPORT_FORMATS:
        raise HTTPException(status_code=422, detail=f"format must be one of {EXPORT_FORMATS}.")
    if image_output not in IMAGE_OUTPUTS:
        raise HTTPException(status_code=422, detail=f"imageOutput must be one of {IMAGE_OUTPUTS}.")
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

    # Any raster image output is the slow path that holds the single worker
    # (rule 9): colour masks emit two full-size PNGs per task, and annotated/
    # binary one each. Cap on the resolved image output, not the format code.
    if image_output in ("mask_direct", "mask_index", "mask_binary", "annotated", "original"):
        # Counted before the job starts so an oversized request fails fast
        # rather than holding the worker for minutes and looking like a hang.
        count_query = db.query(models.Task).filter(models.Task.project_id == req.projectId)
        if req.statusFilter:
            count_query = count_query.filter(models.Task.status.in_(req.statusFilter))
        task_count = count_query.count()
        if task_count > masks_format.MAX_MASK_TASKS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Image output is limited to {masks_format.MAX_MASK_TASKS} tasks per "
                    f"request; this project has {task_count}. Narrow the status filter "
                    "and export in batches."
                ),
            )

    key = _dedupe_key(req.projectId, fmt, image_output, req.include, req.statusFilter)
    job, deduplicated = get_runner().submit(
        "export",
        {"project_id": req.projectId, "request": req.model_dump()},
        timeout=EXPORT_TIMEOUT_S,
        lane=f"export:{req.projectId}",
        key=key,
    )
    # Not destructive, but worth a line: an export is how a dataset leaves the
    # box, and "which batch went out on Tuesday" is a question the team asks of
    # the approved-status batches (CLAUDE.md rule 11a).
    log_event(
        "export.dedup" if deduplicated else "export.create",
        project=req.projectId,
        job=job.id,
        export_format=fmt,
        image_output=image_output,
        include=req.include,
        status_filter=",".join(req.statusFilter) if req.statusFilter else None,
    )
    return {"job_id": job.id, "deduplicated": deduplicated}


@router.get("/{job_id}")
def get_export_status(job_id: str):
    runner = get_runner()
    job = runner.get(job_id)
    if job is None or job.kind != "export":
        raise HTTPException(status_code=404, detail="Export job not found or expired")
    if job.status == COMPLETED:
        return {
            "status": "completed",
            "task_count": job.meta["task_count"],
            "format": job.meta["format"],
            "image_output": job.meta.get("image_output", "none"),
            # Tasks a format could not represent (YOLO without image
            # dimensions, for example). Reported so a short export is visible
            # rather than silently missing files.
            "skipped": job.meta.get("skipped", []),
        }
    if job.status == FAILED:
        return {"status": "failed", "error": job.error}
    # "pending" is kept as the status for both waiting states, so a client
    # bundle older than the queue still polls correctly; `state` and
    # `position` are additive.
    if job.status == QUEUED:
        return {"status": "pending", "state": "queued", "position": runner.queue_position(job)}
    return {"status": "pending", "state": "running"}


@router.get("/{job_id}/download")
def download_export(job_id: str):
    runner = get_runner()
    current = runner.get(job_id)
    if current is None or current.kind != "export":
        raise HTTPException(status_code=404, detail="Export not ready or expired")
    job = runner.take(job_id)  # one-shot download, consistent with detect.py's job cleanup
    if job is None:
        raise HTTPException(status_code=404, detail="Export not ready or expired")
    # Streamed from disk rather than held in RAM, and the file is removed once
    # the response has been sent.
    return FileResponse(
        job.body_path,
        media_type=job.meta["media_type"],
        headers={"Content-Disposition": f'attachment; filename="{job.meta["filename"]}"'},
        background=BackgroundTask(runner.discard_file, job.body_path),
    )

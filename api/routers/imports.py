"""Annotation import endpoints (tracker P4.2, G5).

This module owns the HTTP shape: authentication, the permission check (rules
1b/1c: MANAGER, because a replace-mode import wipes labels for everyone),
the upload cap, and turning a job's outcome into a response. Parsing, task
matching, label resolution and the apply live in `api/import_service.py` and
run as a heavy job (`jobs/runner.py`): in a child process in production.

The endpoints stay synchronous from the client's point of view: the request
awaits the job and returns the same body as before, so the frontend is
unchanged. What changed is where the work happens. These were `async def`
endpoints that parsed on the event loop itself, so a large upload stopped
the whole server from answering anything, `/health` included, until it was
done. Now the loop only awaits. See .devnotes/fix-exports-imports/ I-3.

A dry-run preview (`/preview`) reports the match before anything is written,
because a failed match is silent and expensive to discover after the fact: an
annotation for "img_01.jpg" that does not match any task's description is
simply skipped, and the only way to know that happened is to have asked first.
"""
import logging
import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

import models
from api import import_service
from api.auth import get_current_user, require_csrf
from api.permissions import ProjectRole, require_project
from api.uploads import save_capped
from config import IMPORT_QUEUE_WAIT_S, IMPORT_TIMEOUT_S
from database import get_db
from jobs.runner import COMPLETED, get_runner
from logging_service import log_event

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/imports", tags=["imports"], dependencies=[Depends(get_current_user), Depends(require_csrf)])

# Re-exported so callers (and tests) that reach the parser through this module
# keep working; the implementations live in api/import_service.py.
_parse_import_file = import_service._parse_import_file
_match_to_tasks = import_service._match_to_tasks
_resolve_label_ids = import_service._resolve_label_ids


async def _run_import_job(project_id: int, mode: str, file: UploadFile) -> dict:
    """Stream the upload to disk, run the import as a heavy job, return its body."""
    runner = get_runner()
    upload_path = os.path.join(runner.work_dir, f"{uuid.uuid4()}.upload")
    await save_capped(file, upload_path)
    job, _ = runner.submit(
        "import",
        {"project_id": project_id, "mode": mode, "filename": file.filename or "",
         "upload_path": upload_path},
        timeout=IMPORT_TIMEOUT_S,
        queue_wait=IMPORT_QUEUE_WAIT_S,
    )
    await runner.wait(job)

    # Events the job recorded (label.auto_create, import.annotations) belong on
    # this request's service-log line; the job ran outside the request.
    event = (job.result or {}).get("event")
    if event:
        log_event(event["event"], level=event.get("level") or "INFO", **(event.get("fields") or {}))

    if job.status != COMPLETED:
        raise HTTPException(status_code=job.error_status or 500, detail=job.error)
    return job.result["response"]


@router.post("/annotations/preview")
async def preview_annotation_import(
    projectId: int = Query(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Report what an import would do, without writing anything."""
    require_project(projectId, user, db, minimum=ProjectRole.MANAGER)
    # The request's own session is done: release its connection before the
    # wait, rather than holding a pool slot for the length of the job.
    db.close()
    return await _run_import_job(projectId, "preview", file)


@router.post("/annotations")
async def import_annotations(
    projectId: int = Query(...),
    mode: str = Query("merge", pattern="^(merge|replace)$"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Apply an annotation import. `merge` appends to each matched task's
    existing annotations; `replace` overwrites them. All-or-nothing: a failure
    anywhere rolls the whole import back.
    """
    require_project(projectId, user, db, minimum=ProjectRole.MANAGER)
    db.close()
    return await _run_import_job(projectId, mode, file)

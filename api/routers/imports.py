"""Annotation import endpoints (tracker P4.2, G5).

This module owns the HTTP shape: authentication, the permission check (rules
1b/1c: MANAGER, because a replace-mode import wipes labels for everyone),
the upload cap, and turning a job's outcome into a response. Parsing, task
matching, label resolution and the apply live in `api/import_service.py` and
run as a heavy job (`jobs/runner.py`): in a child process in production.

Two ways to call each endpoint. With `?async=1` (what the import page sends)
the request only uploads: it saves the file, submits the job and answers 202
with a job id, and the page polls `GET /api/imports/jobs/{id}`. Without it the
request awaits the job and returns the result body, as it always did; that path
stays for the test suite and for tabs still holding an older bundle.

Why async exists: every `apiFetch` aborts at 45 s, and that budget covered the
upload *and* the job. A large zip failed with "Could not import" even though
the job, which nothing cancels, went on to commit, so a retry in merge mode
duplicated every imported shape. See .devnotes/fix-import-timeout/01_PLAN.md.

Identical submissions (same project, mode and file bytes) are collapsed while
the first is still queued or running: the second request joins the first job
instead of starting another merge.

What changed earlier is where the work happens. These were `async def`
endpoints that parsed on the event loop itself, so a large upload stopped
the whole server from answering anything, `/health` included, until it was
done. Now the loop only awaits. See .devnotes/fix-exports-imports/ I-3.

A dry-run preview (`/preview`) reports the match before anything is written,
because a failed match is silent and expensive to discover after the fact: an
annotation for "img_01.jpg" that does not match any task's description is
simply skipped, and the only way to know that happened is to have asked first.
"""
import hashlib
import logging
import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

import models
from api import import_service
from api.auth import get_current_user, require_csrf
from api.permissions import ProjectRole, require_project
from api.uploads import save_capped
from config import IMPORT_QUEUE_WAIT_S, IMPORT_TIMEOUT_S
from database import get_db
from jobs.runner import COMPLETED, FAILED, QUEUED, Job, get_runner
from logging_service import log_event
from schemas import ImportJobStatus, ImportJobSubmitted

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/imports", tags=["imports"], dependencies=[Depends(get_current_user), Depends(require_csrf)])

# Re-exported so callers (and tests) that reach the parser through this module
# keep working; the implementations live in api/import_service.py.
_parse_import_file = import_service._parse_import_file
_match_to_tasks = import_service._match_to_tasks
_resolve_label_ids = import_service._resolve_label_ids


async def _submit_import_job(project_id: int, mode: str, file: UploadFile):
    """Stream the upload to disk and queue the import. Returns (job, deduplicated).

    The dedupe key includes the content hash, computed on the copy to disk:
    the same bytes submitted again while the first job is still active join
    that job. The joining request's own copy of the upload is then deleted,
    since the job it joined already has one.
    """
    runner = get_runner()
    upload_path = os.path.join(runner.work_dir, f"{uuid.uuid4()}.upload")
    digest = hashlib.sha256()
    await save_capped(file, upload_path, hasher=digest)
    job, deduplicated = runner.submit(
        "import",
        {"project_id": project_id, "mode": mode, "filename": file.filename or "",
         "upload_path": upload_path},
        timeout=IMPORT_TIMEOUT_S,
        queue_wait=IMPORT_QUEUE_WAIT_S,
        key=f"import:{project_id}:{mode}:{digest.hexdigest()}",
    )
    if deduplicated:
        runner.discard_file(upload_path)
    return job, deduplicated


def _log_job_event_once(job: Job) -> None:
    """Put the job's own event (label.auto_create, import.annotations) on the
    service log exactly once, whichever request first sees the job finish:
    the job ran outside any request."""
    if job.meta.get("_event_logged"):
        return
    job.meta["_event_logged"] = True
    event = (job.result or {}).get("event")
    if event:
        log_event(event["event"], level=event.get("level") or "INFO", **(event.get("fields") or {}))


async def _run_import_job(project_id: int, mode: str, file: UploadFile) -> dict:
    """The synchronous path: submit, await the job, return its body."""
    runner = get_runner()
    job, _ = await _submit_import_job(project_id, mode, file)
    await runner.wait(job)
    _log_job_event_once(job)
    if job.status != COMPLETED:
        raise HTTPException(status_code=job.error_status or 500, detail=job.error)
    return job.result["response"]


async def _start_import(project_id: int, mode: str, file: UploadFile, run_async: bool):
    if not run_async:
        return await _run_import_job(project_id, mode, file)
    job, deduplicated = await _submit_import_job(project_id, mode, file)
    log_event("import.submit", project=project_id, job=job.id, mode=mode,
              deduplicated=deduplicated)
    body = ImportJobSubmitted(job_id=job.id, deduplicated=deduplicated)
    return JSONResponse(status_code=202, content=body.model_dump())


@router.post("/annotations/preview")
async def preview_annotation_import(
    projectId: int = Query(...),
    run_async: bool = Query(False, alias="async"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Report what an import would do, without writing anything."""
    require_project(projectId, user, db, minimum=ProjectRole.MANAGER)
    # The request's own session is done: release its connection before the
    # wait, rather than holding a pool slot for the length of the job.
    db.close()
    return await _start_import(projectId, "preview", file, run_async)


@router.post("/annotations")
async def import_annotations(
    projectId: int = Query(...),
    mode: str = Query("merge", pattern="^(merge|replace)$"),
    run_async: bool = Query(False, alias="async"),
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
    return await _start_import(projectId, mode, file, run_async)


@router.get("/jobs/{job_id}", response_model=ImportJobStatus)
def import_job_status(
    job_id: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Where an `?async=1` import stands.

    MANAGER on the job's project, like the endpoints that create it (rule 1b);
    a caller with no role gets the same 404 as an unknown id. Finished jobs
    stay readable for the runner's result TTL, so a page reloaded mid-import
    can still show how it ended. Writes nothing to the database.
    """
    runner = get_runner()
    job = runner.get(job_id)
    if job is None or job.kind != "import":
        raise HTTPException(status_code=404, detail="Import job not found or expired.")
    require_project(job.spec["project_id"], user, db, minimum=ProjectRole.MANAGER)

    mode = job.spec.get("mode")
    if job.status == COMPLETED:
        _log_job_event_once(job)
        return ImportJobStatus(status="completed", mode=mode, result=job.result["response"])
    if job.status == FAILED:
        _log_job_event_once(job)
        return ImportJobStatus(status="failed", mode=mode, error=job.error,
                               error_status=job.error_status)
    if job.status == QUEUED:
        return ImportJobStatus(status="pending", state="queued", mode=mode,
                               position=runner.queue_position(job))
    return ImportJobStatus(status="pending", state="running", mode=mode)

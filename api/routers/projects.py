import json
import logging
import os
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, UploadFile, File, Query, HTTPException, Request
from sqlalchemy.orm import Session
from sqlalchemy import func

import models
import schemas
from config import DATA_DIR, MAX_UPLOAD_FILES
from database import get_db, commit_with_retry
from schemas import ProjectModel, ProjectMetrics, ProjectSummary
from api.auth import get_current_user, require_csrf
from api.permissions import (
    ProjectRole,
    accessible_project_ids,
    effective_project_role,
    require_project,
)
from formats.common import measure_image

logger = logging.getLogger(__name__)


def get_owned_project(project_id: int, user: models.User, db: Session) -> models.Project:
    """DEPRECATED — use `require_project` with an explicit minimum role.

    Kept for one release as a thin alias so no call site can be silently missed
    during the Teams migration; Phase 5 (F5) deletes it. `manager` is the
    conservative stand-in for the old ownership check: everything this used to
    guard was an administrative act.

    New code must call `api.permissions.require_project` directly and state the
    minimum role the endpoint actually needs — see .devnotes/teams/03_API.md
    § 4.1 for the per-call-site table.
    """
    return require_project(project_id, user, db, minimum=ProjectRole.MANAGER)


def _role_value(role) -> Optional[str]:
    """`ProjectRole` (or None) as the plain string the API exposes."""
    return role.value if role is not None else None


def _count_comments(annotations: Optional[str]) -> int:
    """Number of comment annotations in a task's serialized annotation blob."""
    if not annotations or '"comment"' not in annotations:
        return 0
    try:
        annots = json.loads(annotations)
    except (ValueError, TypeError) as exc:
        logger.warning("Skipping unparseable annotations: %s", exc)
        return 0
    return sum(1 for a in annots if isinstance(a, dict) and a.get("type") == "comment")


def _derive_status(total: int, completed: int) -> Optional[str]:
    """Project status implied by its task counts, or None if unchanged."""
    if total > 0 and completed == total:
        return "Completed"
    if completed > 0:
        return "In Progress"
    return None

def _aggregate_metrics(project_ids: List[int], db: Session) -> dict:
    """Task + label metrics for each id in `project_ids`.

    Two queries total regardless of how many projects are passed, so the list
    page does not fan out per row. Ids with no tasks still get a zeroed entry.
    """
    metrics = {
        pid: {"total": 0, "completed": 0, "in_progress": 0, "comments": 0,
              "progress": 0, "classes": 0, "total_time": 0, "avg_time_per_task": 0}
        for pid in project_ids
    }
    if not project_ids:
        return metrics

    tasks = db.query(
        models.Task.project_id, models.Task.status,
        models.Task.annotations, models.Task.time_spent,
    ).filter(models.Task.project_id.in_(project_ids)).all()

    for t in tasks:
        entry = metrics[t.project_id]
        entry["total"] += 1
        if t.status == 'Completed':
            entry["completed"] += 1
        elif t.status == 'In Progress':
            entry["in_progress"] += 1
        entry["comments"] += _count_comments(t.annotations)
        entry["total_time"] += t.time_spent or 0

    label_counts = db.query(
        models.Label.project_id, func.count(models.Label.id),
    ).filter(models.Label.project_id.in_(project_ids)).group_by(models.Label.project_id).all()
    for pid, count in label_counts:
        metrics[pid]["classes"] = count

    for entry in metrics.values():
        total = entry["total"]
        if total > 0:
            entry["progress"] = int(entry["completed"] / total * 100)
            entry["avg_time_per_task"] = int(entry["total_time"] / total)

    return metrics


router = APIRouter(
    prefix="/api/projects",
    tags=["projects"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)

@router.get("", response_model=List[ProjectSummary])
def get_projects(
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Every project the caller can reach, with its task metrics merged in.

    Scope comes from the token, never from a client-supplied `creator`. It is
    now "reachable" rather than "owned": owned ∪ granted-through-a-team ∪
    org-visible. Under a shared account with no teams that resolves to exactly
    the same set as before.
    """
    project_ids = accessible_project_ids(user, db)
    if not project_ids:
        return []

    projects = (
        db.query(models.Project).filter(models.Project.id.in_(project_ids)).all()
    )
    metrics = _aggregate_metrics(project_ids, db)

    return [
        ProjectSummary(
            id=p.id, name=p.name, slug=p.slug, type=p.type, status=p.status,
            creator=p.creator, created_at=p.created_at,
            my_role=_role_value(effective_project_role(user, p.id, db, request=request)),
            is_owner=p.owner_id == user.id,
            **metrics[p.id],
        )
        for p in projects
    ]

@router.get("/{project_id}")
def get_project(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    p = require_project(project_id, user, db, minimum=ProjectRole.VIEWER, request=request)
    role = effective_project_role(user, project_id, db, request=request)
    # my_role/is_owner are *added* fields — the rest of this shape is unchanged
    # so a cached JS bundle keeps working (03_API.md § 8). The whole Phase 4 UI
    # levels itself on these two.
    return {
        "id": p.id, "name": p.name, "slug": p.slug, "type": p.type,
        "status": p.status, "creator": p.creator, "created_at": p.created_at,
        "my_role": role.value if role else None,
        "is_owner": p.owner_id == user.id,
        "restrict_to_assigned_team": p.restrict_to_assigned_team,
        "visibility": p.visibility,
    }

@router.get("/{project_id}/metrics", response_model=ProjectMetrics)
def get_project_metrics(project_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    project = require_project(project_id, user, db, minimum=ProjectRole.VIEWER)
    m = _aggregate_metrics([project_id], db)[project_id]

    # This endpoint used to write the derived status back to the project, which
    # made a GET mutate the database (CLAUDE.md rule 4). The status is now
    # reported without being persisted; the write happens on task update.
    derived = _derive_status(m["total"], m["completed"])

    return ProjectMetrics(status=derived or project.status, **m)

@router.post("")
def create_project(project: ProjectModel, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    # The owner is the authenticated caller; `creator` is only a display name.
    db_project = models.Project(name=project.name, slug=project.slug, type=project.type, status="Preparing", creator=user.username, owner_id=user.id)
    db.add(db_project)
    commit_with_retry(db)
    db.refresh(db_project)
    return {"id": db_project.id, "status": "ok"}

def _apply_project_update(db_project: models.Project, project_update: schemas.ProjectUpdate) -> None:
    if project_update.name is not None:
        db_project.name = project_update.name
        db_project.slug = project_update.name.lower().replace(" ", "-")
    if project_update.status is not None:
        db_project.status = project_update.status

@router.patch("/{project_id}")
def patch_project(project_id: int, project_update: schemas.ProjectUpdate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    db_project = require_project(project_id, user, db, minimum=ProjectRole.MANAGER)
    _apply_project_update(db_project, project_update)
    commit_with_retry(db)
    return {"status": "ok"}

@router.delete("/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    # Owner only. Deleting a project destroys everyone's work on it, which is
    # not something a delegated manager should be able to do.
    db_project = require_project(project_id, user, db, minimum=ProjectRole.OWNER)
    db.query(models.Task).filter(models.Task.project_id == project_id).delete()
    db.query(models.Label).filter(models.Label.project_id == project_id).delete()
    # E-12: grants are deleted explicitly alongside tasks and labels rather than
    # left to ondelete=CASCADE, which SQLite only honours with
    # PRAGMA foreign_keys=ON. An orphaned grant would keep pointing at a project
    # that no longer exists.
    db.query(models.ProjectGrant).filter(
        models.ProjectGrant.project_id == project_id
    ).delete()
    db.delete(db_project)
    commit_with_retry(db)
    return {"status": "ok"}

ALLOWED_UPLOAD_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MB per image
_UPLOAD_CHUNK = 1024 * 1024


def _save_upload(f: UploadFile, uploads_dir: str) -> str:
    """Stream one upload to disk, enforcing the size cap. Returns the db path.

    Streamed in chunks rather than `f.file.read()` so a large file cannot be
    pulled into memory in full, and the cap is enforced while writing rather
    than after.
    """
    ext = os.path.splitext(f.filename or "")[1].lower()
    if ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise ValueError(f"File type '{ext or 'unknown'}' is not allowed.")

    new_filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(uploads_dir, new_filename)

    written = 0
    try:
        with open(filepath, "wb") as out_file:
            while True:
                chunk = f.file.read(_UPLOAD_CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise ValueError(
                        f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit."
                    )
                out_file.write(chunk)
    except Exception:
        # Never leave a partial or oversized file behind.
        try:
            os.remove(filepath)
        except OSError as exc:
            logger.warning("Could not remove partial upload %s: %s", filepath, exc)
        raise

    if written == 0:
        try:
            os.remove(filepath)
        except OSError as exc:
            logger.warning("Could not remove empty upload %s: %s", filepath, exc)
        raise ValueError("File is empty.")

    # Always a forward slash: this is served as a URL path, and os.path.join
    # would produce a backslash on Windows that breaks the <img src>.
    return f"uploads/{new_filename}"


@router.post("/{project_id}/upload")
def upload_files(project_id: int, assignee: Optional[str] = Query(None), file: List[UploadFile] = File(...), db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Bulk image upload. One bad file no longer aborts the whole batch.

    Previously any disallowed extension raised mid-loop, so earlier files were
    left on disk with no task row and the client got a 400 with no record of
    what did succeed. Each file is now reported individually.
    """
    require_project(project_id, user, db, minimum=ProjectRole.MANAGER)

    # Bounds the work one request can queue. Each file is streamed to disk while
    # holding a worker thread, so an unbounded batch lets a single request stall
    # the shared threadpool for everyone else on the instance.
    if len(file) > MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=413,
            detail=(
                f"{len(file)} files in one request; the limit is "
                f"{MAX_UPLOAD_FILES}. Upload in smaller batches."
            ),
        )

    uploads_dir = os.path.join(DATA_DIR, "uploads")
    os.makedirs(uploads_dir, exist_ok=True)

    # Existing task names in this project, so a re-upload of the same image
    # is rejected instead of silently creating a second task with an
    # identical name. Seeded with names already used *within this batch* too,
    # since a bulk upload can itself contain the same filename twice.
    existing_names = {
        name for (name,) in db.query(models.Task.description)
        .filter(models.Task.project_id == project_id).all()
    }

    uploaded = []
    failed = []
    duplicates = []

    for f in file:
        if f.filename in existing_names:
            duplicates.append(f.filename)
            continue
        try:
            db_filepath = _save_upload(f, uploads_dir)
        except ValueError as exc:
            failed.append({"filename": f.filename, "error": str(exc)})
            continue
        except OSError as exc:
            logger.error("Failed to write upload %s: %s", f.filename, exc)
            failed.append({"filename": f.filename, "error": "Could not save the file."})
            continue

        # Measured once here rather than on every export: YOLO normalizes by
        # these and mask rasterization sizes its canvas from them. Pillow reads
        # only the header, so this adds no meaningful cost to a request that
        # already wrote the file.
        width, height = measure_image(os.path.join(DATA_DIR, *db_filepath.split("/")))

        db.add(models.Task(
            project_id=project_id, image_path=db_filepath,
            description=f.filename, status='New', assignee=assignee,
            image_width=width, image_height=height,
        ))
        uploaded.append({"filename": f.filename, "path": db_filepath})
        # Seen within this same request too, so a batch containing the same
        # filename twice only creates one task and flags the rest as dupes.
        existing_names.add(f.filename)

    commit_with_retry(db)
    return {
        "status": "ok",
        "uploaded": uploaded,
        "failed": failed,
        "duplicates": duplicates,
        # Legacy field: project_details.js only checked res.ok, but keep the
        # shape until that page is deleted (tracker P5.1).
        "files": [u["path"] for u in uploaded],
    }

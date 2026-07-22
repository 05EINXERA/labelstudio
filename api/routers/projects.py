import json
import logging
import os
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, UploadFile, File, Query, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func

import models
import schemas
from config import DATA_DIR
from database import get_db
from schemas import ProjectModel, ProjectMetrics, ProjectSummary
from api.auth import get_current_user

logger = logging.getLogger(__name__)


def get_owned_project(project_id: int, user: models.User, db: Session) -> models.Project:
    """Return the project if `user` owns it, else raise 404.

    404 rather than 403 so the API does not confirm the existence of other
    users' project ids.
    """
    project = db.query(models.Project).filter(
        models.Project.id == project_id,
        models.Project.owner_id == user.id,
    ).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


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


router = APIRouter(prefix="/api/projects", tags=["projects"], dependencies=[Depends(get_current_user)])

@router.get("", response_model=List[ProjectSummary])
def get_projects(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Every project the caller owns, with its task metrics merged in.

    Scope comes from the token, never from a client-supplied `creator`.
    """
    # Scope comes from the token, never from a client-supplied `creator`.
    projects = db.query(models.Project).filter(models.Project.owner_id == user.id).all()
    if not projects:
        return []

    project_ids = [p.id for p in projects]
    metrics = _aggregate_metrics(project_ids, db)

    return [
        ProjectSummary(
            id=p.id, name=p.name, slug=p.slug, type=p.type, status=p.status,
            creator=p.creator, assignee=p.assignee, created_at=p.created_at,
            **metrics[p.id],
        )
        for p in projects
    ]

@router.get("/{project_id}")
def get_project(project_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    p = get_owned_project(project_id, user, db)
    return {"id": p.id, "name": p.name, "slug": p.slug, "type": p.type, "status": p.status, "creator": p.creator, "created_at": p.created_at, "assignee": p.assignee}

@router.get("/{project_id}/metrics", response_model=ProjectMetrics)
def get_project_metrics(project_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    project = get_owned_project(project_id, user, db)
    m = _aggregate_metrics([project_id], db)[project_id]

    # This endpoint used to write the derived status back to the project, which
    # made a GET mutate the database (CLAUDE.md rule 4). The status is now
    # reported without being persisted; the write happens on task update.
    derived = _derive_status(m["total"], m["completed"])

    return ProjectMetrics(status=derived or project.status, **m)

@router.post("")
def create_project(project: ProjectModel, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    # The owner is the authenticated caller; `creator` is only a display name.
    db_project = models.Project(name=project.name, slug=project.slug, type=project.type, status="Preparing", creator=user.username, owner_id=user.id, assignee=project.assignee)
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    return {"id": db_project.id, "status": "ok"}

def _apply_project_update(db_project: models.Project, project_update: schemas.ProjectUpdate) -> None:
    if project_update.name is not None:
        db_project.name = project_update.name
        db_project.slug = project_update.name.lower().replace(" ", "-")
    if project_update.status is not None:
        db_project.status = project_update.status
    if project_update.assignee is not None:
        db_project.assignee = project_update.assignee

@router.patch("/{project_id}")
def patch_project(project_id: int, project_update: schemas.ProjectUpdate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    db_project = get_owned_project(project_id, user, db)
    _apply_project_update(db_project, project_update)
    db.commit()
    return {"status": "ok"}

@router.delete("/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    db_project = get_owned_project(project_id, user, db)
    db.query(models.Task).filter(models.Task.project_id == project_id).delete()
    db.query(models.Label).filter(models.Label.project_id == project_id).delete()
    db.delete(db_project)
    db.commit()
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
    get_owned_project(project_id, user, db)
    uploads_dir = os.path.join(DATA_DIR, "uploads")
    os.makedirs(uploads_dir, exist_ok=True)

    uploaded = []
    failed = []

    for f in file:
        try:
            db_filepath = _save_upload(f, uploads_dir)
        except ValueError as exc:
            failed.append({"filename": f.filename, "error": str(exc)})
            continue
        except OSError as exc:
            logger.error("Failed to write upload %s: %s", f.filename, exc)
            failed.append({"filename": f.filename, "error": "Could not save the file."})
            continue

        db.add(models.Task(
            project_id=project_id, image_path=db_filepath,
            description=f.filename, status='New', assignee=assignee,
        ))
        uploaded.append({"filename": f.filename, "path": db_filepath})

    db.commit()
    return {
        "status": "ok",
        "uploaded": uploaded,
        "failed": failed,
        # Legacy field: project_details.js only checked res.ok, but keep the
        # shape until that page is deleted (tracker P5.1).
        "files": [u["path"] for u in uploaded],
    }

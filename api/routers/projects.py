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
from database import get_db
from schemas import ProjectModel, ProjectMetrics
from api.auth import get_current_user

logger = logging.getLogger(__name__)


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

router = APIRouter(prefix="/api/projects", tags=["projects"], dependencies=[Depends(get_current_user)])

@router.get("")
def get_projects(creator: Optional[str] = Query(None), db: Session = Depends(get_db)):
    if creator:
        projects = db.query(models.Project).filter(func.lower(models.Project.creator) == func.lower(creator)).all()
    else:
        projects = db.query(models.Project).all()
    return [{"id": p.id, "name": p.name, "slug": p.slug, "type": p.type, "status": p.status, "creator": p.creator, "created_at": p.created_at, "assignee": p.assignee} for p in projects]

@router.get("/{project_id}/metrics", response_model=ProjectMetrics)
def get_project_metrics(project_id: int, db: Session = Depends(get_db)):
    tasks = db.query(
        models.Task.project_id, models.Task.status,
        models.Task.annotations, models.Task.time_spent,
    ).filter(models.Task.project_id == project_id).all()

    total = len(tasks)
    completed = sum(1 for t in tasks if t.status == 'Completed')
    comments_count = sum(_count_comments(t.annotations) for t in tasks)
    total_time = sum(t.time_spent or 0 for t in tasks)
    progress = int((completed / total * 100)) if total > 0 else 0

    # This endpoint used to write the derived status back to the project, which
    # made a GET mutate the database (CLAUDE.md rule 4). The status is now
    # reported without being persisted; the write happens on task update.
    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    derived = _derive_status(total, completed)

    return ProjectMetrics(
        total=total,
        completed=completed,
        progress=progress,
        comments=comments_count,
        total_time=total_time,
        avg_time_per_task=int(total_time / total) if total > 0 else 0,
        status=derived or (project.status if project else None),
    )

@router.get("/metrics/batch")
def get_projects_metrics_batch(creator: Optional[str] = Query(None), db: Session = Depends(get_db)):
    if creator:
        projects = db.query(models.Project).filter(func.lower(models.Project.creator) == func.lower(creator)).all()
    else:
        projects = db.query(models.Project).all()
        
    project_ids = [p.id for p in projects]
    if not project_ids:
        return {}
        
    tasks = db.query(
        models.Task.project_id, models.Task.status,
        models.Task.annotations, models.Task.time_spent,
    ).filter(models.Task.project_id.in_(project_ids)).all()

    metrics = {
        pid: {"total": 0, "completed": 0, "comments": 0, "progress": 0,
              "total_time": 0, "avg_time_per_task": 0}
        for pid in project_ids
    }
    for t in tasks:
        entry = metrics[t.project_id]
        entry["total"] += 1
        if t.status == 'Completed':
            entry["completed"] += 1
        entry["comments"] += _count_comments(t.annotations)
        entry["total_time"] += t.time_spent or 0

    for pid in project_ids:
        entry = metrics[pid]
        total = entry["total"]
        entry["progress"] = int((entry["completed"] / total * 100)) if total > 0 else 0
        entry["avg_time_per_task"] = int(entry["total_time"] / total) if total > 0 else 0

    return metrics


@router.post("")
def create_project(project: ProjectModel, db: Session = Depends(get_db)):
    db_project = models.Project(name=project.name, slug=project.slug, type=project.type, status="Preparing", creator=project.creator, assignee=project.assignee)
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    return {"id": db_project.id, "status": "ok"}

@router.post("/update")
def update_project(project_update: schemas.ProjectUpdate, db: Session = Depends(get_db)):
    db_project = db.query(models.Project).filter(models.Project.id == project_update.id).first()
    if db_project:
        if project_update.name is not None:
            db_project.name = project_update.name
            db_project.slug = project_update.name.lower().replace(" ", "-")
        if project_update.status is not None:
            db_project.status = project_update.status
        if project_update.assignee is not None:
            db_project.assignee = project_update.assignee
        db.commit()
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Project not found")

@router.delete("/{project_id}")
def delete_project(project_id: int, db: Session = Depends(get_db)):
    db_project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if db_project:
        db.query(models.Task).filter(models.Task.project_id == project_id).delete()
        db.delete(db_project)
        db.commit()
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Project not found")

from config import DATA_DIR

@router.post("/{project_id}/upload")
def upload_files(project_id: int, assignee: Optional[str] = Query(None), file: List[UploadFile] = File(...), db: Session = Depends(get_db)):
    uploads_dir = os.path.join(DATA_DIR, "uploads")
    os.makedirs(uploads_dir, exist_ok=True)
    saved_files = []
    
    ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
    
    for f in file:
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"File type {ext} is not allowed.")
            
        new_filename = f"{uuid.uuid4().hex}{ext}"
        filepath = os.path.join(uploads_dir, new_filename)
        
        # Save relative to uploads dir for DB
        db_filepath = os.path.join("uploads", new_filename)
        
        with open(filepath, "wb") as out_file:
            out_file.write(f.file.read())
            
        task = models.Task(project_id=project_id, image_path=db_filepath, description=f.filename, status='New', assignee=assignee)
        db.add(task)
        saved_files.append(db_filepath)
        
    db.commit()
    return {"status": "ok", "files": saved_files}

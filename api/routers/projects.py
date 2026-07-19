import os
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import func
from sqlalchemy.orm import Session

import models
from api.auth import get_current_user
from database import get_db
from schemas import ProjectModel

router = APIRouter(
    prefix="/api/projects", tags=["projects"], dependencies=[Depends(get_current_user)]
)


@router.get("")
def get_projects(creator: Optional[str] = Query(None), db: Session = Depends(get_db)):
    if creator:
        projects = (
            db.query(models.Project)
            .filter(func.lower(models.Project.creator) == func.lower(creator))
            .all()
        )
    else:
        projects = db.query(models.Project).all()
    return [
        {
            "id": p.id,
            "name": p.name,
            "slug": p.slug,
            "type": p.type,
            "status": p.status,
            "creator": p.creator,
            "created_at": p.created_at,
            "assignee": p.assignee,
        }
        for p in projects
    ]


@router.get("/{project_id}/metrics")
def get_project_metrics(project_id: int, db: Session = Depends(get_db)):
    tasks = (
        db.query(models.Task.project_id, models.Task.status, models.Task.annotations)
        .filter(models.Task.project_id == project_id)
        .all()
    )
    total = len(tasks)
    completed = sum(1 for t in tasks if t.status == "Completed")

    comments_count = 0
    import json

    for t in tasks:
        if t.annotations and '"comment"' in t.annotations:
            try:
                annots = json.loads(t.annotations)
                comments_count += sum(1 for a in annots if a.get("type") == "comment")
            except Exception:
                pass

    progress = int((completed / total * 100)) if total > 0 else 0

    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if project:
        if total > 0 and completed == total:
            project.status = "Completed"
            db.commit()
        elif completed > 0:
            project.status = "In Progress"
            db.commit()

    return {
        "total": total,
        "completed": completed,
        "progress": progress,
        "comments": comments_count,
    }


@router.get("/metrics/batch")
def get_projects_metrics_batch(
    creator: Optional[str] = Query(None), db: Session = Depends(get_db)
):
    if creator:
        projects = (
            db.query(models.Project)
            .filter(func.lower(models.Project.creator) == func.lower(creator))
            .all()
        )
    else:
        projects = db.query(models.Project).all()

    project_ids = [p.id for p in projects]
    if not project_ids:
        return {}

    tasks = (
        db.query(models.Task.project_id, models.Task.status, models.Task.annotations)
        .filter(models.Task.project_id.in_(project_ids))
        .all()
    )

    metrics = {
        pid: {"total": 0, "completed": 0, "comments": 0, "progress": 0}
        for pid in project_ids
    }
    import json

    for t in tasks:
        metrics[t.project_id]["total"] += 1
        if t.status == "Completed":
            metrics[t.project_id]["completed"] += 1

        if t.annotations and '"comment"' in t.annotations:
            try:
                annots = json.loads(t.annotations)
                metrics[t.project_id]["comments"] += sum(
                    1 for a in annots if a.get("type") == "comment"
                )
            except Exception:
                pass

    for pid in project_ids:
        total = metrics[pid]["total"]
        completed = metrics[pid]["completed"]
        metrics[pid]["progress"] = int((completed / total * 100)) if total > 0 else 0

    return metrics


import schemas


@router.post("")
def create_project(project: ProjectModel, db: Session = Depends(get_db)):
    db_project = models.Project(
        name=project.name,
        slug=project.slug,
        type=project.type,
        status="Preparing",
        creator=project.creator,
        assignee=project.assignee,
    )
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    return {"id": db_project.id, "status": "ok"}


@router.post("/update")
def update_project(
    project_update: schemas.ProjectUpdate, db: Session = Depends(get_db)
):
    db_project = (
        db.query(models.Project).filter(models.Project.id == project_update.id).first()
    )
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
    db_project = (
        db.query(models.Project).filter(models.Project.id == project_id).first()
    )
    if db_project:
        db.query(models.Task).filter(models.Task.project_id == project_id).delete()
        db.delete(db_project)
        db.commit()
        return {"status": "ok"}
    raise HTTPException(status_code=404, detail="Project not found")


from config import DATA_DIR


@router.post("/{project_id}/upload")
def upload_files(
    project_id: int,
    assignee: Optional[str] = Query(None),
    file: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    uploads_dir = os.path.join(DATA_DIR, "uploads")
    os.makedirs(uploads_dir, exist_ok=True)
    saved_files = []

    ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

    for f in file:
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(
                status_code=400, detail=f"File type {ext} is not allowed."
            )

        new_filename = f"{uuid.uuid4().hex}{ext}"
        filepath = os.path.join(uploads_dir, new_filename)

        # Save relative to uploads dir for DB
        db_filepath = os.path.join("uploads", new_filename)

        with open(filepath, "wb") as out_file:
            out_file.write(f.file.read())

        task = models.Task(
            project_id=project_id,
            image_path=db_filepath,
            description=f.filename,
            status="New",
            assignee=assignee,
        )
        db.add(task)
        saved_files.append(db_filepath)

    db.commit()
    return {"status": "ok", "files": saved_files}

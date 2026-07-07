import os
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, UploadFile, File, Query, HTTPException
from sqlalchemy.orm import Session

import models
from database import get_db
from schemas import ProjectModel

router = APIRouter(prefix="/api/projects", tags=["projects"])

@router.get("")
def get_projects(creator: Optional[str] = Query(None), db: Session = Depends(get_db)):
    if creator:
        projects = db.query(models.Project).filter(models.Project.creator == creator).all()
    else:
        projects = db.query(models.Project).all()
    return [{"id": p.id, "name": p.name, "slug": p.slug, "type": p.type, "status": p.status, "creator": p.creator, "created_at": p.created_at} for p in projects]

@router.get("/{project_id}/metrics")
def get_project_metrics(project_id: int, db: Session = Depends(get_db)):
    total = db.query(models.Task).filter(models.Task.project_id == project_id).count()
    completed = db.query(models.Task).filter(models.Task.project_id == project_id, models.Task.status == 'Completed').count()
    
    progress = int((completed / total * 100)) if total > 0 else 0
    
    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    if project:
        if total > 0 and completed == total:
            project.status = 'Completed'
            db.commit()
        elif completed > 0:
            project.status = 'In Progress'
            db.commit()

    return {"total": total, "completed": completed, "progress": progress}

@router.post("")
def create_project(project: ProjectModel, db: Session = Depends(get_db)):
    db_project = models.Project(name=project.name, slug=project.slug, type=project.type, status="Preparing", creator=project.creator)
    db.add(db_project)
    db.commit()
    db.refresh(db_project)
    return {"id": db_project.id, "status": "ok"}

@router.post("/{project_id}/upload")
def upload_files(project_id: int, assignee: Optional[str] = Query(None), file: List[UploadFile] = File(...), db: Session = Depends(get_db)):
    os.makedirs("uploads", exist_ok=True)
    saved_files = []
    
    ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
    
    for f in file:
        ext = os.path.splitext(f.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=400, detail=f"File type {ext} is not allowed.")
            
        new_filename = f"{uuid.uuid4().hex}{ext}"
        filepath = os.path.join("uploads", new_filename)
        
        with open(filepath, "wb") as out_file:
            out_file.write(f.file.read())
            
        task = models.Task(project_id=project_id, image_path=filepath, description=f.filename, status='New', assignee=assignee)
        db.add(task)
        saved_files.append(filepath)
        
    db.commit()
    return {"status": "ok", "files": saved_files}

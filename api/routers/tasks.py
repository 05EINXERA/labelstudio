import json
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.orm import Session

import models
from database import get_db
from schemas import TaskUpdate, BulkDelete, BulkUpdate

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

@router.get("")
def get_tasks(projectId: Optional[int] = Query(None), db: Session = Depends(get_db)):
    if projectId:
        tasks = db.query(models.Task).filter(models.Task.project_id == projectId).all()
    else:
        tasks = db.query(models.Task).all()
    
    result = []
    for t in tasks:
        annotations_data = []
        if t.annotations:
            try:
                annotations_data = json.loads(t.annotations)
            except:
                pass
        result.append({
            "id": t.id, "description": t.description, "assignee": t.assignee, 
            "image_path": t.image_path, "status": t.status, "time_spent": t.time_spent, 
            "updated_at": t.updated_at, "annotations": annotations_data
        })
    return result

@router.post("")
def update_or_create_task(task: TaskUpdate, projectId: Optional[int] = Query(None), db: Session = Depends(get_db)):
    if task.id:
        db_task = db.query(models.Task).filter(models.Task.id == task.id).first()
        if db_task:
            if task.updated_at and db_task.updated_at:
                try:
                    client_updated = datetime.datetime.fromisoformat(task.updated_at.replace('Z', '+00:00')).replace(tzinfo=None)
                    if (db_task.updated_at - client_updated).total_seconds() > 1.0:
                        raise HTTPException(status_code=409, detail="Task was updated by another user. Please refresh to see latest annotations.")
                except ValueError:
                    pass
            if task.assignee is not None:
                db_task.assignee = task.assignee
            if task.status is not None:
                db_task.status = task.status
            if task.description is not None:
                db_task.description = task.description
            if task.time_spent_delta is not None:
                db_task.time_spent = (db_task.time_spent or 0) + task.time_spent_delta
            if task.annotations is not None:
                db_task.annotations = task.annotations
            db_task.updated_at = datetime.datetime.utcnow()
            task_id = db_task.id
            new_updated_at = db_task.updated_at
        else:
            raise HTTPException(status_code=404, detail="Task not found")
    else:
        db_task = models.Task(
            description=task.description, 
            assignee=task.assignee, 
            project_id=projectId, 
            status=task.status or "New", 
            time_spent=task.time_spent_delta or 0, 
            annotations=task.annotations,
            updated_at=datetime.datetime.utcnow()
        )
        db.add(db_task)
        db.commit()
        db.refresh(db_task)
        task_id = db_task.id
        new_updated_at = db_task.updated_at
        
    db.commit()
    return {"id": task_id, "status": "ok", "updated_at": new_updated_at.isoformat()}

@router.delete("/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    db.query(models.Task).filter(models.Task.id == task_id).delete()
    db.commit()
    return {"status": "ok"}

@router.post("/bulk-delete")
def bulk_delete_tasks(payload: BulkDelete, db: Session = Depends(get_db)):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No ids provided")
    db.query(models.Task).filter(models.Task.id.in_(payload.ids)).delete(synchronize_session=False)
    db.commit()
    return {"status": "ok"}

@router.post("/bulk-update")
def bulk_update_tasks(payload: BulkUpdate, db: Session = Depends(get_db)):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No ids provided")
    
    update_data = {}
    if payload.assignee is not None:
        update_data[models.Task.assignee] = payload.assignee
    if payload.status is not None:
        update_data[models.Task.status] = payload.status
        
    if update_data:
        update_data[models.Task.updated_at] = datetime.datetime.utcnow()
        db.query(models.Task).filter(models.Task.id.in_(payload.ids)).update(update_data, synchronize_session=False)
        db.commit()
        
    return {"status": "ok"}

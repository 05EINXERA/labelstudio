import json
import logging
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import case, func
from sqlalchemy.orm import Session

import models
from database import get_db
from schemas import TaskUpdate, BulkDelete, BulkUpdate
from api.auth import get_current_user
from api.routers.projects import get_owned_project

router = APIRouter(
    prefix="/api/tasks",
    tags=["tasks"],
    dependencies=[Depends(get_current_user)],
)

logger = logging.getLogger(__name__)


def _owned_project_ids(user: models.User, db: Session):
    """Ids of every project owned by `user`."""
    return [
        pid for (pid,) in db.query(models.Project.id)
        .filter(models.Project.owner_id == user.id).all()
    ]


def _get_owned_task(task_id: int, user: models.User, db: Session) -> models.Task:
    """Return the task if it belongs to a project `user` owns, else 404."""
    task = (
        db.query(models.Task)
        .join(models.Project, models.Task.project_id == models.Project.id)
        .filter(models.Task.id == task_id, models.Project.owner_id == user.id)
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task

@router.get("")
def get_tasks(projectId: Optional[int] = Query(None), include_annotations: bool = Query(True), db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    if projectId:
        get_owned_project(projectId, user, db)
        query = db.query(models.Task).filter(models.Task.project_id == projectId)
    else:
        # No project given: return tasks across every project the caller owns,
        # never the whole table.
        query = db.query(models.Task).filter(
            models.Task.project_id.in_(_owned_project_ids(user, db))
        )

    if not include_annotations:
        query = query.with_entities(
            models.Task.id, models.Task.description, models.Task.assignee,
            models.Task.image_path, models.Task.status, models.Task.time_spent, models.Task.updated_at
        )
        tasks = query.all()
        return [{"id": t.id, "description": t.description, "assignee": t.assignee, 
                 "image_path": t.image_path, "status": t.status, "time_spent": t.time_spent, 
                 "updated_at": t.updated_at, "annotations": []} for t in tasks]

    tasks = query.all()
    result = []
    for t in tasks:
        annotations_data = []
        if t.annotations:
            try:
                annotations_data = json.loads(t.annotations)
            except (ValueError, TypeError) as exc:
                logger.warning("Task %s has unparseable annotations: %s", t.id, exc)
        result.append({
            "id": t.id, "description": t.description, "assignee": t.assignee, 
            "image_path": t.image_path, "status": t.status, "time_spent": t.time_spent, 
            "updated_at": t.updated_at, "annotations": annotations_data
        })
    return result

@router.post("")
def update_or_create_task(task: TaskUpdate, projectId: Optional[int] = Query(None), db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    if task.id:
        db_task = _get_owned_task(task.id, user, db)
        if task.updated_at and db_task.updated_at:
            try:
                client_updated = datetime.datetime.fromisoformat(task.updated_at.replace('Z', '+00:00'))
            except ValueError:
                # Silently skipping the check here disabled conflict
                # detection entirely on a malformed timestamp, which then
                # caused clients to drop time deltas. See TIMER_AUDIT.md F10.
                raise HTTPException(status_code=422, detail="Invalid 'updated_at' timestamp format.")

            # Rows written before the tz-aware migration are naive UTC.
            stored = db_task.updated_at
            if stored.tzinfo is None:
                stored = stored.replace(tzinfo=datetime.timezone.utc)
            if client_updated.tzinfo is None:
                client_updated = client_updated.replace(tzinfo=datetime.timezone.utc)

            if (stored - client_updated).total_seconds() > 1.0:
                raise HTTPException(status_code=409, detail="Task was updated by another user. Please refresh to see latest annotations.")
        if task.assignee is not None:
            db_task.assignee = task.assignee
        if task.status is not None:
            # 'Approved' is a review gate the project owner sets. Every
            # project is single-owner (see REFACTOR_MANAGEMENT.md Q1), and
            # _get_owned_task above already proved `user` owns this task's
            # project, so no separate check is needed here today. If projects
            # ever gain shared members, this is the line that needs one.
            db_task.status = task.status
        if task.description is not None:
            db_task.description = task.description
        if task.time_spent_delta is not None:
            db_task.time_spent = (db_task.time_spent or 0) + task.time_spent_delta
        if task.annotations is not None:
            db_task.annotations = task.annotations
        db_task.updated_at = datetime.datetime.now(datetime.timezone.utc)
        task_id = db_task.id
        new_updated_at = db_task.updated_at
    else:
        if projectId is None:
            raise HTTPException(status_code=422, detail="Query param 'projectId' is required to create a task.")
        get_owned_project(projectId, user, db)
        db_task = models.Task(
            description=task.description,
            assignee=task.assignee, 
            project_id=projectId, 
            status=task.status or "New", 
            time_spent=task.time_spent_delta or 0, 
            annotations=task.annotations,
            updated_at=datetime.datetime.now(datetime.timezone.utc)
        )
        db.add(db_task)
        db.commit()
        db.refresh(db_task)
        task_id = db_task.id
        new_updated_at = db_task.updated_at
        
    # Project status is derived from its tasks. It used to be written by the
    # GET /metrics endpoint; deriving it here keeps that read side-effect free
    # (CLAUDE.md rule 4 / docs/TIMER_AUDIT.md F13).
    project_id = db_task.project_id
    if project_id is not None:
        # Push the pending task change to the DB so the aggregate below counts
        # it; without this the project never reaches 'Completed' on the update
        # that completes its last task.
        db.flush()
        counts = db.query(
            func.count(models.Task.id),
            func.sum(case((models.Task.status == 'Completed', 1), else_=0)),
        ).filter(models.Task.project_id == project_id).one()
        total, completed = counts[0] or 0, counts[1] or 0

        new_status = None
        if total > 0 and completed == total:
            new_status = 'Completed'
        elif completed > 0:
            new_status = 'In Progress'

        if new_status:
            project = db.query(models.Project).filter(models.Project.id == project_id).first()
            if project and project.status != new_status:
                project.status = new_status

    db.commit()
    return {"id": task_id, "status": "ok", "updated_at": new_updated_at.isoformat()}

@router.patch("/{task_id}")
def patch_task(task_id: int, task: TaskUpdate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """CLAUDE.md rule 5 shape for POST /api/tasks's update branch.

    Delegates to update_or_create_task rather than duplicating the timer /
    optimistic-concurrency / status-derivation logic (docs/TIMER_AUDIT.md
    F10/F13) a second time.
    """
    task.id = task_id
    return update_or_create_task(task, projectId=None, db=db, user=user)

@router.delete("/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    task = _get_owned_task(task_id, user, db)
    db.delete(task)
    db.commit()
    return {"status": "ok"}


def _restrict_to_owned(ids, user: models.User, db: Session):
    """Subset of `ids` the caller owns, and how many were rejected.

    Bulk routes accept arbitrary ids, so filtering (rather than a single guard)
    is what stops a caller from mutating another owner's tasks by mixing ids
    into the payload.
    """
    owned = [
        tid for (tid,) in db.query(models.Task.id)
        .join(models.Project, models.Task.project_id == models.Project.id)
        .filter(models.Task.id.in_(ids), models.Project.owner_id == user.id)
        .all()
    ]
    return owned, len(set(ids)) - len(owned)

@router.post("/bulk-delete")
def bulk_delete_tasks(payload: BulkDelete, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No ids provided")
    owned, skipped = _restrict_to_owned(payload.ids, user, db)
    if owned:
        db.query(models.Task).filter(models.Task.id.in_(owned)).delete(synchronize_session=False)
        db.commit()
    return {"status": "ok", "deleted": len(owned), "skipped": skipped}

@router.post("/bulk-update")
def bulk_update_tasks(payload: BulkUpdate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No ids provided")

    owned, skipped = _restrict_to_owned(payload.ids, user, db)

    update_data = {}
    if payload.assignee is not None:
        update_data[models.Task.assignee] = payload.assignee
    if payload.status is not None:
        update_data[models.Task.status] = payload.status

    if update_data and owned:
        update_data[models.Task.updated_at] = datetime.datetime.now(datetime.timezone.utc)
        db.query(models.Task).filter(models.Task.id.in_(owned)).update(update_data, synchronize_session=False)
        db.commit()

    return {"status": "ok", "updated": len(owned) if update_data else 0, "skipped": skipped}

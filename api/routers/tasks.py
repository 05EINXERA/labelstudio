import json
import logging
import os
import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import case, func
from sqlalchemy.orm import Session

import models
from database import get_db, commit_with_retry
from schemas import TaskUpdate, BulkDelete, BulkUpdate
from api.auth import get_current_user, require_csrf
from api.routers.projects import get_owned_project

router = APIRouter(
    prefix="/api/tasks",
    tags=["tasks"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)

logger = logging.getLogger(__name__)

# How far the stored timestamp may run ahead of the client's before a write
# from a *different* client is treated as a conflict.
#
# The old 1.0s was tuned on a localhost round-trip. Over the LAN, with SQLite
# write contention from ~20-30 annotators, a legitimate save can easily be
# further behind than that, which turned ordinary latency into a 409. Widened
# to five seconds: still far shorter than a human edit cycle, so a genuine
# two-person collision is caught, but no longer fires on network jitter.
CONFLICT_TOLERANCE_SECONDS = float(
    os.environ.get("TASK_CONFLICT_TOLERANCE_SECONDS", "5.0")
)


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
        # Conflict detection guards one thing: a client overwriting a write it
        # never saw. It deliberately does *not* fire when a client overwrites
        # its own earlier save — one browser tab writes the same task from
        # three places (debounced autosave, the visibilitychange beacon, and
        # the 30s timer drain), and the beacon path can never learn the
        # timestamp it produced. Treating that as a conflict is what silently
        # discarded annotations on the LAN deployment.
        # See .devnotes/deployment-hardening/04_ANNOTATION_SAVE_LOSS.md.
        #
        # `last_client_id` answers "who wrote last" exactly, so it is the
        # primary signal. The timestamp is only consulted when identity is
        # unavailable (an older client, or a row predating the column), where
        # it remains the best available approximation.
        if task.updated_at:
            # Parsed even when unused, so a malformed value is still a 422
            # rather than silently disabling the check (TIMER_AUDIT.md F10).
            try:
                client_updated = datetime.datetime.fromisoformat(task.updated_at.replace('Z', '+00:00'))
            except ValueError:
                raise HTTPException(status_code=422, detail="Invalid 'updated_at' timestamp format.")
            if client_updated.tzinfo is None:
                client_updated = client_updated.replace(tzinfo=datetime.timezone.utc)

            if task.client_id and db_task.last_client_id:
                # Both sides identified: a different last writer is a genuine
                # conflict regardless of how recently it happened.
                if task.client_id != db_task.last_client_id:
                    raise HTTPException(
                        status_code=409,
                        detail="Task was updated by another user. Please refresh to see latest annotations.",
                    )
            elif db_task.updated_at:
                # No identity to compare — fall back to the timestamp.
                stored = db_task.updated_at
                if stored.tzinfo is None:
                    stored = stored.replace(tzinfo=datetime.timezone.utc)
                if (stored - client_updated).total_seconds() > CONFLICT_TOLERANCE_SECONDS:
                    raise HTTPException(
                        status_code=409,
                        detail="Task was updated by another user. Please refresh to see latest annotations.",
                    )
        if task.client_id is not None:
            db_task.last_client_id = task.client_id
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
            updated_at=datetime.datetime.now(datetime.timezone.utc),
            last_client_id=task.client_id,
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

    commit_with_retry(db)
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
        commit_with_retry(db)
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
        commit_with_retry(db)

    return {"status": "ok", "updated": len(owned) if update_data else 0, "skipped": skipped}

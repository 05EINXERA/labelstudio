import json
import logging
import os
import datetime
import uuid
from typing import Dict, Optional, List

from fastapi import APIRouter, Depends, Query, HTTPException, Header
from sqlalchemy import case, func, or_, distinct
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.exc import IntegrityError
import time

import models
from database import get_db, commit_with_retry, SessionLocal
from schemas import TaskUpdate, BulkDelete, BulkUpdate, TaskDetail, PaginatedTasks, TaskSequenceItem
from api.auth import get_current_user, require_csrf, get_current_annotator
from api.routers.projects import get_owned_project, get_user_accessible_team_ids, is_project_creator

router = APIRouter(
    prefix="/api/tasks",
    tags=["tasks"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Soft task lock (T2.1 / D3)
#
# Database-backed lock in `task_locks` table keyed by task_id → (client_id, claimed_at).
# Safe across multiple Uvicorn workers and process restarts.
# TTL = 60 s — a claim not refreshed within that window is stale and any other
# annotator may take it.
# ---------------------------------------------------------------------------
TASK_LOCK_TTL_SECONDS = int(os.environ.get("TASK_LOCK_TTL_SECONDS", "60"))


def _sweep_stale_locks(db: Optional[Session] = None, ttl_seconds: int = TASK_LOCK_TTL_SECONDS) -> int:
    """Proactively evict expired task locks to prevent table growth over long uptimes."""
    close_on_exit = False
    if db is None:
        db = SessionLocal()
        close_on_exit = True
    try:
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=ttl_seconds)
        deleted = db.query(models.TaskLock).filter(models.TaskLock.claimed_at < cutoff).delete(synchronize_session=False)
        if deleted:
            commit_with_retry(db)
        return deleted
    finally:
        if close_on_exit:
            db.close()


def _lock_status(task_id: int, db: Session) -> Optional[models.TaskLock]:
    """Return the active TaskLock for task_id, or None if absent/stale."""
    lock = db.query(models.TaskLock).filter(models.TaskLock.task_id == task_id).first()
    if not lock:
        return None
    claimed_at = lock.claimed_at
    if claimed_at.tzinfo is None:
        claimed_at = claimed_at.replace(tzinfo=datetime.timezone.utc)
    age = (datetime.datetime.now(datetime.timezone.utc) - claimed_at).total_seconds()
    if age > TASK_LOCK_TTL_SECONDS:
        db.delete(lock)
        commit_with_retry(db)
        return None
    return lock

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


def _accessible_project_ids(user: models.User, db: Session, annotator: Optional[models.TeamMember] = None):
    """Ids of every project accessible to `user` (owned, created, via team, or assigned task)."""
    team_ids = get_user_accessible_team_ids(user, db, annotator)
    names = {user.username}
    if annotator and annotator.name:
        names.add(annotator.name)
    conditions = [
        models.Project.owner_id == user.id,
        models.Project.creator.in_(names),
    ]
    if team_ids:
        conditions.append(models.Project.team_id.in_(team_ids))

    task_pids = [
        t[0] for t in db.query(models.Task.project_id).filter(
            models.Task.assignee.in_(names)
        ).distinct().all()
    ]
    if task_pids:
        conditions.append(models.Project.id.in_(task_pids))

    return [
        pid for (pid,) in db.query(models.Project.id).filter(or_(*conditions)).all()
    ]


def _creator_project_ids(user: models.User, db: Session, annotator: Optional[models.TeamMember] = None):
    """Ids of every project created or owned by the caller."""
    names = set()
    if annotator and annotator.name:
        names.add(annotator.name)
    else:
        names.add(user.username)

    conditions = [
        models.Project.creator.in_(names),
    ]
    if not annotator or (annotator and annotator.name == user.username):
        conditions.append(models.Project.owner_id == user.id)

    return [
        pid for (pid,) in db.query(models.Project.id).filter(or_(*conditions)).all()
    ]


def _get_owned_task(task_id: int, user: models.User, db: Session, annotator: Optional[models.TeamMember] = None) -> models.Task:
    """Return the task if it belongs to a project `user` can access, else 404."""
    proj_ids = _accessible_project_ids(user, db, annotator)
    task = (
        db.query(models.Task)
        .filter(models.Task.id == task_id, models.Task.project_id.in_(proj_ids))
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
        
    return task

@router.get("", response_model=PaginatedTasks)
def get_tasks(
    projectId: Optional[int] = Query(None),
    limit: int = Query(50),
    offset: int = Query(0),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    assignee: Optional[str] = Query(None),
    sort_by: Optional[str] = Query("updated_at"),
    sort_desc: bool = Query(True),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    annotator: Optional[models.TeamMember] = Depends(get_current_annotator)
):
    if projectId:
        get_owned_project(projectId, user, db, annotator)
        query = db.query(models.Task).filter(models.Task.project_id == projectId)
    else:
        # No project given: return tasks across every project the caller can access
        query = db.query(models.Task).filter(
            models.Task.project_id.in_(_accessible_project_ids(user, db, annotator))
        )

    if search:
        query = query.filter(
            or_(
                models.Task.description.ilike(f"%{search}%"),
                models.Task.assignee.ilike(f"%{search}%")
            )
        )
    if status and status.lower() != "all":
        query = query.filter(models.Task.status == status)
    if assignee:
        query = query.filter(models.Task.assignee == assignee)

    total = query.count()

    if sort_by:
        sort_col = getattr(models.Task, sort_by, models.Task.updated_at)
        if sort_desc:
            sort_col = sort_col.desc()
        query = query.order_by(sort_col, models.Task.id.desc())
    else:
        query = query.order_by(models.Task.id.desc())

    query = query.with_entities(
        models.Task.id, models.Task.description, models.Task.assignee,
        models.Task.image_path, models.Task.status, models.Task.time_spent, models.Task.updated_at
    )
    
    tasks = query.offset(offset).limit(limit).all()
    task_ids = [t.id for t in tasks]
    
    comment_counts = dict(
        db.query(models.Annotation.task_id, func.count(models.Annotation.id))
        .filter(models.Annotation.task_id.in_(task_ids), models.Annotation.type == "comment")
        .group_by(models.Annotation.task_id)
        .all()
    ) if task_ids else {}
    
    class_counts = dict(
        db.query(models.Annotation.task_id, func.count(distinct(models.Annotation.label_id)))
        .filter(models.Annotation.task_id.in_(task_ids), models.Annotation.label_id.isnot(None))
        .group_by(models.Annotation.task_id)
        .all()
    ) if task_ids else {}
    
    items = []
    for t in tasks:
        items.append({
             "id": t.id, "description": t.description, "assignee": t.assignee, 
             "image_path": t.image_path, "status": t.status, "time_spent": t.time_spent, 
             "updated_at": t.updated_at, "annotations": [],
             "comment_count": comment_counts.get(t.id, 0),
             "class_count": class_counts.get(t.id, 0)
        })
             
    return {"items": items, "total": total, "limit": limit, "offset": offset}

@router.get("/sequence/{projectId}", response_model=List[TaskSequenceItem])
def get_task_sequence(projectId: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    get_owned_project(projectId, user, db, annotator)
    tasks = db.query(models.Task).filter(models.Task.project_id == projectId).order_by(models.Task.id).with_entities(models.Task.id, models.Task.description, models.Task.image_path).all()
    return [{"id": t.id, "description": t.description, "image_path": t.image_path} for t in tasks]

@router.get("/label-usage/{projectId}")
def get_label_usage(projectId: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    get_owned_project(projectId, user, db, annotator)
    # Count annotations grouped by label_id for the given project.
    rows = db.query(
        models.Annotation.label_id,
        func.count(models.Annotation.id)
    ).join(models.Task, models.Task.id == models.Annotation.task_id).filter(
        models.Task.project_id == projectId,
        models.Annotation.label_id.isnot(None)
    ).group_by(models.Annotation.label_id).all()
    
    return {row[0]: row[1] for row in rows}

@router.get("/{task_id}", response_model=TaskDetail)
def get_task(task_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    """Return a single task with its full annotations blob.

    The list endpoint (GET /api/tasks) intentionally omits annotations so the
    initial gallery load stays small. The workspace calls this endpoint once per
    task open to hydrate annotations on demand (T1.1 / T1.3).
    """
    # _get_owned_task does not eager-load annotations, but since it's one task, lazy load is fine.
    # Pydantic TaskDetail response_model will handle serialization of `task.annotations`.
    task = _get_owned_task(task_id, user, db, annotator)
    return TaskDetail(
        id=task.id,
        description=task.description,
        assignee=task.assignee,
        image_path=task.image_path,
        status=task.status,
        time_spent=task.time_spent,
        updated_at=task.updated_at,
        annotations=task.annotations,
    )


# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Soft lock endpoints (T2.1 / D3)
# ---------------------------------------------------------------------------

@router.post("/{task_id}/claim")
def claim_task(task_id: int, client_id: str = Query(..., max_length=64),
               db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    """Claim a task for editing, or refresh an existing claim.

    Returns {"status": "ok"} when the claim is granted.
    Returns HTTP 200 with {"status": "locked", "locked_by": ...} when the task
    is held by a *different* client that is still within its TTL.

    The lock is advisory — the save path does not enforce it. Its purpose is
    to warn a second annotator before they invest time on a task someone else
    is already editing, not to block them outright.
    """
    _get_owned_task(task_id, user, db, annotator)      # 404 if not owned/accessible
    for attempt in range(2):
        try:
            _sweep_stale_locks(db)
            now = datetime.datetime.now(datetime.timezone.utc)
            existing = _lock_status(task_id, db)
            if existing and existing.client_id != client_id:
                claimed_at = existing.claimed_at
                if claimed_at.tzinfo is None:
                    claimed_at = claimed_at.replace(tzinfo=datetime.timezone.utc)
                age = (now - claimed_at).total_seconds()
                return {"status": "locked",
                        "locked_by": existing.client_id,
                        "seconds_remaining": max(0, TASK_LOCK_TTL_SECONDS - int(age))}
            if existing:
                existing.claimed_at = now
            else:
                new_lock = models.TaskLock(task_id=task_id, client_id=client_id, claimed_at=now)
                db.add(new_lock)
            commit_with_retry(db)
            return {"status": "ok", "ttl": TASK_LOCK_TTL_SECONDS}
        except (IntegrityError, StaleDataError):
            db.rollback()
            if attempt == 1:
                return {"status": "locked", "locked_by": "unknown", "seconds_remaining": TASK_LOCK_TTL_SECONDS}


@router.post("/{task_id}/heartbeat")
def heartbeat_task(task_id: int, client_id: str = Query(..., max_length=64),
                   db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    """Refresh the TTL on an existing claim.

    Called on a timer cadence (every ~30 s) while the task is open. Silently
    re-claims if the lock expired (e.g. the client was backgrounded longer
    than the TTL).
    """
    _get_owned_task(task_id, user, db, annotator)
    for attempt in range(2):
        try:
            existing = _lock_status(task_id, db)
            if existing and existing.client_id != client_id:
                # Another client took over the stale lock before this heartbeat arrived.
                return {"status": "lost"}
            now = datetime.datetime.now(datetime.timezone.utc)
            if existing:
                existing.claimed_at = now
            else:
                new_lock = models.TaskLock(task_id=task_id, client_id=client_id, claimed_at=now)
                db.add(new_lock)
            commit_with_retry(db)
            return {"status": "ok", "ttl": TASK_LOCK_TTL_SECONDS}
        except (IntegrityError, StaleDataError):
            db.rollback()
            if attempt == 1:
                # If it still fails, just return ok. Next heartbeat will try again.
                pass
    return {"status": "ok", "ttl": TASK_LOCK_TTL_SECONDS}


@router.delete("/{task_id}/claim")
def release_task(task_id: int, client_id: str = Query(..., max_length=64),
                 db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    """Release a claim when the annotator closes or switches away from the task."""
    _get_owned_task(task_id, user, db, annotator)
    deleted = db.query(models.TaskLock).filter(
        models.TaskLock.task_id == task_id,
        models.TaskLock.client_id == client_id,
    ).delete(synchronize_session=False)
    if deleted:
        commit_with_retry(db)
    return {"status": "ok"}


@router.post("/{task_id}/release-beacon")
def release_task_beacon(task_id: int, client_id: str = Query(..., max_length=64),
                        db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    """POST variant of release for sendBeacon (which cannot send DELETE).

    sendBeacon is used on pagehide/visibilitychange where a real fetch is not
    guaranteed to complete. The advisory lock will expire via TTL anyway, but
    an explicit release is cleaner UX for the waiting annotator.
    """
    _get_owned_task(task_id, user, db, annotator)
    deleted = db.query(models.TaskLock).filter(
        models.TaskLock.task_id == task_id,
        models.TaskLock.client_id == client_id,
    ).delete(synchronize_session=False)
    if deleted:
        commit_with_retry(db)
    return {"status": "ok"}


@router.get("/{task_id}/lock-status")
def get_lock_status(task_id: int,
                    db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    """Query the current lock state without claiming. Used by the task list."""
    _get_owned_task(task_id, user, db, annotator)
    lock = _lock_status(task_id, db)
    if not lock:
        return {"locked": False}
    now = datetime.datetime.now(datetime.timezone.utc)
    claimed_at = lock.claimed_at
    if claimed_at.tzinfo is None:
        claimed_at = claimed_at.replace(tzinfo=datetime.timezone.utc)
    age = (now - claimed_at).total_seconds()
    return {
        "locked": True,
        "locked_by": lock.client_id,
        "seconds_remaining": max(0, TASK_LOCK_TTL_SECONDS - int(age)),
    }


@router.post("")
def update_or_create_task(task: TaskUpdate, projectId: Optional[int] = Query(None), db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    for attempt in range(3):
        try:
            return _update_or_create_task_impl(task, projectId, db, user, annotator)
        except (IntegrityError, StaleDataError) as e:
            db.rollback()
            is_unique = isinstance(e, IntegrityError) and ("unique" in str(e).lower() or "duplicate" in str(e).lower())
            is_stale = isinstance(e, StaleDataError)
            if attempt == 2 or not (is_unique or is_stale):
                raise
            delay = 0.1 * (2 ** attempt)
            logger.warning("Concurrent insert/update race detected on task %s (attempt %d/3), retrying in %.2fs: %s", task.id, attempt + 1, delay, type(e).__name__)
            time.sleep(delay)

def _update_or_create_task_impl(task: TaskUpdate, projectId: Optional[int], db: Session, user: models.User, annotator: Optional[models.TeamMember]):
    if task.id:
        db_task = _get_owned_task(task.id, user, db, annotator)
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
                #
                # Exception: if the client's token already matches the stored
                # updated_at exactly, the client loaded a fresh copy of this
                # task (via GET /api/tasks/{id}) and has up-to-date state.
                # A client_id mismatch in that case means the previous session's
                # ID is still in last_client_id but this is the same browser —
                # the localStorage-based clientId persists across reloads, so
                # this guard should be rare but is included for safety.
                stored = db_task.updated_at
                if stored:
                    if stored.tzinfo is None:
                        stored = stored.replace(tzinfo=datetime.timezone.utc)
                    tokens_match = abs((stored - client_updated).total_seconds()) <= 0.001
                else:
                    tokens_match = False

                if task.client_id != db_task.last_client_id and not tokens_match:
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
            db_task.status = task.status
        if task.description is not None:
            db_task.description = task.description
        if task.time_spent_delta is not None:
            db_task.time_spent = (db_task.time_spent or 0) + task.time_spent_delta
        if task.annotations is not None:
            try:
                anns = json.loads(task.annotations)
                existing_map = {a.id: a for a in db_task.annotations}
                
                # Find IDs in the payload that are new to this task
                incoming_ids = {a.get('id') for a in anns if isinstance(a, dict) and a.get('id')}
                new_to_task = incoming_ids - set(existing_map.keys())
                
                # Check if any of these new IDs already exist in the database (e.g. copied from another task)
                conflicting_ids = set()
                if new_to_task:
                    conflicts = db.query(models.Annotation.id).filter(models.Annotation.id.in_(new_to_task)).all()
                    conflicting_ids = {row[0] for row in conflicts}
                
                # Remove annotations that are no longer in the payload
                to_remove = [a for a in db_task.annotations if a.id not in incoming_ids]
                for a in to_remove:
                    db_task.annotations.remove(a)
                    # explicitly delete to handle passive_deletes=True
                    db.delete(a)
                db.flush()
                
                seen_ids = set()
                for a in anns:
                    if not isinstance(a, dict): continue
                    ann_id = a.get('id')
                    
                    # Regenerate if: no ID, duplicate in payload, or belongs to another task
                    if not ann_id or ann_id in seen_ids or ann_id in conflicting_ids:
                        ann_id = str(uuid.uuid4())
                    seen_ids.add(ann_id)
                    
                    known_keys = {'id', 'type', 'labelId', 'points', 'x', 'y', 'width', 'height', 'text', 'color', 'order', 'groupId'}
                    extra_dict = {k: v for k, v in a.items() if k not in known_keys}
                    extra = json.dumps(extra_dict) if extra_dict else None
                    points = json.dumps(a['points']) if a.get('points') is not None else None
                    
                    if ann_id in existing_map:
                        existing = existing_map[ann_id]
                        existing.label_id = a.get('labelId')
                        existing.type = a.get('type', 'polygon')
                        existing.points = points
                        existing.x = a.get('x')
                        existing.y = a.get('y')
                        existing.width = a.get('width')
                        existing.height = a.get('height')
                        existing.text = a.get('text')
                        existing.color = a.get('color')
                        existing.order = a.get('order')
                        existing.group_id = a.get('groupId')
                        existing.extra = extra
                    else:
                        db_task.annotations.append(models.Annotation(
                            id=ann_id,
                            label_id=a.get('labelId'),
                            type=a.get('type', 'polygon'),
                            points=points,
                            x=a.get('x'), y=a.get('y'), width=a.get('width'), height=a.get('height'),
                            text=a.get('text'), color=a.get('color'), order=a.get('order'), group_id=a.get('groupId'),
                            extra=extra
                        ))
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid annotations JSON")
        db_task.updated_at = datetime.datetime.now(datetime.timezone.utc)
        task_id = db_task.id
        new_updated_at = db_task.updated_at
    else:
        if projectId is None:
            raise HTTPException(status_code=422, detail="Query param 'projectId' is required to create a task.")
        db_project = get_owned_project(projectId, user, db, annotator)
        if not is_project_creator(db_project, user, annotator):
            raise HTTPException(status_code=403, detail="Only the project creator can add tasks to this project.")
        db_task = models.Task(
            description=task.description,
            assignee=task.assignee, 
            project_id=projectId, 
            status=task.status or "New", 
            time_spent=task.time_spent_delta or 0, 
            updated_at=datetime.datetime.now(datetime.timezone.utc),
            last_client_id=task.client_id,
        )
        if task.annotations is not None:
            try:
                anns = json.loads(task.annotations)
                
                # Find IDs in the payload
                incoming_ids = {a.get('id') for a in anns if isinstance(a, dict) and a.get('id')}
                
                # Check if any of these IDs already exist in the database (e.g. copied from another task)
                conflicting_ids = set()
                if incoming_ids:
                    conflicts = db.query(models.Annotation.id).filter(models.Annotation.id.in_(incoming_ids)).all()
                    conflicting_ids = {row[0] for row in conflicts}
                
                new_annotations = []
                seen_ids = set()
                for a in anns:
                    if not isinstance(a, dict): continue
                    ann_id = a.get('id')
                    
                    # Regenerate if: no ID, duplicate in payload, or belongs to another task
                    if not ann_id or ann_id in seen_ids or ann_id in conflicting_ids:
                        ann_id = str(uuid.uuid4())
                    seen_ids.add(ann_id)
                    
                    known_keys = {'id', 'type', 'labelId', 'points', 'x', 'y', 'width', 'height', 'text', 'color', 'order', 'groupId'}
                    extra_dict = {k: v for k, v in a.items() if k not in known_keys}
                    extra = json.dumps(extra_dict) if extra_dict else None
                    points = json.dumps(a['points']) if a.get('points') is not None else None
                    
                    new_annotations.append(models.Annotation(
                        id=ann_id,
                        label_id=a.get('labelId'),
                        type=a.get('type', 'polygon'),
                        points=points,
                        x=a.get('x'), y=a.get('y'), width=a.get('width'), height=a.get('height'),
                        text=a.get('text'), color=a.get('color'), order=a.get('order'), group_id=a.get('groupId'),
                        extra=extra
                    ))
                db_task.annotations = new_annotations
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid annotations JSON")
        db.add(db_task)
        commit_with_retry(db)
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
def patch_task(task_id: int, task: TaskUpdate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    """CLAUDE.md rule 5 shape for POST /api/tasks's update branch.

    Delegates to update_or_create_task rather than duplicating the timer /
    optimistic-concurrency / status-derivation logic (docs/TIMER_AUDIT.md
    F10/F13) a second time.
    """
    db_task = _get_owned_task(task_id, user, db, annotator)
    project = db.query(models.Project).filter(models.Project.id == db_task.project_id).first()
    if not project or not is_project_creator(project, user, annotator):
        raise HTTPException(status_code=403, detail="Only the project creator can edit tasks.")
    task.id = task_id
    return update_or_create_task(task, projectId=None, db=db, user=user, annotator=annotator)

@router.delete("/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    task = _get_owned_task(task_id, user, db, annotator)
    project = db.query(models.Project).filter(models.Project.id == task.project_id).first()
    if not project or not is_project_creator(project, user, annotator):
        raise HTTPException(status_code=403, detail="Only the project creator can delete tasks.")
    db.delete(task)
    commit_with_retry(db)
    return {"status": "ok"}


def _restrict_to_creator(ids, user: models.User, db: Session, annotator: Optional[models.TeamMember] = None):
    """Subset of `ids` belonging to projects created by the caller, and how many were rejected.

    Bulk routes accept arbitrary ids, so filtering (rather than a single guard)
    is what stops a caller from mutating another owner's tasks by mixing ids
    into the payload.
    """
    proj_ids = _creator_project_ids(user, db, annotator)
    owned = [
        tid for (tid,) in db.query(models.Task.id)
        .filter(models.Task.id.in_(ids), models.Task.project_id.in_(proj_ids))
        .all()
    ]
    return owned, len(set(ids)) - len(owned)

@router.post("/bulk-delete")
def bulk_delete_tasks(payload: BulkDelete, db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No ids provided")
    owned, skipped = _restrict_to_creator(payload.ids, user, db, annotator)
    if owned:
        db.query(models.Task).filter(models.Task.id.in_(owned)).delete(synchronize_session=False)
        commit_with_retry(db)
    return {"status": "ok", "deleted": len(owned), "skipped": skipped}

@router.post("/bulk-update")
def bulk_update_tasks(payload: BulkUpdate, db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No ids provided")

    owned, skipped = _restrict_to_creator(payload.ids, user, db, annotator)

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

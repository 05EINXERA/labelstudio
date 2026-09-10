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
from sqlalchemy.exc import IntegrityError, OperationalError
import time

import models
from config import IS_SQLITE
from database import get_db, commit_with_retry, SessionLocal
from schemas import (
    TaskUpdate, BulkDelete, BulkUpdate, TaskDetail, PaginatedTasks, TaskSequenceItem,
    TaskMove, TaskMoveResult, TaskMoveSkip,
)
from api.auth import get_current_user, require_csrf, get_current_annotator
from api.notifications import notify_task_assigned, notify_task_status_changed
from api.routers.projects import get_owned_project, get_user_accessible_team_ids, is_project_creator

router = APIRouter(
    prefix="/api/tasks",
    tags=["tasks"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)

logger = logging.getLogger(__name__)

# Terminal statuses: reaching one restricts further status changes to the
# task's assignee and the project owner (see the status-lock block in
# save_task). Kept in sync with LOCKED_STATUSES in
# frontend/js/pages/project/tasks.js.
LOCKED_STATUSES = {
    "Completed", "Approved", "Verified", "Passed", "Reviewed", "Monitored",
}


def _known_label_ids(anns, db) -> set:
    """The subset of labelIds in `anns` that actually exist in `labels`.

    A tab that had a class selected when someone else deleted it keeps sending
    that labelId on every autosave. annotations.label_id is a FK with ON DELETE
    SET NULL, so the delete itself is handled — but writing the stale id back
    raises ForeignKeyViolation, which surfaced as a 500 that failed *every*
    subsequent save for that annotator until they reloaded (their real
    annotation edits were lost with it). Resolving the ids up front lets an
    unknown one degrade to NULL — exactly the state the cascade would have left
    it in — so the shapes and their geometry still save. The frontend already
    recovers label-less shapes into a synthetic class on load (see the
    "replace" branch in api/routers/labels.py).

    One query per save, not one per annotation: tasks here carry ~700 shapes.
    """
    ids = {
        a.get('labelId') for a in anns
        if isinstance(a, dict) and a.get('labelId')
    }
    if not ids:
        return set()
    rows = db.query(models.Label.id).filter(models.Label.id.in_(ids)).all()
    return {row[0] for row in rows}

# ---------------------------------------------------------------------------
# Soft task lock (T2.1 / D3)
#
# Database-backed lock in `task_locks` table keyed by task_id → (client_id, claimed_at).
# Safe across multiple Uvicorn workers and process restarts.
# TTL = 60 s — a claim not refreshed within that window is stale and any other
# annotator may take it.
# ---------------------------------------------------------------------------
TASK_LOCK_TTL_SECONDS = int(os.environ.get("TASK_LOCK_TTL_SECONDS", "60"))

# A save that would drop a task's annotation count from at least this many
# down toward zero is refused rather than applied. Set to 0 to disable the
# guard. Low enough to protect real work, high enough that clearing a handful
# of shapes by hand is unaffected.
ANNOTATION_WIPE_GUARD_THRESHOLD = int(os.environ.get("ANNOTATION_WIPE_GUARD_THRESHOLD", "25"))

# A save against a task at/above the threshold above is also refused if the
# new payload is BOTH under this fraction of the existing count AND under
# this absolute count — not just a fully empty payload. A tab that autosaves
# before its canvas finishes hydrating a large task sends a short-but-nonempty
# list, which the empty-payload check alone does not catch. Requiring both
# conditions (rather than fraction alone) keeps a deliberate bulk delete by an
# annotator who can see what they're doing — e.g. clearing 30 of a task's 40
# shapes in one action — from being refused; only a payload that is small in
# absolute terms, on a task that had far more, looks like a truncated load
# rather than an edit. See .devnotes/deployment-hardening/08_POOL_EXHAUSTION.md
# (task 248's second wipe, 2026-08-23).
ANNOTATION_WIPE_GUARD_MIN_FRACTION = float(os.environ.get("ANNOTATION_WIPE_GUARD_MIN_FRACTION", "0.5"))
ANNOTATION_WIPE_GUARD_ABS_FLOOR = int(os.environ.get("ANNOTATION_WIPE_GUARD_ABS_FLOOR", "5"))


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


def _is_task_editor(task: models.Task, user: models.User, db: Session, annotator: Optional[models.TeamMember] = None) -> bool:
    """True if the caller may edit `task`, as its assignee or as the project owner.

    The two have equal authority over a task: the assignee owns the work itself,
    the project owner owns everything in the project. Every rule that restricts a
    task write is keyed on this, so an unassigned task is editable by anyone with
    project access, and an assigned one only by its assignee or the owner.
    """
    if annotator and task.assignee and task.assignee == annotator.name:
        return True
    project = db.query(models.Project).filter(models.Project.id == task.project_id).first()
    return bool(project and is_project_creator(project, user, annotator))


def _get_owned_task(task_id: int, user: models.User, db: Session, annotator: Optional[models.TeamMember] = None, require_edit: bool = True, for_update: bool = False) -> models.Task:
    """Return the task if it belongs to a project `user` can access, else 404.

    `for_update` takes a row lock on the task, serialising concurrent writes to
    it. Callers that mutate the task or its annotations should pass it; pure
    readers must not, so a slow read never blocks a save.

    Why this exists: every autosave rewrites a task's whole annotation set, so
    two overlapping saves on the same task raced, lost, and retried — 600
    retries an hour on 2026-09-07, concentrated on the largest tasks, plus
    deadlocks from the two transactions touching annotation rows in opposite
    orders. Acquiring this one lock before any annotation write gives every
    writer the same lock order, which removes both. Contending saves now queue
    briefly instead of doing the whole expensive rewrite and throwing it away.
    See .devnotes/deployment-hardening/08_POOL_EXHAUSTION.md.

    No-op on SQLite (single writer already, and `with_for_update` is ignored),
    so development and the test suite behave unchanged.
    """
    proj_ids = _accessible_project_ids(user, db, annotator)
    query = db.query(models.Task).filter(
        models.Task.id == task_id, models.Task.project_id.in_(proj_ids)
    )
    if for_update and not IS_SQLITE:
        query = query.with_for_update(of=models.Task)
    task = query.first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if require_edit and annotator and task.assignee and task.assignee != annotator.name:
        if not _is_task_editor(task, user, db, annotator):
            raise HTTPException(status_code=403, detail="Task is assigned to another user")

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
    task = _get_owned_task(task_id, user, db, annotator, require_edit=False)
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
    # Both attempts failed, so the claim was NOT refreshed. Reporting "ok" here
    # told the client it held a lock it did not, and a second annotator saw the
    # task as free. "error" is distinct from "lost": nothing is known about who
    # holds it, and the next heartbeat should simply try again.
    logger.warning("Heartbeat for task %s could not refresh the claim", task_id)
    return {"status": "error", "ttl": TASK_LOCK_TTL_SECONDS}


@router.delete("/{task_id}/claim")
def release_task(task_id: int, client_id: str = Query(..., max_length=64),
                 db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    """Release a claim when the annotator closes or switches away from the task."""
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
    _get_owned_task(task_id, user, db, annotator, require_edit=False)
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
    for attempt in range(5):
        try:
            return _update_or_create_task_impl(task, projectId, db, user, annotator)
        except (IntegrityError, StaleDataError, OperationalError) as e:
            db.rollback()

            # Expunge Annotation objects from the identity map to prevent SAWarnings
            # and poisoned state on subsequent attempts during prolonged race conditions.
            for obj in list(db.identity_map.values()):
                if isinstance(obj, models.Annotation):
                    db.expunge(obj)

            is_unique = isinstance(e, IntegrityError) and ("unique" in str(e).lower() or "duplicate" in str(e).lower())
            is_stale = isinstance(e, StaleDataError)
            # A deadlock raised *mid*-transaction (during the annotation
            # rewrite) reaches here rather than commit_with_retry, which only
            # wraps the commit. Before this it escaped as an unhandled 500 —
            # four of them on 2026-09-07. The FOR UPDATE lock above should stop
            # deadlocks arising at all; this is the belt-and-braces path.
            is_deadlock = isinstance(e, OperationalError) and "deadlock detected" in str(e).lower()
            if not (is_unique or is_stale or is_deadlock):
                # A genuine fault (FK/NOT NULL violation, a real bug). Let it
                # surface as a 500 rather than masking it as contention.
                raise
            if attempt == 4:
                # Contention we could not ride out. A 503 tells the client this
                # is transient and safe to retry, so it keeps its local draft;
                # a 500 is indistinguishable from a real fault (CLAUDE.md r11).
                logger.warning(
                    "Task %s save gave up after 5 attempts under contention: %s",
                    task.id or "new", e, exc_info=True,
                )
                raise HTTPException(
                    status_code=503,
                    detail="The server is busy. Your work is safe — the save will be retried.",
                    headers={"Retry-After": "2"},
                )


            # Back off
            delay = 0.1 * (2 ** attempt)
            logger.info(f"Concurrent insert/update race detected on task {task.id or 'new'} (attempt {attempt+1}/5), retrying in {delay:.2f}s: {e.__class__.__name__}")
            time.sleep(delay)

def _update_or_create_task_impl(task: TaskUpdate, projectId: Optional[int], db: Session, user: models.User, annotator: Optional[models.TeamMember]):
    # What actually changed in this write, for the notifications emitted after
    # the commit. Reset per attempt: the retry loop re-enters this function, and
    # a stale value here would announce a change the successful attempt did not
    # make.
    status_changed_to: Optional[str] = None
    assigned_to: Optional[str] = None
    notify_project: Optional[models.Project] = None

    if task.id:
        # for_update: serialise concurrent saves of this task. Taken here,
        # before the annotation rewrite below, so every writer acquires the
        # same lock first and the overlapping-save race cannot start.
        db_task = _get_owned_task(task.id, user, db, annotator, for_update=True)

        # Status lock: once a task reaches a terminal status, only the people
        # responsible for it — its assignee and the project owner — may move it
        # out again (_is_task_editor). The lock exists to stop a *third* party
        # from reopening someone else's finished work, not to stop the annotator
        # who did the work from correcting it. Annotations are never locked at
        # all; only the status transition is restricted.
        #
        # A rejected status change is dropped from this write rather than
        # raising, so it never blocks the annotations/time bundled in the
        # same request: every autosave echoes the task's current status
        # alongside its real payload (see workspace.js syncToBackend), so a
        # 403 here would silently block ordinary annotation edits too.
        # Held in a local rather than written back onto `task`: the retry loop
        # re-runs this function with the same payload object, and mutating it
        # here made attempt 2+ operate on a request the client never sent.
        incoming_status = task.status
        if (
            db_task.status in LOCKED_STATUSES
            and incoming_status is not None
            and incoming_status != db_task.status
            and not _is_task_editor(db_task, user, db, annotator)
        ):
            incoming_status = None

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
                        detail={
                            "code": "conflict",
                            "message": "Task was updated by another user. Please refresh to see latest annotations.",
                        },
                    )
            elif db_task.updated_at:
                # No identity to compare — fall back to the timestamp.
                stored = db_task.updated_at
                if stored.tzinfo is None:
                    stored = stored.replace(tzinfo=datetime.timezone.utc)
                if (stored - client_updated).total_seconds() > CONFLICT_TOLERANCE_SECONDS:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "conflict",
                            "message": "Task was updated by another user. Please refresh to see latest annotations.",
                        },
                    )
        if task.client_id is not None:
            db_task.last_client_id = task.client_id
        # Captured before the assignment so the emit after the commit can tell a
        # real transition from an autosave echoing the value it already had —
        # every save resends the current status and assignee (workspace.js
        # syncToBackend), so comparing against the stored value is what stops
        # the bell firing on every 30s drain.
        if task.assignee is not None and task.assignee != db_task.assignee:
            assigned_to = task.assignee
        if incoming_status is not None and incoming_status != db_task.status:
            status_changed_to = incoming_status
        if task.assignee is not None:
            db_task.assignee = task.assignee
        if incoming_status is not None:
            db_task.status = incoming_status
        if task.description is not None:
            db_task.description = task.description
        if task.time_spent_delta is not None:
            db_task.time_spent = (db_task.time_spent or 0) + task.time_spent_delta
        if task.annotations is not None:
            try:
                anns = json.loads(task.annotations)
                existing_map = {a.id: a for a in db_task.annotations}

                # Refuse a save that would wipe a substantial task outright, or
                # drop most of it in one shot.
                #
                # An empty (or near-empty) payload against a task holding
                # thousands of shapes is not something an annotator does
                # deliberately — it is a tab that autosaved before its canvas
                # hydrated, or a client retrying after a failed load. Three
                # tasks lost 4,704 / 1,980 / 770 annotations this way before
                # this guard existed; a fourth (task 248, again) lost its
                # restored 1,980 to a non-empty-but-truncated payload that the
                # empty-only check didn't catch. See
                # .devnotes/deployment-hardening/08_POOL_EXHAUSTION.md.
                #
                # Deleting every shape one at a time still works (each save
                # carries the shrinking remainder close to 1:1), so this only
                # ever fires on a single save that guts most of the task at once.
                existing_count = len(existing_map)
                if (existing_count >= ANNOTATION_WIPE_GUARD_THRESHOLD
                        and len(anns) < existing_count * ANNOTATION_WIPE_GUARD_MIN_FRACTION
                        and len(anns) < ANNOTATION_WIPE_GUARD_ABS_FLOOR):
                    logger.warning(
                        "Refused an annotation payload for task %s that would drop "
                        "%d annotations to %d (client_id=%s)",
                        db_task.id, existing_count, len(anns), task.client_id,
                    )
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "wipe_guard",
                            "message": (
                                "This save would delete most of the annotations on the "
                                "task. It was refused to protect existing work. Reload "
                                "the task to get the current annotations before editing."
                            ),
                        },
                    )

                # Find IDs in the payload that are new to this task
                incoming_ids = {a.get('id') for a in anns if isinstance(a, dict) and a.get('id')}
                new_to_task = incoming_ids - set(existing_map.keys())
                
                # Check if any of these new IDs already exist in the database (e.g. copied from another task)
                conflicting_ids = set()
                if new_to_task:
                    conflicts = db.query(models.Annotation.id).filter(models.Annotation.id.in_(new_to_task)).all()
                    conflicting_ids = {row[0] for row in conflicts}
                
                valid_label_ids = _known_label_ids(anns, db)

                # Remove annotations that are no longer in the payload
                to_remove = [a for a in db_task.annotations if a.id not in incoming_ids]
                for a in to_remove:
                    db_task.annotations.remove(a)
                    # explicitly delete to handle passive_deletes=True
                    db.delete(a)
                db.flush()
                
                seen_ids = set()
                for position, a in enumerate(anns):
                    if not isinstance(a, dict): continue
                    ann_id = a.get('id')
                    
                    # Regenerate if: no ID, duplicate in payload, or belongs to another task
                    if not ann_id or ann_id in seen_ids or ann_id in conflicting_ids:
                        ann_id = str(uuid.uuid4())
                    seen_ids.add(ann_id)
                    
                    known_keys = {'id', 'type', 'labelId', 'points', 'x', 'y', 'width', 'height', 'text', 'color', 'order', 'groupId'}
                    extra_dict = {k: v for k, v in a.items() if k not in known_keys and k != 'extra'}
                    if 'extra' in a and isinstance(a['extra'], dict):
                        extra_dict.update(a['extra'])
                    extra = json.dumps(extra_dict) if extra_dict else None
                    points = json.dumps(a['points']) if a.get('points') is not None else None
                    
                    if ann_id in existing_map:
                        existing = existing_map[ann_id]
                        # Unknown label (deleted by someone else mid-session) ->
                        # NULL rather than a FK violation. See _known_label_ids.
                        existing.label_id = a.get('labelId') if a.get('labelId') in valid_label_ids else None
                        existing.type = a.get('type', 'polygon')
                        existing.points = points
                        existing.x = a.get('x')
                        existing.y = a.get('y')
                        existing.width = a.get('width')
                        existing.height = a.get('height')
                        existing.text = a.get('text')
                        existing.color = a.get('color')
                        # Canvas z-order is the payload's array position, not a
                        # field the client sends: the reorder actions rewrite the
                        # array itself. Persist the index so the order survives a
                        # reload (models.Task.annotations sorts on it).
                        #
                        # Only assign when it actually moved. Tasks here reach
                        # 7,000+ shapes, and the ORM emits one UPDATE per dirtied
                        # row: assigning unconditionally turned every autosave
                        # into thousands of single-row UPDATEs in one
                        # transaction, holding a pool connection long enough to
                        # exhaust the pool under ~25 concurrent annotators.
                        if existing.order != position:
                            existing.order = position
                        existing.group_id = a.get('groupId')
                        existing.extra = extra
                    else:
                        db_task.annotations.append(models.Annotation(
                            id=ann_id,
                            label_id=a.get('labelId') if a.get('labelId') in valid_label_ids else None,
                            type=a.get('type', 'polygon'),
                            points=points,
                            x=a.get('x'), y=a.get('y'), width=a.get('width'), height=a.get('height'),
                            text=a.get('text'), color=a.get('color'), order=position, group_id=a.get('groupId'),
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
        # A task created already assigned notifies its assignee, same as one
        # assigned later.
        if task.assignee:
            assigned_to = task.assignee
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
                
                valid_label_ids = _known_label_ids(anns, db)

                new_annotations = []
                seen_ids = set()
                for position, a in enumerate(anns):
                    if not isinstance(a, dict): continue
                    ann_id = a.get('id')
                    
                    # Regenerate if: no ID, duplicate in payload, or belongs to another task
                    if not ann_id or ann_id in seen_ids or ann_id in conflicting_ids:
                        ann_id = str(uuid.uuid4())
                    seen_ids.add(ann_id)
                    
                    known_keys = {'id', 'type', 'labelId', 'points', 'x', 'y', 'width', 'height', 'text', 'color', 'order', 'groupId'}
                    extra_dict = {k: v for k, v in a.items() if k not in known_keys and k != 'extra'}
                    if 'extra' in a and isinstance(a['extra'], dict):
                        extra_dict.update(a['extra'])
                    extra = json.dumps(extra_dict) if extra_dict else None
                    points = json.dumps(a['points']) if a.get('points') is not None else None
                    
                    new_annotations.append(models.Annotation(
                        id=ann_id,
                        label_id=a.get('labelId') if a.get('labelId') in valid_label_ids else None,
                        type=a.get('type', 'polygon'),
                        points=points,
                        x=a.get('x'), y=a.get('y'), width=a.get('width'), height=a.get('height'),
                        text=a.get('text'), color=a.get('color'), order=position, group_id=a.get('groupId'),
                        extra=extra
                    ))
                db_task.annotations = new_annotations
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid annotations JSON")
        db.add(db_task)
        # flush, not commit: the single commit at the end of this function is
        # what makes the whole create atomic. Committing here left the task row
        # durable while the retry loop re-ran the create from the top on a
        # later failure, inserting a duplicate.
        db.flush()
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

        # Loaded unconditionally (not only when the rollup changes) because the
        # post-commit notify needs the owner regardless of whether the project's
        # own status moved.
        project = db.query(models.Project).filter(models.Project.id == project_id).first()
        notify_project = project

        if new_status:
            if project and project.status != new_status:
                project.status = new_status

    commit_with_retry(db)

    # Notify AFTER the commit, never before: this whole function is re-run by
    # the retry loop in update_or_create_task on contention, so an emit placed
    # inside the transaction would fire for a write that later rolled back and
    # would fire again on each attempt. See api/notifications.py.
    if status_changed_to:
        notify_task_status_changed(
            db, notify_project, db_task, status_changed_to,
            actor_name=annotator.name if annotator else user.username,
        )
    if assigned_to:
        notify_task_assigned(
            db, db_task.id, db_task.description or f"Task {db_task.id}",
            assigned_to, actor_name=annotator.name if annotator else user.username,
        )

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
    # for_update: a delete racing an in-flight save of the same task would
    # otherwise interleave with that save's annotation rewrite.
    task = _get_owned_task(task_id, user, db, annotator, for_update=True)
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
        # Read the rows the assign notification needs before the UPDATE, so the
        # message can name each task and so tasks already assigned to this
        # person are skipped (a re-assign to the same name is not news).
        newly_assigned = []
        if payload.assignee:
            newly_assigned = [
                (tid, desc) for (tid, desc) in db.query(models.Task.id, models.Task.description)
                .filter(
                    models.Task.id.in_(owned),
                    or_(models.Task.assignee.is_(None), models.Task.assignee != payload.assignee),
                ).all()
            ]

        update_data[models.Task.updated_at] = datetime.datetime.now(datetime.timezone.utc)
        db.query(models.Task).filter(models.Task.id.in_(owned)).update(update_data, synchronize_session=False)
        commit_with_retry(db)

        # After the commit (see api/notifications.py): the assignment is durable
        # before anyone is told about it.
        actor = annotator.name if annotator else user.username
        for task_id, description in newly_assigned:
            notify_task_assigned(
                db, task_id, description or f"Task {task_id}", payload.assignee, actor_name=actor,
            )

    return {"status": "ok", "updated": len(owned) if update_data else 0, "skipped": skipped}


def _remap_labels_by_name(task_ids, target_project_id: int, db: Session):
    """Repoint the moved tasks' annotations at the destination project's classes.

    Labels are per-project rows (`labels.project_id`) but `annotations.label_id`
    is a plain FK to one of them, so a task that changes project keeps pointing
    at classes its new project does not own. The shapes survive — geometry lives
    on the annotation — but their class is invisible in the destination's
    Classes view, exports disagree, and deleting the *source* project NULLs
    every one of them (the FK is ON DELETE SET NULL; see delete_project).

    So the move reconciles by class name: for each distinct source label used by
    the moved annotations, find the destination label of the same name, creating
    it (name + color copied) when the destination has none. Name, not id,
    because two projects that both have a "Car" class hold two unrelated rows —
    matching on id would only ever match when the label already belonged to the
    destination.

    Annotations whose label_id is already NULL, or already point at a
    destination label, are left alone. Returns (labels_created, annotations_remapped).
    """
    source_label_ids = {
        lid for (lid,) in db.query(distinct(models.Annotation.label_id))
        .filter(
            models.Annotation.task_id.in_(task_ids),
            models.Annotation.label_id.isnot(None),
        ).all()
    }
    if not source_label_ids:
        return 0, 0

    source_labels = db.query(models.Label).filter(models.Label.id.in_(source_label_ids)).all()
    # Anything already owned by the destination needs no remapping.
    source_labels = [l for l in source_labels if l.project_id != target_project_id]
    if not source_labels:
        return 0, 0

    # Case-insensitive so "Car" and "car" reconcile to one class rather than
    # leaving the destination with a near-duplicate pair.
    dest_by_name = {
        (l.name or "").strip().lower(): l
        for l in db.query(models.Label).filter(models.Label.project_id == target_project_id).all()
    }

    created = 0
    id_map = {}
    for src in source_labels:
        key = (src.name or "").strip().lower()
        dest = dest_by_name.get(key)
        if dest is None:
            dest = models.Label(
                id=uuid.uuid4().hex,
                name=src.name,
                color=src.color,
                project_id=target_project_id,
            )
            db.add(dest)
            dest_by_name[key] = dest
            created += 1
        id_map[src.id] = dest.id

    if created:
        # The new rows must exist before annotations can reference them.
        db.flush()

    remapped = 0
    for src_id, dest_id in id_map.items():
        remapped += (
            db.query(models.Annotation)
            .filter(
                models.Annotation.task_id.in_(task_ids),
                models.Annotation.label_id == src_id,
            )
            .update({models.Annotation.label_id: dest_id}, synchronize_session=False)
        )

    return created, remapped


@router.post("/move", response_model=TaskMoveResult)
def move_tasks(
    payload: TaskMove,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    annotator: Optional[models.TeamMember] = Depends(get_current_annotator),
):
    """Move tasks between projects, preserving their annotations.

    The task row carries everything an annotator cares about — status,
    assignee, time_spent, image_path, image dimensions — and annotations hang
    off task_id, so changing project_id moves the geometry, z-order (`order`),
    groups and `extra` untouched. Uploaded images are not partitioned by
    project, so no file moves either. The one thing that does not travel
    cleanly is the class each annotation points at; see _remap_labels_by_name.

    The caller must own both ends: the source side is filtered per-task through
    _restrict_to_creator (so mixing another owner's ids into the payload moves
    nothing rather than failing the whole batch), and the destination through
    get_owned_project.
    """
    target = get_owned_project(payload.targetProjectId, user, db, annotator)

    requested = list(dict.fromkeys(payload.taskIds))
    owned, _ = _restrict_to_creator(requested, user, db, annotator)
    owned_set = set(owned)

    skipped = [
        TaskMoveSkip(taskId=tid, reason="not_owned")
        for tid in requested if tid not in owned_set
    ]

    tasks = db.query(models.Task).filter(models.Task.id.in_(owned)).all() if owned else []

    movable = []
    for task in tasks:
        if task.project_id == target.id:
            skipped.append(TaskMoveSkip(taskId=task.id, reason="already_in_target"))
            continue
        # A task someone has open is being edited right now; moving it out from
        # under them would drop it out of their gallery mid-edit, and their
        # localStorage draft would then save against a task in another project.
        # Refuse it and let the caller retry once the lock lapses (60s TTL).
        if _lock_status(task.id, db) is not None:
            skipped.append(TaskMoveSkip(taskId=task.id, reason="locked"))
            continue
        movable.append(task)

    if not movable:
        return TaskMoveResult(moved=0, labelsCreated=0, labelsRemapped=0, skipped=skipped)

    movable_ids = [t.id for t in movable]
    created, remapped = _remap_labels_by_name(movable_ids, target.id, db)

    now = datetime.datetime.now(datetime.timezone.utc)
    for task in movable:
        task.project_id = target.id
        task.updated_at = now

    commit_with_retry(db)

    logger.info(
        "Moved %d task(s) to project %d (labels created=%d, annotations remapped=%d)",
        len(movable_ids), target.id, created, remapped,
    )
    return TaskMoveResult(
        moved=len(movable_ids),
        labelsCreated=created,
        labelsRemapped=remapped,
        skipped=skipped,
    )

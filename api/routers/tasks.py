import json
import logging
import os
import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import case, func
from sqlalchemy.orm import Session

import models
from database import get_db, commit_with_retry
from schemas import (
    BulkAssign,
    BulkAssignResult,
    BulkDelete,
    BulkUpdate,
    ReviewCreate,
    ReviewOut,
    ReviewResult,
    TaskAssignment,
    TaskAssignmentResult,
    TaskDetail,
    TaskUpdate,
)
from api.auth import get_current_user, require_csrf
from api.permissions import (
    ProjectRole,
    accessible_project_ids,
    at_least,
    can_write_task,
    effective_project_role,
    require_project,
    require_task,
)

router = APIRouter(
    prefix="/api/tasks",
    tags=["tasks"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Soft task lock (T2.1)
#
# In-process dict keyed by task_id → {client_id, claimed_at}.
# Single-worker constraint applies (CLAUDE.md rule 9 / D3): do not use this
# across multiple workers.  TTL = 60 s — a claim not refreshed within that
# window is stale and any other annotator may take it.
# ---------------------------------------------------------------------------
TASK_LOCK_TTL_SECONDS = int(os.environ.get("TASK_LOCK_TTL_SECONDS", "60"))

# {task_id: {"client_id": str, "claimed_at": datetime}}
_TASK_LOCKS: Dict[int, dict] = {}


def _lock_status(task_id: int) -> Optional[dict]:
    """Return the active lock for task_id, or None if absent/stale."""
    lock = _TASK_LOCKS.get(task_id)
    if not lock:
        return None
    age = (datetime.datetime.now(datetime.timezone.utc) - lock["claimed_at"]).total_seconds()
    if age > TASK_LOCK_TTL_SECONDS:
        _TASK_LOCKS.pop(task_id, None)
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


# Statuses that represent a review decision. Moving a task *into* or *out of*
# either one requires the Reviewer role: un-approving is exactly as privileged
# as approving (.devnotes/teams/01_DESIGN.md § 4).
REVIEW_STATUSES = frozenset({"Approved", "Rejected"})


def _require_review_role_for_status(
    db_task: models.Task, new_status: Optional[str], user: models.User, db: Session
) -> None:
    """403 unless the caller may make this status transition.

    Only transitions that touch a review state are gated. An annotator moving a
    task New → In Progress → Completed is ordinary work and stays untouched;
    this is what keeps the shared-account deployment behaving identically.
    """
    if new_status is None or new_status == db_task.status:
        return
    if new_status not in REVIEW_STATUSES and db_task.status not in REVIEW_STATUSES:
        return

    role = effective_project_role(user, db_task.project_id, db)
    if at_least(role, ProjectRole.REVIEWER):
        return

    verb = "Approving" if new_status == "Approved" else (
        "Rejecting" if new_status == "Rejected" else "Changing a reviewed task"
    )
    raise HTTPException(
        status_code=403,
        detail=f"{verb} requires the Reviewer role on this project.",
    )


def _require_assigned_team_membership(
    db_task: models.Task, user: models.User, db: Session
) -> None:
    """403 when `restrict_to_assigned_team` blocks this writer (E-24).

    A no-op unless the project opted in *and* the task is assigned to a team,
    so every pre-teams task and every non-opted-in project is unaffected.
    """
    role = effective_project_role(user, db_task.project_id, db)
    if can_write_task(db_task, user, role, db):
        return

    team = (
        db.get(models.Team, db_task.assigned_team_id)
        if db_task.assigned_team_id
        else None
    )
    raise HTTPException(
        status_code=403,
        detail=(
            f"This task is assigned to {team.name}."
            if team
            else "You do not have permission to edit this task."
        ),
    )


def _owned_project_ids(user: models.User, db: Session):
    """DEPRECATED — use `accessible_project_ids`.

    Kept for one release so no call site is silently missed; Phase 5 (F5)
    deletes it.
    """
    return accessible_project_ids(user, db)


def _get_owned_task(task_id: int, user: models.User, db: Session) -> models.Task:
    """DEPRECATED — use `require_task` with an explicit minimum role.

    Kept for one release as a thin alias; Phase 5 (F5) deletes it. `manager` is
    the conservative stand-in for the old ownership check.
    """
    return require_task(task_id, user, db, minimum=ProjectRole.MANAGER)

@router.get("")
def get_tasks(projectId: Optional[int] = Query(None), include_annotations: bool = Query(True), db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    if projectId:
        require_project(projectId, user, db, minimum=ProjectRole.VIEWER)
        query = db.query(models.Task).filter(models.Task.project_id == projectId)
    else:
        # No project given: return tasks across every project the caller can
        # reach, never the whole table.
        query = db.query(models.Task).filter(
            models.Task.project_id.in_(accessible_project_ids(user, db))
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

@router.get("/{task_id}", response_model=TaskDetail)
def get_task(task_id: int, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Return a single task with its full annotations blob.

    The list endpoint (GET /api/tasks) intentionally omits annotations so the
    initial gallery load stays small. The workspace calls this endpoint once per
    task open to hydrate annotations on demand (T1.1 / T1.3).
    """
    task = require_task(task_id, user, db, minimum=ProjectRole.VIEWER)
    annotations_data: list = []
    if task.annotations:
        try:
            annotations_data = json.loads(task.annotations)
        except (ValueError, TypeError) as exc:
            logger.warning("Task %s has unparseable annotations: %s", task.id, exc)
    return TaskDetail(
        id=task.id,
        description=task.description,
        assignee=task.assignee,
        image_path=task.image_path,
        status=task.status,
        time_spent=task.time_spent,
        updated_at=task.updated_at,
        annotations=annotations_data,
    )


# ---------------------------------------------------------------------------
# Soft lock endpoints (T2.1)
# ---------------------------------------------------------------------------

@router.post("/{task_id}/claim")
def claim_task(task_id: int, client_id: str = Query(..., max_length=64),
               db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Claim a task for editing, or refresh an existing claim.

    Returns {"status": "ok"} when the claim is granted.
    Returns HTTP 409 with a "locked" status when the task is held by a
    *different* client that is still within its TTL.

    The lock is advisory — the save path does not enforce it.  Its purpose is
    to warn a second annotator before they invest time on a task someone else
    is already editing, not to block them outright.

    Single-worker constraint: _TASK_LOCKS is in-process.  Rule 9 applies.
    """
    # Annotator minimum: a viewer has nothing to lock, since they cannot write.
    require_task(task_id, user, db, minimum=ProjectRole.ANNOTATOR)
    now = datetime.datetime.now(datetime.timezone.utc)
    existing = _lock_status(task_id)
    if existing and existing["client_id"] != client_id:
        age = (now - existing["claimed_at"]).total_seconds()
        return {"status": "locked",
                "locked_by": existing["client_id"],
                "seconds_remaining": max(0, TASK_LOCK_TTL_SECONDS - int(age))}
    _TASK_LOCKS[task_id] = {"client_id": client_id, "claimed_at": now}
    return {"status": "ok", "ttl": TASK_LOCK_TTL_SECONDS}


@router.post("/{task_id}/heartbeat")
def heartbeat_task(task_id: int, client_id: str = Query(..., max_length=64),
                   db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Refresh the TTL on an existing claim.

    Called on a timer cadence (every ~30 s) while the task is open.  Silently
    re-claims if the lock expired (e.g. the client was backgrounded longer
    than the TTL).
    """
    require_task(task_id, user, db, minimum=ProjectRole.ANNOTATOR)
    existing = _lock_status(task_id)
    if existing and existing["client_id"] != client_id:
        # Another client took over the stale lock before this heartbeat arrived.
        return {"status": "lost"}
    _TASK_LOCKS[task_id] = {
        "client_id": client_id,
        "claimed_at": datetime.datetime.now(datetime.timezone.utc),
    }
    return {"status": "ok", "ttl": TASK_LOCK_TTL_SECONDS}


@router.delete("/{task_id}/claim")
def release_task(task_id: int, client_id: str = Query(..., max_length=64),
                 db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Release a claim when the annotator closes or switches away from the task."""
    require_task(task_id, user, db, minimum=ProjectRole.ANNOTATOR)
    existing = _TASK_LOCKS.get(task_id)
    if existing and existing["client_id"] == client_id:
        del _TASK_LOCKS[task_id]
    return {"status": "ok"}


@router.post("/{task_id}/release-beacon")
def release_task_beacon(task_id: int, client_id: str = Query(..., max_length=64),
                        db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """POST variant of release for sendBeacon (which cannot send DELETE).

    sendBeacon is used on pagehide/visibilitychange where a real fetch is not
    guaranteed to complete.  The advisory lock will expire via TTL anyway, but
    an explicit release is cleaner UX for the waiting annotator.
    """
    require_task(task_id, user, db, minimum=ProjectRole.ANNOTATOR)
    existing = _TASK_LOCKS.get(task_id)
    if existing and existing["client_id"] == client_id:
        del _TASK_LOCKS[task_id]
    return {"status": "ok"}


@router.get("/{task_id}/lock-status")
def get_lock_status(task_id: int,
                    db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Query the current lock state without claiming.  Used by the task list."""
    require_task(task_id, user, db, minimum=ProjectRole.ANNOTATOR)
    lock = _lock_status(task_id)
    if not lock:
        return {"locked": False}
    now = datetime.datetime.now(datetime.timezone.utc)
    age = (now - lock["claimed_at"]).total_seconds()
    return {
        "locked": True,
        "locked_by": lock["client_id"],
        "seconds_remaining": max(0, TASK_LOCK_TTL_SECONDS - int(age)),
    }


@router.post("")
def update_or_create_task(task: TaskUpdate, projectId: Optional[int] = Query(None), db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    if task.id:
        db_task = require_task(task.id, user, db, minimum=ProjectRole.ANNOTATOR)

        # --- Permission checks -------------------------------------------
        # These run *before* the conflict detection below, deliberately. A user
        # who may not write this task must get a 403 explaining why, never a
        # 409 telling them someone else edited it — that is a confusing,
        # unactionable message for a permission problem (03_API.md § 4.2).
        #
        # None of this touches the conflict logic itself. Rule 11 and
        # .devnotes/deployment-hardening/04_ANNOTATION_SAVE_LOSS.md apply: the
        # client_id / last_client_id model below is load-bearing and unchanged.
        _require_review_role_for_status(db_task, task.status, user, db)
        _require_assigned_team_membership(db_task, user, db)

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
            # The review gate for 'Approved'/'Rejected' is enforced above, in
            # _require_review_role_for_status, before conflict detection runs.
            # (This is the line the old comment predicted would need a check
            # "if projects ever gain shared members" — they now have them.)
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
        require_project(projectId, user, db, minimum=ProjectRole.MANAGER)
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
    task = require_task(task_id, user, db, minimum=ProjectRole.MANAGER)
    db.delete(task)
    commit_with_retry(db)
    return {"status": "ok"}


def _restrict_to_owned(ids, user: models.User, db: Session,
                       minimum: ProjectRole = ProjectRole.MANAGER):
    """Subset of `ids` the caller may act on at `minimum`, and how many were not.

    Bulk routes accept arbitrary ids, so filtering (rather than a single guard)
    is what stops a caller mutating tasks they cannot reach by mixing ids into
    the payload. Filtering rather than failing the whole batch is deliberate and
    is the shape every bulk endpoint here follows.

    The role is resolved per *project*, not per task: a batch usually spans one
    or two projects, so this is a couple of resolves rather than one per id.
    """
    rows = (
        db.query(models.Task.id, models.Task.project_id)
        .filter(models.Task.id.in_(ids))
        .all()
    )

    permitted_projects = {}
    allowed = []
    for task_id, project_id in rows:
        if project_id not in permitted_projects:
            role = effective_project_role(user, project_id, db)
            permitted_projects[project_id] = at_least(role, minimum)
        if permitted_projects[project_id]:
            allowed.append(task_id)

    return allowed, len(set(ids)) - len(allowed)

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

    # E-11: the reviewer gate applies here exactly as it does to PATCH.
    # Otherwise bulk-update is a one-request bypass for approval — set
    # status="Approved" on the ids you could not approve individually. This is
    # the easiest check in the whole feature to forget, which is why it has its
    # own named test.
    #
    # A review-status change *is* the reviewer's job, so REVIEWER is the
    # minimum for that case rather than an additional requirement on top of
    # MANAGER — a reviewer who cannot bulk-approve would have to click through
    # a thousand tasks one at a time. Setting any other field (or a
    # non-review status) remains an administrative bulk edit at MANAGER.
    if payload.status in REVIEW_STATUSES and payload.assignee is None:
        minimum = ProjectRole.REVIEWER
    else:
        minimum = ProjectRole.MANAGER

    owned, skipped = _restrict_to_owned(payload.ids, user, db, minimum=minimum)

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


# ---------------------------------------------------------------------------
# Review flow (.devnotes/teams/03_API.md § 4.3)
#
# `Task.status` stays the single source of truth for whether a task is approved
# (01_DESIGN.md § 4.2). `TaskReview` is an append-only *log* of transitions, not
# a second opinion about the current state — two columns that can disagree about
# approval is a bug generator.
# ---------------------------------------------------------------------------

_REVIEW_ACTION_STATUS = {
    "approved": "Approved",
    "rejected": "Rejected",
    # "Reopened" is not a status of its own: sending a task back into the
    # working vocabulary is what re-opening means.
    "reopened": "In Progress",
}


def _review_out(review: models.TaskReview, username: Optional[str] = None) -> ReviewOut:
    return ReviewOut(
        id=review.id,
        task_id=review.task_id,
        reviewer_id=review.reviewer_id,
        reviewer_username=username,
        action=review.action,
        note=review.note,
        previous_status=review.previous_status,
        created_at=review.created_at,
    )


@router.post("/{task_id}/review", response_model=ReviewResult)
def review_task(
    task_id: int,
    payload: ReviewCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Approve, reject, or reopen a task, recording who did it and why.

    The explicit review verb. Setting the status through PATCH still works
    (§ 4.2) so existing clients keep functioning; this endpoint additionally
    captures the note and the actor.

    Self-approval is **allowed and recorded** rather than blocked
    (01_DESIGN.md § 4.1): blocking it breaks the common small-team case where
    the reviewer is the only person who touched a dataset, and it is trivially
    defeated by two people approving each other's work. Making it visible in the
    audit trail beats making it impossible.
    """
    task = require_task(task_id, user, db, minimum=ProjectRole.REVIEWER)

    previous_status = task.status
    task.status = _REVIEW_ACTION_STATUS[payload.action]
    task.updated_at = datetime.datetime.now(datetime.timezone.utc)

    review = models.TaskReview(
        task_id=task.id,
        reviewer_id=user.id,
        action=payload.action,
        note=payload.note,
        previous_status=previous_status,
    )
    db.add(review)
    # One commit, deliberately: the review row and the status change land
    # together or not at all. A review without its status change (or the
    # reverse) is worse than neither, because the audit trail would then be
    # lying about what happened.
    commit_with_retry(db)
    db.refresh(review)

    return ReviewResult(
        status="ok",
        task_id=task.id,
        task_status=task.status,
        review=_review_out(review, user.username),
    )


@router.get("/{task_id}/reviews", response_model=List[ReviewOut])
def list_task_reviews(
    task_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Review history for a task, newest first. Readable by any project member."""
    require_task(task_id, user, db, minimum=ProjectRole.VIEWER)

    rows = (
        db.query(models.TaskReview, models.User.username)
        .outerjoin(models.User, models.User.id == models.TaskReview.reviewer_id)
        .filter(models.TaskReview.task_id == task_id)
        .order_by(models.TaskReview.created_at.desc(), models.TaskReview.id.desc())
        .all()
    )
    return [_review_out(review, username) for review, username in rows]


# ---------------------------------------------------------------------------
# Task assignment (.devnotes/teams/03_API.md § 4.3)
# ---------------------------------------------------------------------------


def _validate_assignment(
    project_id: int,
    assigned_team_id: Optional[int],
    assignee_user_id: Optional[int],
    db: Session,
) -> List[str]:
    """Check an assignment, returning warnings. Raises 422 on a hard error.

    The asymmetry here is deliberate (E-09 vs E-10):

    - Assigning to a team with **no grant** is rejected. A task assigned to a
      team that cannot see the project is invisible work — the worst kind of
      silent failure.
    - Assigning a **user outside** the assigned team is allowed with a warning.
      Individual assignment is advisory by design (01_DESIGN.md § 3.4), and a
      legitimate case exists: a reviewer from another team taking one task.
      Rejecting would make the advisory field behave like an enforced one.
    """
    warnings: List[str] = []

    if assigned_team_id is not None:
        team = db.get(models.Team, assigned_team_id)
        if team is None:
            raise HTTPException(status_code=404, detail="Team not found")
        has_grant = (
            db.query(models.ProjectGrant)
            .filter(
                models.ProjectGrant.project_id == project_id,
                models.ProjectGrant.team_id == assigned_team_id,
            )
            .first()
        )
        if has_grant is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Team {team.name} does not have access to this project. "
                    "Grant it access first."
                ),
            )

    if assignee_user_id is not None:
        target = db.get(models.User, assignee_user_id)
        if target is None:
            raise HTTPException(status_code=404, detail="User not found")
        if assigned_team_id is not None:
            in_team = (
                db.query(models.TeamMembership)
                .filter(
                    models.TeamMembership.team_id == assigned_team_id,
                    models.TeamMembership.user_id == assignee_user_id,
                )
                .first()
            )
            if in_team is None:
                warnings.append(
                    f"{target.username} is not a member of the assigned team."
                )

    return warnings


@router.patch("/{task_id}/assignment", response_model=TaskAssignmentResult)
def update_task_assignment(
    task_id: int,
    payload: TaskAssignment,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Assign a task to a team and/or an individual.

    An explicit `null` unassigns, returning the task to the shared pool; an
    omitted field is left alone. Pydantic cannot tell those apart on its own,
    hence `model_fields_set`.
    """
    task = require_task(task_id, user, db, minimum=ProjectRole.MANAGER)

    sent = payload.model_fields_set
    new_team = payload.assigned_team_id if "assigned_team_id" in sent else task.assigned_team_id
    new_user = payload.assignee_user_id if "assignee_user_id" in sent else task.assignee_user_id

    warnings = _validate_assignment(task.project_id, new_team, new_user, db)

    task.assigned_team_id = new_team
    task.assignee_user_id = new_user
    commit_with_retry(db)

    return TaskAssignmentResult(
        status="ok",
        task_id=task.id,
        assigned_team_id=task.assigned_team_id,
        assignee_user_id=task.assignee_user_id,
        warnings=warnings,
    )


@router.post("/bulk-assign", response_model=BulkAssignResult)
def bulk_assign_tasks(
    payload: BulkAssign,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Assign many tasks at once.

    Follows the filter-don't-fail shape every bulk endpoint here uses: ids the
    caller cannot manage are counted in `skipped` rather than failing the whole
    batch, which would make one stray id lose the entire operation.
    """
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No ids provided")

    allowed, skipped = _restrict_to_owned(
        payload.ids, user, db, minimum=ProjectRole.MANAGER
    )
    if not allowed:
        return BulkAssignResult(status="ok", updated=0, skipped=skipped)

    # Validate once per project rather than per task: the whole batch is being
    # assigned to the same team, so the grant check has the same answer for
    # every task in a given project.
    project_ids = {
        pid
        for (pid,) in db.query(models.Task.project_id)
        .filter(models.Task.id.in_(allowed))
        .distinct()
        .all()
    }
    warnings: List[str] = []
    for project_id in project_ids:
        warnings.extend(
            _validate_assignment(
                project_id, payload.assigned_team_id, payload.assignee_user_id, db
            )
        )

    sent = payload.model_fields_set
    update_data = {}
    if "assigned_team_id" in sent:
        update_data[models.Task.assigned_team_id] = payload.assigned_team_id
    if "assignee_user_id" in sent:
        update_data[models.Task.assignee_user_id] = payload.assignee_user_id

    if update_data:
        update_data[models.Task.updated_at] = datetime.datetime.now(datetime.timezone.utc)
        db.query(models.Task).filter(models.Task.id.in_(allowed)).update(
            update_data, synchronize_session=False
        )
        commit_with_retry(db)

    return BulkAssignResult(
        status="ok",
        updated=len(allowed) if update_data else 0,
        skipped=skipped,
        # Deduplicated: the same warning from three projects is one fact.
        warnings=sorted(set(warnings)),
    )

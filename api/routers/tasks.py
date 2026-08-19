import json
import logging
import os
import datetime
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import case, false, func
from sqlalchemy.orm import Session

import config as _cfg
import models
from database import get_db, commit_with_retry
from formats.annotation_diff import is_pure_append, total_point_count
from schemas import (
    APPROVED_STATUSES,
    is_approved,
    REVIEW_ACTION_STATUS,
    REVIEW_STATUSES,
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
    TaskOrder,
    TaskPage,
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


# Statuses that represent a review decision — the whole approved group (every
# batch synonym) plus 'Rejected'. Moving a task *into* one requires the Reviewer
# role: approving under any batch name is exactly as privileged as approving
# (.devnotes/teams/01_DESIGN.md § 4). Defined in schemas.py so the export
# filter, the metrics and this gate cannot disagree about what counts.


# How many superseded annotation blobs to keep per task.
#
# Was 5, sized on the assumption that saves are minutes apart. Real usage
# disproved that immediately: task 1211 recorded five saves in 61 seconds
# (an annotator deleting shapes one per second, each triggering the debounced
# autosave), so the entire window covered barely a minute of a multi-hour
# session and the values from earlier in the shift were already evicted by the
# time anyone looked. History that only reaches back one minute cannot answer
# "what did this task hold before the thing that went wrong".
#
# 50 covers a working session at that observed rate. The cost is bounded and
# small: rows are capped at 50 x task count regardless of how much editing
# happens, and a blob is annotation JSON, not image data.
#
# Hourly snapshots (schedule-backup.ps1) remain the coarse floor underneath
# this; history is the fine-grained layer that catches what a snapshot cadence
# cannot — including a bad value that is itself overwritten within the hour.
# See .devnotes/task-history/01_DESIGN.md § 4.2.
# The knob itself lives in config.py (rule 12); the rationale stays here with
# the code it governs.
ANNOTATION_HISTORY_KEEP = _cfg.ANNOTATION_HISTORY_KEEP

# Skipping a row is a decision about whether recoverable work survives, so the
# skip is opt-in and the heartbeat below bounds what it can cost. See
# formats/annotation_diff for what counts as purely additive.
ANNOTATION_HISTORY_APPEND_SKIP = _cfg.ANNOTATION_HISTORY_APPEND_SKIP
ANNOTATION_HISTORY_HEARTBEAT_SECONDS = _cfg.ANNOTATION_HISTORY_HEARTBEAT_SECONDS


def _blob_is_empty(blob: Optional[str]) -> bool:
    """True when an annotations blob holds no work.

    The three spellings a client can send for "nothing", matching the
    clear-guard below so the two cannot disagree about what empty means.
    """
    return not blob or blob.strip() in ("", "[]", "null")


def _count_annotations(blob: Optional[str]) -> int:
    """Number of objects in an annotations blob, or 0 if unparseable.

    Only ever used for the denormalised history counters, so a malformed blob
    must degrade to a number rather than raise: failing a save because its
    *history* row could not be counted would make this feature the cause of the
    data loss it exists to prevent.
    """
    if _blob_is_empty(blob):
        return 0
    try:
        parsed = json.loads(blob)
    except (ValueError, TypeError):
        return -1  # distinguishable from a genuine 0 in the loss scan
    return len(parsed) if isinstance(parsed, list) else -1


def _may_skip_as_append(
    db: Session,
    db_task: models.Task,
    incoming: str,
) -> bool:
    """True when this save adds only, and a heartbeat row is not yet due.

    Two independent conditions, both required:

    1. The write is purely additive — nothing in the stored blob is missing
       from the incoming one (`is_pure_append`). If anything was removed or
       changed, the stored value is the only copy of it and must be preserved.

    2. A snapshot is not overdue. Condition 1 alone would let an hour of
       freehand drawing pass with no history at all, so if the newest row for
       this task is older than the heartbeat we keep one anyway. That row is
       the floor a recovery can reach back to between destructive edits.

    Any error answers False: failing to skip costs disk, wrongly skipping costs
    annotations.
    """
    if not is_pure_append(db_task.annotations, incoming):
        return False

    if ANNOTATION_HISTORY_HEARTBEAT_SECONDS <= 0:
        return True

    newest = (
        db.query(models.TaskAnnotationHistory.created_at)
        .filter(models.TaskAnnotationHistory.task_id == db_task.id)
        .order_by(models.TaskAnnotationHistory.id.desc())
        .first()
    )
    if newest is None or newest[0] is None:
        # No history at all for this task yet. Keep the first row so there is
        # something to reach back to, even though this save added only.
        return False

    created_at = newest[0]
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=datetime.timezone.utc)

    age = datetime.datetime.now(datetime.timezone.utc) - created_at
    if age.total_seconds() >= ANNOTATION_HISTORY_HEARTBEAT_SECONDS:
        return False

    logger.debug(
        "Task %s: skipping history row, save is purely additive (%d -> %d points).",
        db_task.id,
        total_point_count(db_task.annotations),
        total_point_count(incoming),
    )
    return True


def _record_annotation_history(
    db: Session,
    db_task: models.Task,
    incoming: str,
    user: Optional[models.User],
    client_id: Optional[str],
) -> None:
    """Preserve the blob this write is about to replace, then prune to N.

    Called immediately before `db_task.annotations` is reassigned, inside the
    caller's transaction: if the annotation write rolls back the history row
    must roll back with it, or the log claims something that never happened.

    Skipped when there is nothing worth keeping — an empty stored value has no
    work to preserve — and when the incoming blob is byte-identical to the
    stored one. That second condition is load-bearing rather than tidy: one tab
    writes the same task from the debounced autosave, the visibilitychange
    beacon and the 30s timer drain, so without it every task would accumulate
    five identical rows within seconds and the real previous value would be
    pushed out of the retention window.

    Never raises, and never poisons the caller's transaction. The whole body
    runs inside a SAVEPOINT (`db.begin_nested()`), which is what makes that
    guarantee real: a failed INSERT — a missing table on a box where the
    migration has not been applied yet, a constraint surprise — marks the
    session as needing a rollback, so merely catching the exception would still
    leave the caller unable to commit the annotation write. Rolling back to the
    savepoint discards only the history attempt and leaves the surrounding
    transaction usable.

    That is not hypothetical: writing this against an un-migrated database
    turned a working save into a 500 until the savepoint was added. History is
    a safety net, and a net that can drop the thing it is catching is worse
    than no net at all.
    """
    try:
        if _blob_is_empty(db_task.annotations):
            return
        if db_task.annotations == incoming:
            return
        if ANNOTATION_HISTORY_APPEND_SKIP and _may_skip_as_append(
            db, db_task, incoming
        ):
            return

        with db.begin_nested():
            db.add(
                models.TaskAnnotationHistory(
                    task_id=db_task.id,
                    annotations=db_task.annotations,
                    annotation_count=_count_annotations(db_task.annotations),
                    replaced_with_count=_count_annotations(incoming),
                    replaced_by_user_id=getattr(user, "id", None),
                    client_id=client_id,
                )
            )
            # Flush so the new row has an id and is visible to the prune below.
            # Still the caller's transaction; nothing is committed here.
            db.flush()

            # Retention. The one sanctioned DELETE on this table — it drops
            # rows that have aged out, never rewrites what happened.
            keep_ids = [
                row_id
                for (row_id,) in db.query(models.TaskAnnotationHistory.id)
                .filter(models.TaskAnnotationHistory.task_id == db_task.id)
                .order_by(models.TaskAnnotationHistory.id.desc())
                .limit(ANNOTATION_HISTORY_KEEP)
                .all()
            ]
            if keep_ids:
                (
                    db.query(models.TaskAnnotationHistory)
                    .filter(
                        models.TaskAnnotationHistory.task_id == db_task.id,
                        ~models.TaskAnnotationHistory.id.in_(keep_ids),
                    )
                    .delete(synchronize_session=False)
                )
    except Exception:
        logger.exception(
            "Task %s: failed to record annotation history; the save itself is unaffected.",
            db_task.id,
        )


def _sync_project_status(project_id: Optional[int], db: Session) -> None:
    """Re-derive the project's status from its tasks. Does not commit.

    Project status is derived from its tasks. It used to be written by the
    GET /metrics endpoint; deriving it on write keeps that read side-effect free
    (CLAUDE.md rule 4 / docs/TIMER_AUDIT.md F13).

    "Done" means *signed off* — any approved-group status — which is the same
    definition `_aggregate_metrics` uses for the completion statistics, so the
    project badge and the Overview progress bar cannot disagree.

    Called from every endpoint that changes a task's status. The review endpoint
    used to skip it, so approving the last task of a project left the project
    sitting at its old status until some unrelated task update happened to
    refresh it — invisible while completion was counted on 'Completed' (which
    review never sets) and glaring now that approving *is* what completes a
    project.
    """
    if project_id is None:
        return

    # Push the pending task change to the DB so the aggregate below counts it;
    # without this the project never reaches 'Completed' on the update that
    # approves its last task.
    db.flush()
    counts = db.query(
        func.count(models.Task.id),
        func.sum(case((models.Task.status.in_(APPROVED_STATUSES), 1), else_=0)),
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


def _require_review_role_for_status(
    db_task: models.Task, new_status: Optional[str], user: models.User, db: Session
) -> None:
    """403 unless the caller may make this status transition.

    Rules:
    - Setting any approved-group status (Approved / Verified / Checked /
      Passed) or Rejected always requires Reviewer role.
    - Moving *away* from a review status (e.g. Approved → In Progress to
      re-open for rework) is allowed for any annotator-capable user — the
      annotator who was assigned the task must be able to act on feedback and
      re-submit without needing a reviewer to manually reset the state first.
    - Any other transition (New ↔ In Progress ↔ Completed) is unrestricted.
    """
    if new_status is None or new_status == db_task.status:
        return

    # Setting a review status always needs reviewer role.
    if new_status in REVIEW_STATUSES:
        role = effective_project_role(user, db_task.project_id, db)
        if not at_least(role, ProjectRole.REVIEWER):
            # Keyed off Rejected, not Approved: every other member of
            # REVIEW_STATUSES is an approval under some batch name, so testing
            # for "Approved" alone would tell a blocked reviewer that
            # "Rejecting requires..." when they tried to mark a task Verified.
            verb = "Rejecting" if new_status == "Rejected" else "Approving"
            raise HTTPException(
                status_code=403,
                detail=f"{verb} requires the Reviewer role on this project.",
            )
        return

    # Moving away from an *approved* status requires reviewer role: un-approving
    # is as much a review verdict as approving, and letting an annotator do it
    # would hand them a one-click thaw of the freeze in `can_write_task`.
    #
    # Leaving 'Rejected' stays open to any write-capable user — that is the
    # annotator acting on feedback and re-submitting, which is the whole point
    # of rejecting rather than deleting.
    if is_approved(db_task.status):
        role = effective_project_role(user, db_task.project_id, db)
        if not at_least(role, ProjectRole.REVIEWER):
            raise HTTPException(
                status_code=403,
                detail=(
                    f"This task was marked {db_task.status}. Re-opening it "
                    "requires the Reviewer role on this project."
                ),
            )


def _require_assigned_team_membership(
    db_task: models.Task, user: models.User, db: Session
) -> None:
    """403 when assignment blocks this writer (E-24).

    Blocks three ways: the task is reserved for another person, it belongs to a
    team the writer is not in, or it has not been assigned to anyone at all.
    Managers and owners are never blocked.
    """
    role = effective_project_role(user, db_task.project_id, db)
    if can_write_task(db_task, user, role, db):
        return

    # The frozen case first, and with its own message. Falling through to the
    # branches below would tell an assigned annotator "this task is assigned to
    # <themselves>", which reads as a bug. Says what happened and who can undo
    # it, so the reader has something to act on.
    if is_approved(db_task.status):
        raise HTTPException(
            status_code=403,
            detail=(
                f"This task was marked {db_task.status} and is locked. "
                "Ask a reviewer to reject or re-open it if it needs changes."
            ),
        )

    # Name whoever the task is actually reserved for, so the message is
    # actionable: "ask Priya" or "ask a manager to reassign it" beats a bare
    # refusal. The individual takes precedence over the team, because that is
    # the narrower claim and the one that blocked this writer.
    if db_task.assignee_user_id and db_task.assignee_user_id != user.id:
        assignee = db.get(models.User, db_task.assignee_user_id)
        raise HTTPException(
            status_code=403,
            detail=(
                f"This task is assigned to {assignee.username}."
                if assignee
                else "This task is assigned to someone else."
            ),
        )

    # Unassigned is its own refusal, not a generic one. "Nobody has been given
    # this yet" tells the reader to ask for it; "you lack permission" invites
    # them to conclude the app is broken.
    if db_task.assigned_team_id is None:
        raise HTTPException(
            status_code=403,
            detail=(
                "This task has not been assigned to anyone yet. "
                "Ask a project manager to assign it to you or your team."
            ),
        )

    team = db.get(models.Team, db_task.assigned_team_id)
    raise HTTPException(
        status_code=403,
        detail=(
            f"This task is assigned to {team.name}, which you are not a member of."
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

def _assignment_names(tasks, db: Session) -> tuple[dict, dict]:
    """Resolve team and user ids on a task list to display names.

    Two batched queries rather than a lookup per row: the canvas needs the names
    to say *who* a task is reserved for, and a per-task `db.get` would be an N+1
    across a gallery of several hundred.
    """
    team_ids = {t.assigned_team_id for t in tasks if t.assigned_team_id is not None}
    user_ids = {t.assignee_user_id for t in tasks if t.assignee_user_id is not None}

    teams = (
        dict(
            db.query(models.Team.id, models.Team.name)
            .filter(models.Team.id.in_(team_ids))
            .all()
        )
        if team_ids
        else {}
    )
    users = (
        dict(
            db.query(models.User.id, models.User.username)
            .filter(models.User.id.in_(user_ids))
            .all()
        )
        if user_ids
        else {}
    )
    return teams, users


_EMPTY_COUNTS = {"comment_count": 0, "class_count": 0}


def _annotation_counts(task_ids: List[int], db: Session) -> Dict[int, dict]:
    """Comment and distinct-label counts per task, without shipping the blobs.

    The Tasks view shows two integers per row — a comment count and a count of
    distinct classes used. It used to obtain them by downloading every task's
    full annotation JSON and counting in the browser, which on a 120-task
    project meant ~8.9 MB of payload to render two columns
    (.devnotes/server-optimization/03_TASKS_PAGE.md).

    The blobs still have to be read *here* — they are stored as opaque `Text`,
    so Postgres cannot count inside them (finding F16) — but they stop at the
    application, and only the integers cross the wire. That is the bulk of the
    win: the browser no longer parses megabytes to produce a handful of
    numbers, and the JSON response shrinks by orders of magnitude.

    The honest next step is denormalised counter columns maintained on write,
    which would make this free rather than merely cheap. Deliberately not done
    here: that needs a migration plus a backfill, and it would put new logic in
    the annotation save path, which is exactly where this project has been bitten
    before (.devnotes/deployment-hardening/04_ANNOTATION_SAVE_LOSS.md). Kept as
    a read-path-only change on purpose.
    """
    if not task_ids:
        return {}

    counts: Dict[int, dict] = {}
    rows = (
        db.query(models.Task.id, models.Task.annotations)
        .filter(models.Task.id.in_(task_ids))
        .all()
    )
    for task_id, raw in rows:
        comments = 0
        label_ids = set()
        if raw:
            try:
                parsed = json.loads(raw)
            except (ValueError, TypeError) as exc:
                # Same tolerance as everywhere else that reads this column: a
                # single corrupt blob must not fail the whole task list.
                logger.warning("Task %s has unparseable annotations: %s", task_id, exc)
                parsed = []
            if isinstance(parsed, list):
                for annotation in parsed:
                    if not isinstance(annotation, dict):
                        continue
                    if annotation.get("type") == "comment":
                        comments += 1
                    label_id = annotation.get("labelId")
                    if label_id is not None:
                        label_ids.add(label_id)
        counts[task_id] = {
            "comment_count": comments,
            "class_count": len(label_ids),
        }
    return counts


# ---------------------------------------------------------------------------
# Task list ordering (.devnotes/tasks-pagination/PLAN.md § 3.1)
#
# Until this existed, GET /api/tasks had no ORDER BY at all: rows came back in
# whatever order the database chose. That is not merely untidy, it is unstable
# — on Postgres an UPDATE can move a row within a heap scan, so saving a task
# reordered the list under the user. The Tasks table and the annotation canvas
# each fetch this list separately, so an unstable order meant the canvas's
# "next image" was not the table's next row, and prev/next from image 39/50
# jumped to an unrelated task.
#
# Ordering therefore lives here, in one helper used by *every* endpoint that
# lists or walks tasks. Two copies of the same .order_by() would be free to
# drift apart, and the resulting bug (canvas order disagreeing with table
# order) is exactly the one being fixed.
# ---------------------------------------------------------------------------

# Sortable columns, whitelisted. User input selects a key from this dict; it is
# never interpolated into SQL. An unknown key is rejected rather than silently
# ignored, so a typo surfaces as a 422 instead of a mystery ordering.
_SORT_COLUMNS = {
    "description": models.Task.description,
    "status": models.Task.status,
    "updated_at": models.Task.updated_at,
    "time_spent": models.Task.time_spent,
    "id": models.Task.id,
}

DEFAULT_SORT = "description"
DEFAULT_ORDER = "asc"

# Page size bounds. The cap matters: without it a caller could pass
# page_size=100000 and pull the whole table in one request, reintroducing
# precisely the payload problem that per-task hydration (rule 17) removed.
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 100

# Upper bound on the comma-separated id list the assignee *name* search sends.
# It is generated from the project's assignable-members roster (~20-25 people in
# the target deployment), so this is slack, not a working limit — it exists so a
# hand-crafted URL cannot build an unbounded IN clause.
MAX_ASSIGNEE_FILTER_IDS = 200


def _apply_ordering(query, sort: str, order: str):
    """Order `query` deterministically by `sort`, tie-broken by id.

    The id tiebreaker is not cosmetic, it is what makes pagination correct.
    `ORDER BY description` alone leaves rows with duplicate filenames — routine
    here, since uploads collide on names like `image.jpg` — in an arbitrary
    order that the database may resolve differently between two queries. Under
    LIMIT/OFFSET that means a row can appear on both page 1 and page 2, or on
    neither, depending on how each page's query happened to sort. Appending a
    unique column makes the total order strict, so every row appears exactly
    once across the pages.

    Sorting by any column other than the filename still ends in `description,
    id`, so tasks with equal status (the common case — most are "pending") keep
    a stable, human-meaningful order rather than shuffling per request.
    """
    column = _SORT_COLUMNS.get(sort)
    if column is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown sort field '{sort}'. Allowed: {', '.join(sorted(_SORT_COLUMNS))}",
        )
    if order not in ("asc", "desc"):
        raise HTTPException(
            status_code=422, detail=f"Unknown sort order '{order}'. Allowed: asc, desc"
        )

    direction = (lambda c: c.desc()) if order == "desc" else (lambda c: c.asc())
    keys = [direction(column)]
    if sort != "description":
        keys.append(models.Task.description.asc())
    keys.append(models.Task.id.asc())
    return query.order_by(*keys)


def _visible_tasks_query(projectId: Optional[int], user: models.User, db: Session):
    """Base query for tasks the caller may see, scoped to one project or all.

    Shared by the list, the paged list and the id-order endpoint so all three
    apply identical permission scoping. If these diverged, the canvas could walk
    tasks the table never showed the user.
    """
    if projectId:
        require_project(projectId, user, db, minimum=ProjectRole.VIEWER)
        return db.query(models.Task).filter(models.Task.project_id == projectId)
    # No project given: return tasks across every project the caller can
    # reach, never the whole table.
    return db.query(models.Task).filter(
        models.Task.project_id.in_(accessible_project_ids(user, db))
    )


def _apply_filters(
    query,
    q: Optional[str],
    status: Optional[str],
    team: Optional[str],
    assignee: Optional[str],
    user: models.User,
):
    """Narrow `query` by the Tasks view's search box and three filter selects.

    These moved server-side with pagination and are not optional polish: the
    client used to filter the full in-memory list, and once it only holds one
    page, a client-side filter would search 10 rows out of 4,000 and report
    "no matches" for a task that plainly exists.

    `team` and `assignee` carry the sentinel vocabulary the UI already speaks
    ("unassigned", "mine", "user-<id>"), so the select values pass straight
    through without the caller translating them.

    `assignee` additionally accepts a comma-separated id list
    ("user-3,user-7,user-9") and the sentinel "none". Both serve the assignee
    *name* search: the client matches the typed text against the roster it
    already holds from `/api/projects/{id}/assignable-members` and sends the
    resulting ids, so searching by name costs no join here and no request per
    keystroke. "none" is what a query matching nobody sends — it must return an
    empty page rather than every task, which is what dropping the filter would
    silently do.
    """
    if q:
        # Filename substring, case-insensitive. `ilike` rather than lower(...)
        # so Postgres can still use a suitable index; the wildcards are bound as
        # a parameter, never concatenated into SQL.
        query = query.filter(models.Task.description.ilike(f"%{q}%"))

    if status and status != "All":
        query = query.filter(models.Task.status == status)

    if team and team != "All":
        if team == "unassigned":
            query = query.filter(models.Task.assigned_team_id.is_(None))
        else:
            try:
                query = query.filter(models.Task.assigned_team_id == int(team))
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail=f"Invalid team filter '{team}'")

    if assignee and assignee != "All":
        if assignee == "unassigned":
            query = query.filter(models.Task.assignee_user_id.is_(None))
        elif assignee == "mine":
            query = query.filter(models.Task.assignee_user_id == user.id)
        elif assignee == "none":
            # A name search that matched nobody. `false()` rather than an
            # impossible id: it says what is meant, and no real id can collide
            # with it.
            query = query.filter(false())
        else:
            # "user-<id>", the value the assignee select emits, or a comma-
            # separated list of them from the name search.
            parts = [p.strip() for p in assignee.split(",") if p.strip()]
            if len(parts) > MAX_ASSIGNEE_FILTER_IDS:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"Too many assignee ids ({len(parts)}); "
                        f"the limit is {MAX_ASSIGNEE_FILTER_IDS}."
                    ),
                )
            ids = []
            for part in parts:
                raw = part[5:] if part.startswith("user-") else part
                try:
                    ids.append(int(raw))
                except (TypeError, ValueError):
                    raise HTTPException(
                        status_code=422, detail=f"Invalid assignee filter '{assignee}'"
                    )
            # `in_` even for one id, so the single-select and the name search
            # take exactly the same path — one shape to reason about and test.
            query = query.filter(models.Task.assignee_user_id.in_(ids))

    return query


def _as_page(rows: list, page: Optional[int], page_size: int, total: Optional[int]):
    """Wrap `rows` in the paging envelope, or hand them back bare when unpaged.

    Both branches of get_tasks funnel through here so the two shapes are decided
    in exactly one place; building the envelope at each return site invites the
    branches to disagree about a field.
    """
    if page is None:
        return rows
    # Built through the schema rather than as a loose dict. The endpoint serves
    # two shapes so it cannot declare a single `response_model` (rule 6), and
    # this is the next best thing: the envelope's fields are validated here, and
    # a typo in a key fails at the source instead of reaching the client.
    return TaskPage(
        items=rows,
        total=total,
        page=page,
        page_size=page_size,
        # ceil, with an explicit floor of 1: an empty project has one (empty)
        # page, not zero, so the pager always has a page to be on.
        total_pages=max(1, -(-total // page_size)),
    )


@router.get("")
def get_tasks(
    projectId: Optional[int] = Query(None),
    include_annotations: bool = Query(True),
    page: Optional[int] = Query(None, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    sort: str = Query(DEFAULT_SORT),
    order: str = Query(DEFAULT_ORDER),
    q: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    team: Optional[str] = Query(None),
    assignee: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """List tasks, ordered by filename ascending unless told otherwise.

    **Two response shapes, selected by `page`.** Without `page` this returns a
    bare JSON array, exactly as it always has. With `page` it returns a
    `TaskPage` envelope carrying the totals a pager needs.

    The split is deliberate back-compat: this endpoint has several callers (the
    Tasks table, the canvas, ad-hoc scripts), so unconditionally wrapping the
    array would break every one of them at once. Callers opt in by asking for a
    page. `tests/test_tasks_pagination.py` pins the unpaged shape so a later
    refactor cannot quietly drop it.

    An out-of-range `page` returns empty `items` with the true `total` rather
    than a 404: deleting the last row of the last page is then recoverable —
    the client sees `total_pages` shrink and clamps — instead of a dead end.
    """
    query = _visible_tasks_query(projectId, user, db)
    query = _apply_filters(query, q, status, team, assignee, user)
    query = _apply_ordering(query, sort, order)

    total = None
    if page is not None:
        # COUNT before LIMIT/OFFSET. order_by is stripped first: ordering a
        # count is wasted work, and Postgres rejects an ORDER BY over a column
        # that isn't grouped or selected in some count formulations.
        total = query.order_by(None).count()
        query = query.offset((page - 1) * page_size).limit(page_size)

    if not include_annotations:
        # The annotations column is deliberately absent from this projection:
        # not fetching it is the entire point of the flag (CLAUDE.md rule 17).
        # `comment_count`/`class_count` below are derived from a *separate*
        # narrow query so the blobs never reach the response.
        query = query.with_entities(
            models.Task.id, models.Task.description, models.Task.assignee,
            models.Task.image_path, models.Task.status, models.Task.time_spent,
            models.Task.updated_at,
            models.Task.assigned_team_id, models.Task.assignee_user_id,
        )
        tasks = query.all()
        team_names, user_names = _assignment_names(tasks, db)
        counts = _annotation_counts([t.id for t in tasks], db)
        rows = [{"id": t.id, "description": t.description, "assignee": t.assignee,
                 "image_path": t.image_path, "status": t.status, "time_spent": t.time_spent,
                 "updated_at": t.updated_at,
                 "assigned_team_id": t.assigned_team_id,
                 "assignee_user_id": t.assignee_user_id,
                 "assigned_team_name": team_names.get(t.assigned_team_id),
                 "assignee_name": user_names.get(t.assignee_user_id),
                 # Two integers per task so the Tasks view's Classes and
                 # Comments columns still render without shipping every blob.
                 **counts.get(t.id, _EMPTY_COUNTS),
                 "annotations": []} for t in tasks]
        return _as_page(rows, page, page_size, total)

    tasks = query.all()
    team_names, user_names = _assignment_names(tasks, db)
    result = []
    for t in tasks:
        annotations_data = []
        if t.annotations:
            try:
                annotations_data = json.loads(t.annotations)
            except (ValueError, TypeError) as exc:
                logger.warning("Task %s has unparseable annotations: %s", t.id, exc)
        # Counted from the already-parsed list rather than via
        # _annotation_counts, which would re-read and re-parse the same blobs.
        # The fields are present on both branches so a client never has to care
        # which one served it.
        comment_count = sum(
            1 for a in annotations_data
            if isinstance(a, dict) and a.get("type") == "comment"
        )
        class_count = len({
            a.get("labelId") for a in annotations_data
            if isinstance(a, dict) and a.get("labelId") is not None
        })
        result.append({
            "id": t.id, "description": t.description, "assignee": t.assignee,
            "image_path": t.image_path, "status": t.status, "time_spent": t.time_spent,
            "updated_at": t.updated_at,
            # Both assignment fields ship on every task row: the Tasks view's
            # Team and Assignee columns cannot render without them, and their
            # absence here is why the Team column always read "Unassigned".
            "assigned_team_id": t.assigned_team_id,
            "assignee_user_id": t.assignee_user_id,
            "assigned_team_name": team_names.get(t.assigned_team_id),
            "assignee_name": user_names.get(t.assignee_user_id),
            "comment_count": comment_count,
            "class_count": class_count,
            "annotations": annotations_data
        })
    return _as_page(result, page, page_size, total)

@router.get("/order", response_model=TaskOrder)
def get_task_order(
    projectId: Optional[int] = Query(None),
    sort: str = Query(DEFAULT_SORT),
    order: str = Query(DEFAULT_ORDER),
    q: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    team: Optional[str] = Query(None),
    assignee: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """The full ordered list of task ids — ids only, no row data.

    This exists for the annotation canvas. The canvas needs to know the whole
    sequence in order to answer "what is the image after this one?" and to show
    "39 / 50", but under server-side pagination it can no longer get that from
    the list endpoint, which now returns one page.

    Fetching every *row* just to learn the order would undo the payload work
    that rule 17 did, so this returns bare integers: a 4,000-task project is
    ~30 KB here versus megabytes of rows. The canvas builds its gallery from
    these ids and hydrates each task's real data through GET /api/tasks/{id}
    when it is actually opened.

    Ordering and permission scoping come from the same helpers as the list
    endpoint, so the canvas's sequence is identical to the table's by
    construction — which is the whole point, and what makes prev/next from
    39/50 land on 38/50 and 40/50.

    Declared before `GET /{task_id}` deliberately, for the same reason as
    `/lock-status`: FastAPI matches in declaration order, and a literal path
    registered after a parameterised sibling is swallowed as
    `task_id="order"` and fails as a 422.
    """
    query = _visible_tasks_query(projectId, user, db)
    # Same filters as the list. The canvas walks what the table showed, so a
    # user who filtered to "Rejected" pages through only rejected tasks rather
    # than silently walking into ones the table had excluded.
    query = _apply_filters(query, q, status, team, assignee, user)
    query = _apply_ordering(query, sort, order)
    return {"ids": [row_id for (row_id,) in query.with_entities(models.Task.id).all()]}


@router.get("/lock-status")
def bulk_lock_status(projectId: int = Query(...), db: Session = Depends(get_db),
                     user: models.User = Depends(get_current_user)):
    """Lock state for every currently-locked task in one project.

    The Tasks view shows a "busy" badge on tasks another annotator has open. It
    used to get that by calling GET /{task_id}/lock-status once per task — 120
    authenticated requests on a 120-task project, each doing a JWT decode, a
    user lookup and a full permission resolve, all to read an in-process dict
    that needed no database at all. Over HTTP/1.1 the browser also ran them ~6
    at a time, blocking the connection pool for everything else on the page.
    See .devnotes/server-optimization/03_TASKS_PAGE.md.

    **Only locked tasks are returned.** The client treats a missing id as
    unlocked, which is what the per-task endpoint already reported for a free
    task, so the rendered result is identical. Returning the locked minority
    keeps the response tiny — a lock means someone has that task open *right
    now*, so it is normally a handful of entries at most.

    Shape per entry mirrors GET /{task_id}/lock-status exactly (`locked`,
    `locked_by`, `seconds_remaining`) so the two cannot drift and the caller can
    use either interchangeably.

    Declared before `GET /{task_id}` deliberately: FastAPI matches routes in
    declaration order, so a literal path registered after a parameterised
    sibling is swallowed as `task_id="lock-status"` and fails as a 422.

    Permission: ANNOTATOR on the project, matching the per-task endpoint — a
    viewer cannot claim a task, so the badge tells them nothing actionable.
    Resolved **once** here rather than once per task, which is the other half of
    the saving.

    Single-worker constraint (CLAUDE.md rule 9) is unchanged: this reads the
    same in-process `_TASK_LOCKS` dict as every other lock endpoint.
    """
    require_project(projectId, user, db, minimum=ProjectRole.ANNOTATOR)

    # Nothing is locked anywhere — skip the task-id query entirely. This is the
    # overwhelmingly common case on a quiet project.
    if not _TASK_LOCKS:
        return {}

    # Restrict to this project's tasks so a lock held on some other project's
    # task can never leak through. Only the ids are fetched.
    task_ids = {
        task_id
        for (task_id,) in db.query(models.Task.id)
        .filter(models.Task.project_id == projectId)
        .all()
    }

    now = datetime.datetime.now(datetime.timezone.utc)
    result = {}
    # Iterate the (small) lock dict rather than the (large) task id set, and
    # snapshot its keys: _lock_status evicts stale entries, which mutates the
    # dict during iteration.
    for task_id in list(_TASK_LOCKS.keys()):
        if task_id not in task_ids:
            continue
        lock = _lock_status(task_id)
        if not lock:
            continue
        age = (now - lock["claimed_at"]).total_seconds()
        result[str(task_id)] = {
            "locked": True,
            "locked_by": lock["client_id"],
            "seconds_remaining": max(0, TASK_LOCK_TTL_SECONDS - int(age)),
        }
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

    team = (
        db.get(models.Team, task.assigned_team_id)
        if task.assigned_team_id is not None
        else None
    )
    assignee_user = (
        db.get(models.User, task.assignee_user_id)
        if task.assignee_user_id is not None
        else None
    )
    role = effective_project_role(user, task.project_id, db)

    return TaskDetail(
        id=task.id,
        description=task.description,
        assignee=task.assignee,
        image_path=task.image_path,
        status=task.status,
        time_spent=task.time_spent,
        updated_at=task.updated_at,
        annotations=annotations_data,
        assigned_team_id=task.assigned_team_id,
        assignee_user_id=task.assignee_user_id,
        assigned_team_name=team.name if team else None,
        assignee_name=assignee_user.username if assignee_user else None,
        # The same call the save path makes, so the canvas cannot believe it may
        # write something the server will refuse.
        can_write=can_write_task(task, user, role, db),
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
        if db_task.updated_at:
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
            elif task.annotations is not None:
                # task.updated_at is None / missing, and this write carries an
                # annotation set.
                #
                # A missing token is only accepted if identity proves this is the SAME client
                # writing over its own previous save (e.g. after a beacon nulled updated_at).
                # A different client sending null updated_at cannot prove freshness and must 409.
                #
                # Scoped to writes that actually carry annotations: this whole
                # branch exists to stop a client clobbering annotation work it
                # never saw. A metadata-only write (status, assignee,
                # description — the Tasks page's status dropdown and edit form)
                # touches no annotations and therefore cannot destroy any, so
                # judging it by this rule refused every such edit with a 409
                # blaming "another user" even when the caller was alone on the
                # task. The annotation path below is unchanged.
                is_same_client = (
                    task.client_id is not None
                    and db_task.last_client_id is not None
                    and task.client_id == db_task.last_client_id
                )
                if not is_same_client:
                    raise HTTPException(
                        status_code=409,
                        detail="Task was updated by another user. Please refresh to see latest annotations.",
                    )
        # Did this write actually change anything?
        #
        # `updated_at` is only moved when the answer is yes. A reviewer who
        # opens a task, pans and zooms, and leaves produces a time-only save
        # (the timer auto-starts on the first canvas pointerdown), and bumping
        # the timestamp for it made a look-only visit indistinguishable from a
        # fresh edit — in the Tasks list, and in the concurrency token every
        # other client holds. See .devnotes/unwanted-time-change/01_DIAGNOSIS.md.
        #
        # The test is *value* difference, not key presence: clients resend the
        # full record on every drain, so "the key was supplied" says nothing
        # about whether anything moved.
        #
        # Deliberately NOT counted as a change:
        #   * `last_client_id` — it records who wrote last, not what they wrote.
        #     Counting it would bump on the first look-only save from every new
        #     tab, which is precisely the case being fixed. It is still assigned
        #     below, because conflict detection depends on it being current.
        #   * a zero `time_spent_delta` — adding nothing changes nothing.
        changed = False

        if task.client_id is not None:
            db_task.last_client_id = task.client_id
        if task.assignee is not None:
            if task.assignee != db_task.assignee:
                changed = True
            db_task.assignee = task.assignee
        if task.status is not None:
            # The review gate for 'Approved'/'Rejected' is enforced above, in
            # _require_review_role_for_status, before conflict detection runs.
            # (This is the line the old comment predicted would need a check
            # "if projects ever gain shared members" — they now have them.)
            if task.status != db_task.status:
                changed = True
            db_task.status = task.status
        if task.description is not None:
            if task.description != db_task.description:
                changed = True
            db_task.description = task.description
        if task.time_spent_delta:
            # Truthiness, so a zero delta is inert. A non-zero one is a real
            # column write and does count: once the client stops sending
            # seconds for no-edit visits, a non-zero delta only arrives when
            # work genuinely happened, and treating it as inert would leave a
            # real session's final drain with a stale timestamp.
            changed = True
            db_task.time_spent = (db_task.time_spent or 0) + task.time_spent_delta
        if task.annotations is not None:
            # Refuse a save that would silently erase existing work unless the
            # client explicitly confirms it means to. A save carrying an empty
            # annotation set is indistinguishable, at this point, from a client
            # that reloaded into a broken/half-hydrated state and autosaved its
            # blank default over real work — exactly what happened to task 692
            # (.devnotes/offline/INCIDENT_692.md): a CSRF-cookie desync after an
            # outage caused a storm of rejected writes, and when the cookie
            # resynced the next autosave carried `[]` and overwrote 403 real
            # polygons. Conflict detection did not catch it because the same
            # client_id never conflicts with itself (by design, see
            # 04_ANNOTATION_SAVE_LOSS.md) — this is the guard for the case that
            # rule cannot cover.
            incoming_is_empty = task.annotations.strip() in ("", "[]", "null")
            existing_has_work = db_task.annotations and db_task.annotations.strip() not in ("", "[]", "null")
            if incoming_is_empty and existing_has_work:
                if not task.allow_clear:
                    logger.warning(
                        "Task %s: incoming save attempted to clear annotations from existing work without allow_clear (client_id=%s, user=%s). Refused with 422.",
                        db_task.id,
                        task.client_id,
                        getattr(user, "username", "unknown"),
                    )
                    # Deliberately NOT 409: the frontend's 409 handler means "a
                    # different client wrote since you last read — pick a version",
                    # and its "keep mine" path would resend this exact empty
                    # payload with allow_clear still unset, looping forever. 422 is
                    # "the payload itself is refused on the merits" and gets its
                    # own un-retryable handling in timer.js/init.js, same shape as
                    # the existing 403-forbidden path.
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "Refusing to clear existing annotations. Reload the task to "
                            "see the current work; if you really mean to delete every "
                            "annotation, delete them in the UI and save again."
                        ),
                    )
                else:
                    logger.warning(
                        "Task %s: explicit clear of annotations executed with allow_clear=True (client_id=%s, user=%s).",
                        db_task.id,
                        task.client_id,
                        getattr(user, "username", "unknown"),
                    )
            # Preserve what this write is about to destroy, in this same
            # transaction. Placed after the clear-guard so a refused write
            # leaves no history row — the blob was never replaced, so there is
            # nothing superseded to record.
            _record_annotation_history(
                db, db_task, task.annotations, user, task.client_id
            )
            # Compared before the assignment, obviously — and byte-wise, which
            # is the same test `_record_annotation_history` already uses to
            # dedup identical resaves. Two blobs that differ only in key order
            # or whitespace count as a change; that is the conservative
            # direction (an extra bump, never a missed one) and it keeps this
            # flag in lockstep with what history recorded.
            if task.annotations != db_task.annotations:
                changed = True
            db_task.annotations = task.annotations

        # Only a write that changed something moves the timestamp. When nothing
        # changed the stored value is returned untouched, so the caller's
        # concurrency token stays valid rather than being rotated by a no-op
        # (CLAUDE.md rule 11).
        if changed:
            now_utc = datetime.datetime.now(datetime.timezone.utc)
            if db_task.updated_at:
                if db_task.updated_at.tzinfo is None:
                    db_task_updated_at_utc = db_task.updated_at.replace(tzinfo=datetime.timezone.utc)
                else:
                    db_task_updated_at_utc = db_task.updated_at
                if now_utc <= db_task_updated_at_utc:
                    now_utc = db_task_updated_at_utc + datetime.timedelta(microseconds=1000)
            db_task.updated_at = now_utc
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
        
    _sync_project_status(db_task.project_id, db)
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
        # A bulk approval is the normal way a batch gets signed off, so it can
        # complete a project just as a single approval can. The ids may span
        # projects (the caller passes ids, not a project), hence the distinct
        # set rather than one id.
        if payload.status is not None:
            affected = {
                pid for (pid,) in db.query(models.Task.project_id)
                .filter(models.Task.id.in_(owned))
                .distinct()
                if pid is not None
            }
            for pid in affected:
                _sync_project_status(pid, db)
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

# Verb -> status. Defined in schemas.py alongside APPROVED_STATUSES so a new
# batch status brings its review verb with it and every approval — whatever
# batch it belongs to — is recorded in the TaskReview log.
_REVIEW_ACTION_STATUS = REVIEW_ACTION_STATUS


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
    # Approving can complete a project, so the derived project status has to be
    # refreshed here too — not only on the task-update path.
    _sync_project_status(task.project_id, db)
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

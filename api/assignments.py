"""The one place task assignment is written.

A task's assignees live in `task_assignees` (a set), while `tasks.assignee`
survives as a denormalized mirror of the primary one. Two representations of
one fact drift apart the moment two different code paths write them, so every
write goes through `set_task_assignees` here — the task router, the bulk route
and the upload path all call it, and none of them touches either table directly.

`task_assignment_events` is appended to in the same transaction, so the history
the project owner reads can never disagree with the assignment it describes.
The rows are written *inside* the caller's transaction rather than after it (the
opposite of api/notifications.py, which must commit first): a notification is a
side effect that may be safely lost, whereas an event row is part of the fact
being recorded, and one that outlived a rolled-back assignment would be a lie.

Callers commit. Nothing here commits, so an assignment change and the task write
that carries it land atomically under one `commit_with_retry` (CLAUDE.md rule 10).
"""
import logging
from typing import Iterable, List, Optional, Sequence

from sqlalchemy.orm import Session

import models

logger = logging.getLogger(__name__)

ACTION_ASSIGNED = "assigned"
ACTION_UNASSIGNED = "unassigned"

# A task with more names than this is a mis-click or a scripted loop, not a
# workflow. The cap keeps one task's assignee list renderable in a table cell
# and bounds the per-save row churn.
MAX_ASSIGNEES_PER_TASK = 25


def normalize_names(names: Optional[Iterable[Optional[str]]]) -> List[str]:
    """Clean an incoming assignee list: trimmed, de-duplicated, order preserved.

    Trimming matches `api/auth.get_current_annotator`, which already resolves
    " Sanjita" to the member "Sanjita" — an untrimmed name here would create an
    assignee that no roster entry, and therefore no authority check, matches.
    De-duplication is case-sensitive on purpose: the roster itself can contain
    both "sanjita" and "Sanjita" as separate people (see the same function),
    so folding case here would silently merge two annotators.
    """
    if not names:
        return []
    seen = set()
    out: List[str] = []
    for raw in names:
        if raw is None:
            continue
        name = str(raw).strip()
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(name)
    return out[:MAX_ASSIGNEES_PER_TASK]


def get_assignees(db: Session, task_id: int) -> List[str]:
    """Current assignees of one task, primary first."""
    rows = (
        db.query(models.TaskAssignee.member_name)
        .filter(models.TaskAssignee.task_id == task_id)
        .order_by(models.TaskAssignee.position, models.TaskAssignee.id)
        .all()
    )
    return [r[0] for r in rows]


def get_assignees_map(db: Session, task_ids: Sequence[int]) -> dict:
    """{task_id: [names, primary first]} for many tasks in one query.

    The task list renders up to a page of rows at a time; per-row queries here
    would put the list back on the N+1 path that `include_annotations=false`
    exists to avoid (CLAUDE.md rule 17).
    """
    if not task_ids:
        return {}
    rows = (
        db.query(
            models.TaskAssignee.task_id,
            models.TaskAssignee.member_name,
        )
        .filter(models.TaskAssignee.task_id.in_(list(task_ids)))
        .order_by(models.TaskAssignee.task_id, models.TaskAssignee.position, models.TaskAssignee.id)
        .all()
    )
    out: dict = {}
    for task_id, name in rows:
        out.setdefault(task_id, []).append(name)
    return out


def set_task_assignees(
    db: Session,
    task: models.Task,
    names: Optional[Iterable[Optional[str]]],
    actor_name: Optional[str] = None,
) -> dict:
    """Make `names` the exact assignee set of `task`. Returns what changed.

    Returns `{"added": [...], "removed": [...], "primary": str|None,
    "changed": bool}`. `added` is what the caller notifies on: only genuinely
    new assignees are news, and every autosave resends the current assignee
    (workspace.js syncToBackend), so a diff — rather than "the payload named
    someone" — is what stops the bell firing on every 30-second drain.

    Does not commit. The caller's `commit_with_retry` covers this write, the
    mirror and the event rows together.
    """
    desired = normalize_names(names)
    current_rows = (
        db.query(models.TaskAssignee)
        .filter(models.TaskAssignee.task_id == task.id)
        .order_by(models.TaskAssignee.position, models.TaskAssignee.id)
        .all()
    )
    current = [r.member_name for r in current_rows]

    if current == desired:
        # Same people in the same order: nothing to write. The mirror is still
        # restamped below only if it has drifted, which it can have on rows
        # written before this module existed.
        primary = desired[0] if desired else None
        if task.assignee != primary:
            task.assignee = primary
        return {"added": [], "removed": [], "primary": primary, "changed": False}

    desired_set = set(desired)
    current_set = set(current)
    added = [n for n in desired if n not in current_set]
    removed = [n for n in current if n not in desired_set]

    by_name = {r.member_name: r for r in current_rows}
    for name in removed:
        db.delete(by_name[name])

    for position, name in enumerate(desired):
        row = by_name.get(name)
        if row is None:
            db.add(models.TaskAssignee(
                task_id=task.id,
                member_name=name,
                position=position,
                assigned_by=actor_name,
            ))
        elif row.position != position:
            # Kept rather than deleted and re-added: the row carries when this
            # person was first assigned, and reordering the list is not a
            # reassignment.
            row.position = position

    for name in added:
        db.add(models.TaskAssignmentEvent(
            task_id=task.id, member_name=name,
            action=ACTION_ASSIGNED, actor_name=actor_name,
        ))
    for name in removed:
        db.add(models.TaskAssignmentEvent(
            task_id=task.id, member_name=name,
            action=ACTION_UNASSIGNED, actor_name=actor_name,
        ))

    # Restamp the mirror. This is the only line in the codebase that may assign
    # to `task.assignee`.
    primary = desired[0] if desired else None
    task.assignee = primary

    return {"added": added, "removed": removed, "primary": primary, "changed": True}


def get_history(db: Session, task_id: int) -> List[dict]:
    """Every assignment change on one task, newest first.

    Includes people no longer assigned — that is the point of the table. The
    backfilled rows from migration d7a1b93c5e42 have no events, so a task never
    reassigned since the feature shipped returns an empty list while still
    reporting a current assignee; the UI says so rather than implying nobody was
    ever assigned.
    """
    rows = (
        db.query(models.TaskAssignmentEvent)
        .filter(models.TaskAssignmentEvent.task_id == task_id)
        .order_by(models.TaskAssignmentEvent.created_at.desc(), models.TaskAssignmentEvent.id.desc())
        .all()
    )
    return [
        {
            "member_name": r.member_name,
            "action": r.action,
            "actor_name": r.actor_name,
            "created_at": r.created_at,
        }
        for r in rows
    ]


def get_project_history(db: Session, project_id: int) -> List[dict]:
    """Every assignment change across one project, newest first.

    One query joined to `tasks` rather than per-task calls to `get_history`: a
    project holds hundreds of images, and the CSV export needs all of them at
    once. Each row carries the task's filename so the export is readable
    without a second lookup.

    Includes the *current* assignees as synthetic rows for tasks that have no
    events — the backfill (migration d7a1b93c5e42) recorded who holds each task
    but invented no history, so an export keyed only on events would omit every
    task that has not been reassigned since the feature shipped. Those rows are
    marked `source='current'` so a reader can tell a recorded change from a
    known present state.
    """
    rows = (
        db.query(
            models.TaskAssignmentEvent.task_id,
            models.Task.description,
            models.TaskAssignmentEvent.member_name,
            models.TaskAssignmentEvent.action,
            models.TaskAssignmentEvent.actor_name,
            models.TaskAssignmentEvent.created_at,
        )
        .join(models.Task, models.Task.id == models.TaskAssignmentEvent.task_id)
        .filter(models.Task.project_id == project_id)
        .order_by(
            models.TaskAssignmentEvent.created_at.desc(),
            models.TaskAssignmentEvent.id.desc(),
        )
        .all()
    )
    out = [
        {
            "task_id": task_id,
            "description": description,
            "member_name": member_name,
            "action": action,
            "actor_name": actor_name,
            "created_at": created_at,
            "source": "event",
        }
        for task_id, description, member_name, action, actor_name, created_at in rows
    ]

    tasks_with_events = {r["task_id"] for r in out}
    current = (
        db.query(
            models.TaskAssignee.task_id,
            models.Task.description,
            models.TaskAssignee.member_name,
            models.TaskAssignee.assigned_by,
            models.TaskAssignee.assigned_at,
        )
        .join(models.Task, models.Task.id == models.TaskAssignee.task_id)
        .filter(models.Task.project_id == project_id)
        .order_by(models.TaskAssignee.task_id, models.TaskAssignee.position)
        .all()
    )
    for task_id, description, member_name, assigned_by, assigned_at in current:
        if task_id in tasks_with_events:
            continue
        out.append({
            "task_id": task_id,
            "description": description,
            "member_name": member_name,
            "action": "currently assigned",
            "actor_name": assigned_by,
            "created_at": assigned_at,
            "source": "current",
        })
    return out


def get_participants(db: Session, task_id: int) -> List[str]:
    """Everyone who has ever held this task, current assignees first.

    This is the answer to "show me all the assignee names" on one row: the
    current set, then anyone who held it before and no longer does, in the order
    they were most recently involved.
    """
    current = get_assignees(db, task_id)
    seen = set(current)
    past: List[str] = []
    rows = (
        db.query(models.TaskAssignmentEvent.member_name)
        .filter(models.TaskAssignmentEvent.task_id == task_id)
        .order_by(models.TaskAssignmentEvent.created_at.desc(), models.TaskAssignmentEvent.id.desc())
        .all()
    )
    for (name,) in rows:
        if name not in seen:
            seen.add(name)
            past.append(name)
    return current + past

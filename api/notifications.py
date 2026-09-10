"""Creating notifications for annotators.

The read/write endpoints live in `api/routers/notifications.py`; this module is
the *write* side that the task router calls, kept separate so two routers can
emit without importing each other (CONVENTIONS.md § 2).

Two rules govern every call here, both learned from bugs already in this repo:

1. **Emit only after the write it describes has committed.** `commit_with_retry`
   re-runs its block on lock/deadlock contention (CLAUDE.md rule 10), and the
   save path has already been bitten once by state mutated before a retry
   (see the `incoming_status` comment in api/routers/tasks.py). A notification
   written inside the transaction announces a change that may still roll back.

2. **Never let a notification failure break the write.** The annotator's save
   succeeding matters; the bell badge does not. Failures are logged and
   swallowed *at the call site boundary* — `emit` below — rather than with a
   bare except (CONVENTIONS.md § 3).

Recipients are `team_members.name`, the annotator identity carried by
`X-Annotator-Name`, because the deployment shares one login and `User` cannot
identify a person. A recipient with no `team_members` row is skipped rather
than created: the FK would reject it, and inventing members from a stale
assignee string would pollute the Teams page.
"""
import logging
from typing import Optional

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

import models
from database import commit_with_retry

logger = logging.getLogger(__name__)

# Notification types. `entity_id` is read according to these.
TYPE_TASK = "task"
TYPE_PROJECT = "project"

# Cap on a single message so a pathological project or task name cannot write
# an unbounded row (the column is Text, but the bell renders one line).
MAX_MESSAGE_CHARS = 300


def _member_exists(db: Session, name: str) -> bool:
    return db.query(models.TeamMember.name).filter(models.TeamMember.name == name).first() is not None


def emit(
    db: Session,
    recipient_name: Optional[str],
    type: str,
    message: str,
    entity_id: Optional[int] = None,
    actor_name: Optional[str] = None,
) -> bool:
    """Create one notification. Call only *after* the described write committed.

    Returns True when a row was written. Never raises: a failure here must not
    fail the caller's already-committed request.

    `actor_name` is the person who caused the event; when it equals the
    recipient the notice is dropped, since nobody needs telling about their own
    action (an annotator saving their own task would otherwise notify the owner
    on every autosave when they are the owner).
    """
    if not recipient_name or not message:
        return False
    if actor_name and actor_name == recipient_name:
        return False

    try:
        if not _member_exists(db, recipient_name):
            logger.debug("No team member %r; notification skipped", recipient_name)
            return False

        db.add(models.Notification(
            recipient_name=recipient_name,
            type=type,
            entity_id=entity_id,
            message=message[:MAX_MESSAGE_CHARS],
            is_read=0,
        ))
        commit_with_retry(db)
        return True
    except SQLAlchemyError:
        # The caller's write is already committed; losing the notice is the
        # acceptable outcome, but it must be visible in the log.
        logger.exception(
            "Failed to write %s notification for %r (entity_id=%s)",
            type, recipient_name, entity_id,
        )
        db.rollback()
        return False


def notify_task_status_changed(
    db: Session, project: Optional[models.Project], task: models.Task,
    new_status: str, actor_name: Optional[str],
) -> None:
    """Tell the project owner that one of their tasks changed status."""
    if project is None:
        return
    label = task.description or f"Task {task.id}"
    emit(
        db,
        recipient_name=project.creator,
        type=TYPE_TASK,
        entity_id=task.id,
        message=f'"{label}" is now {new_status}',
        actor_name=actor_name,
    )


def notify_task_assigned(
    db: Session, task_id: int, task_label: str,
    assignee: Optional[str], actor_name: Optional[str],
) -> None:
    """Tell an annotator that a task has been assigned to them."""
    emit(
        db,
        recipient_name=assignee,
        type=TYPE_TASK,
        entity_id=task_id,
        message=f'You were assigned "{task_label}"',
        actor_name=actor_name,
    )

"""Read side of the notification bell.

Writes happen in `api/notifications.py`, which the task router calls after a
change commits. This router only lists a person's unread notices and marks them
read.

Recipients are annotator names (`team_members.name`), not usernames: the LAN
deployment shares one login, so the authenticated `User` identifies the account
and cannot tell two annotators apart. The caller's identity therefore comes
from `get_current_annotator` (the `X-Annotator-Name` header), exactly as task
assignment does.
"""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

import models
import schemas
from api.auth import get_current_user, get_current_annotator, require_csrf
from database import get_db, commit_with_retry

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/notifications",
    tags=["notifications"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)

# Upper bound on one page of unread notices. The bell shows a short list; an
# annotator returning from leave should not pull thousands of rows.
MAX_UNREAD = 50


def _caller_name(user: models.User, annotator: Optional[models.TeamMember]) -> str:
    """The annotator identity notifications are addressed to.

    Falls back to the username so a single-user or header-less client still
    resolves to something; `ping_presence` creates a TeamMember under that same
    name, so the two agree.
    """
    return annotator.name if annotator else user.username


@router.get("", response_model=List[schemas.NotificationResponse])
def get_unread_notifications(
    limit: int = Query(MAX_UNREAD, ge=1, le=MAX_UNREAD),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    annotator: Optional[models.TeamMember] = Depends(get_current_annotator),
):
    """Unread notifications for the calling annotator, newest first."""
    name = _caller_name(user, annotator)
    rows = (
        db.query(models.Notification)
        .filter(
            models.Notification.recipient_name == name,
            models.Notification.is_read == 0,
        )
        .order_by(models.Notification.created_at.desc(), models.Notification.id.desc())
        .limit(limit)
        .all()
    )

    # Task notices carry a task id, but the UI links to the project and to the
    # task's canvas, so the owning project's id *and* name are resolved here in
    # one join rather than N lookups client side. A task deleted since the
    # notice was written simply has no project, and the UI falls back to plain
    # text instead of a dead link.
    task_ids = [r.entity_id for r in rows if r.type == "task" and r.entity_id]
    project_by_task = {}
    if task_ids:
        project_by_task = {
            task_id: (project_id, project_name)
            for task_id, project_id, project_name in (
                db.query(models.Task.id, models.Project.id, models.Project.name)
                .join(models.Project, models.Project.id == models.Task.project_id)
                .filter(models.Task.id.in_(task_ids))
                .all()
            )
        }

    # Project notices name themselves, so their ids are resolved in one go too.
    project_ids = [r.entity_id for r in rows if r.type == "project" and r.entity_id]
    name_by_project = {}
    if project_ids:
        name_by_project = dict(
            db.query(models.Project.id, models.Project.name)
            .filter(models.Project.id.in_(project_ids))
            .all()
        )

    out = []
    for row in rows:
        item = schemas.NotificationResponse.model_validate(row)
        if row.type == "task":
            project_id, project_name = project_by_task.get(row.entity_id, (None, None))
            item.project_id = project_id
            item.project_name = project_name
        elif row.type == "project":
            item.project_id = row.entity_id
            item.project_name = name_by_project.get(row.entity_id)
        out.append(item)
    return out


@router.post("/mark-read")
def mark_notifications_read(
    payload: schemas.MarkReadRequest,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    annotator: Optional[models.TeamMember] = Depends(get_current_annotator),
):
    """Mark the given notifications read.

    Scoped to the caller's own rows, so one annotator cannot clear another's
    bell by guessing ids. An empty list marks everything read, which is what
    the "mark all" affordance sends.
    """
    name = _caller_name(user, annotator)
    query = db.query(models.Notification).filter(
        models.Notification.recipient_name == name,
        models.Notification.is_read == 0,
    )
    if payload.notification_ids:
        query = query.filter(models.Notification.id.in_(payload.notification_ids))

    updated = query.update({models.Notification.is_read: 1}, synchronize_session=False)
    if updated:
        commit_with_retry(db)
    return {"status": "ok", "updated": updated}

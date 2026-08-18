"""Accumulated time logging — formerly `/api/team`.

The table behind this was named `team_members` and has nothing to do with the
Teams feature (.devnotes/teams/01_DESIGN.md § 8); it is a free-text name and a
seconds counter. Renamed to `time_logs` so it does not collide with
`TeamMembership`.

`api/routers/team.py` remains mounted as a deprecated alias for one release,
because the frontend pins module versions by hand (`./foo.js?v=1`, deferred item
D4) and annotators may be running a cached bundle that still calls `/api/team`.
"""
import urllib.parse
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import models
from api.auth import get_current_user, require_csrf
from api.permissions import (
    TeamRole,
    can_write_task,
    effective_project_role,
)
from database import get_db, commit_with_retry
from schemas import TeamMemberModel, TeamTime, TimeLogOut, TimeLogUpdateResult

router = APIRouter(
    prefix="/api/time-logs",
    tags=["time-logs"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)


def visible_time_logs(db: Session, current_user: models.User) -> List[models.TimeLog]:
    """Rows this caller may see: their own, plus their team members' if they
    manage a team.

    Closes a pre-existing leak: this endpoint used to return **every** row in the
    table to any authenticated caller, which under individual accounts discloses
    the whole roster and everyone's logged hours. Scoping it is the whole point
    of touching this endpoint during the rename.

    Rows are matched on `user_id` where it is set and on the username string
    where it is not — historical rows predate `user_id` and were never linked, so
    a purely id-based filter would hide a user's own history from them.
    """
    managed_team_ids = [
        team_id
        for (team_id,) in db.query(models.TeamMembership.team_id)
        .filter(
            models.TeamMembership.user_id == current_user.id,
            models.TeamMembership.role.in_([TeamRole.MANAGER.value, TeamRole.OWNER.value]),
        )
        .all()
    ]

    visible_user_ids = {current_user.id}
    visible_names = {current_user.username}

    if managed_team_ids:
        rows = (
            db.query(models.TeamMembership.user_id, models.User.username)
            .join(models.User, models.User.id == models.TeamMembership.user_id)
            .filter(models.TeamMembership.team_id.in_(managed_team_ids))
            .all()
        )
        for user_id, username in rows:
            visible_user_ids.add(user_id)
            visible_names.add(username)

    return (
        db.query(models.TimeLog)
        .filter(
            models.TimeLog.user_id.in_(visible_user_ids)
            | models.TimeLog.name.in_(visible_names)
        )
        .all()
    )


@router.get("", response_model=List[TimeLogOut])
def get_time_logs(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return [
        TimeLogOut(name=row.name, time_logged=row.time_logged or 0, user_id=row.user_id)
        for row in visible_time_logs(db, current_user)
    ]


@router.post("", response_model=TimeLogOut)
def create_time_log(
    member: TeamMemberModel,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    existing = (
        db.query(models.TimeLog).filter(models.TimeLog.name == member.name).first()
    )
    if existing:
        return TimeLogOut(
            name=existing.name,
            time_logged=existing.time_logged or 0,
            user_id=existing.user_id,
        )

    # Link the row to an account when the name is an exact username match — the
    # same rule the M4 backfill uses. Anything looser would guess at attribution.
    matched = (
        db.query(models.User).filter(models.User.username == member.name).first()
    )
    row = models.TimeLog(
        name=member.name,
        time_logged=0,
        user_id=matched.id if matched else None,
    )
    db.add(row)
    commit_with_retry(db)
    return TimeLogOut(name=row.name, time_logged=0, user_id=row.user_id)


@router.delete("/{name}")
def delete_time_log(
    name: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    name = urllib.parse.unquote(name)
    db.query(models.TimeLog).filter(models.TimeLog.name == name).delete()
    commit_with_retry(db)
    return {"status": "ok"}


@router.post("/time", response_model=TimeLogUpdateResult)
def update_time_logged(
    payload: TeamTime,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    # Time is credited to the authenticated user, not to the client-supplied
    # name, which came from an editable localStorage value and let anyone log
    # time against anyone. See docs/TIMER_AUDIT.md F7.
    name = current_user.username

    # Refuse to bank seconds accrued against a task the caller may no longer
    # write. An approved task is frozen (api/permissions.py::can_write_task), and
    # a frozen task must leak nothing — including the user's lifetime clock,
    # which is a separate endpoint from the per-task `time_spent` and so is not
    # covered by the task write gate.
    #
    # Answered 200 with status "ignored", not 403: the client cannot fix this by
    # retrying, and the sync loop treats a failure as "retry later", so a 403
    # would strand a delta that is re-sent forever. The seconds are simply
    # dropped — they were not the annotator's to log.
    if payload.task_id is not None:
        task = db.get(models.Task, payload.task_id)
        if task is not None:
            role = effective_project_role(current_user, task.project_id, db)
            if not can_write_task(task, current_user, role, db):
                existing = (
                    db.query(models.TimeLog)
                    .filter(models.TimeLog.name == name)
                    .first()
                )
                return TimeLogUpdateResult(
                    status="ignored",
                    time_logged=(existing.time_logged or 0) if existing else 0,
                )

    row = db.query(models.TimeLog).filter(models.TimeLog.name == name).first()
    if not row:
        # Previously an unknown member meant the delta was accepted and silently
        # discarded. Create the row so the seconds are never lost.
        row = models.TimeLog(name=name, time_logged=0, user_id=current_user.id)
        db.add(row)

    if row.user_id is None:
        # A historical row for this exact username: link it now that we know who
        # it is. The M4 backfill only ran once.
        row.user_id = current_user.id

    row.time_logged = (row.time_logged or 0) + payload.time_logged
    commit_with_retry(db)
    return TimeLogUpdateResult(status="ok", time_logged=row.time_logged)

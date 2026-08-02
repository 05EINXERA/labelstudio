"""DEPRECATED alias for `/api/time-logs`.

This router's table was renamed from `team_members` to `time_logs`
(.devnotes/teams/01_DESIGN.md § 8) and its endpoints moved to
`api/routers/time_logs.py`. The old paths are kept for **one release** because
the frontend pins module versions by hand (`./foo.js?v=1`, deferred item D4), so
annotators may still be running a cached bundle that calls `/api/team`.

Every handler here delegates to the `time_logs` implementation — there is no
second copy of the logic, so the scoping fix applies to both paths. Delete this
module once the bundles have rolled over.
"""
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import models
from api.auth import get_current_user, require_csrf
from api.routers import time_logs
from database import get_db
from schemas import TeamMemberModel, TeamTime, TimeLogOut, TimeLogUpdateResult

router = APIRouter(
    prefix="/api/team",
    tags=["team (deprecated)"],
    deprecated=True,
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)


@router.get("", response_model=List[TimeLogOut])
def get_team(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return time_logs.get_time_logs(db=db, current_user=current_user)


@router.post("", response_model=TimeLogOut)
def create_team_member(
    member: TeamMemberModel,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return time_logs.create_time_log(member=member, db=db, current_user=current_user)


@router.delete("/{name}")
def delete_team_member(
    name: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return time_logs.delete_time_log(name=name, db=db, current_user=current_user)


@router.post("/time", response_model=TimeLogUpdateResult)
def update_team_time(
    payload: TeamTime,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return time_logs.update_time_logged(payload=payload, db=db, current_user=current_user)

import urllib.parse

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import models
from database import get_db
from schemas import TeamMemberModel, TeamTime
from api.auth import get_current_user

router = APIRouter(prefix="/api/team", tags=["team"], dependencies=[Depends(get_current_user)])

@router.get("")
def get_team(db: Session = Depends(get_db)):
    team = db.query(models.TeamMember).all()
    return [{"name": t.name, "time_logged": t.time_logged} for t in team]

@router.post("")
def create_team_member(member: TeamMemberModel, db: Session = Depends(get_db)):
    existing = db.query(models.TeamMember).filter(models.TeamMember.name == member.name).first()
    if not existing:
        new_member = models.TeamMember(name=member.name, time_logged=0)
        db.add(new_member)
        db.commit()
    return {"status": "ok"}

@router.delete("/{name}")
def delete_team_member(name: str, db: Session = Depends(get_db)):
    name = urllib.parse.unquote(name)
    db.query(models.TeamMember).filter(models.TeamMember.name == name).delete()
    db.commit()
    return {"status": "ok"}

@router.post("/time")
def update_team_time(
    payload: TeamTime,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    # Time is credited to the authenticated user, not to the client-supplied
    # name, which came from an editable localStorage value and let anyone log
    # time against anyone. See docs/TIMER_AUDIT.md F7.
    name = current_user.username
    member = db.query(models.TeamMember).filter(models.TeamMember.name == name).first()
    if not member:
        # Previously an unknown member meant the delta was accepted and silently
        # discarded. Create the row so the seconds are never lost.
        member = models.TeamMember(name=name, time_logged=0)
        db.add(member)

    member.time_logged = (member.time_logged or 0) + payload.time_logged
    db.commit()
    return {"status": "ok", "time_logged": member.time_logged}

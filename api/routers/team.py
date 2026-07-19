import urllib.parse

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import models
from api.auth import get_current_user
from database import get_db
from schemas import TeamMemberModel, TeamTime

router = APIRouter(
    prefix="/api/team", tags=["team"], dependencies=[Depends(get_current_user)]
)


@router.get("")
def get_team(db: Session = Depends(get_db)):
    team = db.query(models.TeamMember).all()
    return [{"name": t.name, "time_logged": t.time_logged} for t in team]


@router.post("")
def create_team_member(member: TeamMemberModel, db: Session = Depends(get_db)):
    existing = (
        db.query(models.TeamMember)
        .filter(models.TeamMember.name == member.name)
        .first()
    )
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
def update_team_time(payload: TeamTime, db: Session = Depends(get_db)):
    member = (
        db.query(models.TeamMember)
        .filter(models.TeamMember.name == payload.name)
        .first()
    )
    if member:
        member.time_logged = (member.time_logged or 0) + payload.time_logged
        db.commit()
    return {"status": "ok"}

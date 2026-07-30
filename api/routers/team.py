from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import models
from database import get_db, commit_with_retry
from schemas import TeamTime, TeamMemberResponse, TeamTimeResponse
from api.auth import get_current_user, require_csrf

router = APIRouter(prefix="/api/team", tags=["team"], dependencies=[Depends(get_current_user), Depends(require_csrf)])

@router.get("", response_model=List[TeamMemberResponse])
def get_team(db: Session = Depends(get_db)):
    team = db.query(models.TeamMember).all()
    return [{"name": t.name, "time_logged": t.time_logged} for t in team]

@router.post("/time", response_model=TeamTimeResponse)
def update_team_time(
    payload: TeamTime,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    # In a shared-login deployment, we must trust the client-supplied name from
    # localStorage, otherwise all annotators log time against the single shared
    # account.
    name = payload.name
    
    if not name or name == 'Unknown':
        return {"status": "ignored", "time_logged": 0}

    member = db.query(models.TeamMember).filter(models.TeamMember.name == name).first()
    if not member:
        # Create the row so the seconds are never lost.
        member = models.TeamMember(name=name, time_logged=0)
        db.add(member)

    member.time_logged = (member.time_logged or 0) + payload.time_logged
    commit_with_retry(db)
    return {"status": "ok", "time_logged": member.time_logged}

from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import models
from database import get_db, commit_with_retry
from schemas import TeamCreate, TeamUpdate, TeamResponse
from api.auth import get_current_user, require_csrf

router = APIRouter(prefix="/api/teams", tags=["teams"], dependencies=[Depends(get_current_user)])

@router.get("", response_model=List[TeamResponse])
def get_teams(db: Session = Depends(get_db)):
    teams = db.query(models.Team).order_by(models.Team.name).all()
    return teams

@router.post("", response_model=TeamResponse, dependencies=[Depends(require_csrf)])
def create_team(payload: TeamCreate, db: Session = Depends(get_db)):
    existing = db.query(models.Team).filter(models.Team.name == payload.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="A team with this name already exists")
    team = models.Team(name=payload.name)
    db.add(team)
    commit_with_retry(db)
    return team

@router.delete("/{team_id}", dependencies=[Depends(require_csrf)])
def delete_team(team_id: int, db: Session = Depends(get_db)):
    team = db.query(models.Team).filter(models.Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
    
    # Unassign all members from this team
    members = db.query(models.TeamMember).filter(models.TeamMember.team_id == team_id).all()
    for member in members:
        member.team_id = None
        
    db.delete(team)
    commit_with_retry(db)
    return {"status": "ok"}

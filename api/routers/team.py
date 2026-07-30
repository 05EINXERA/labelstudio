from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

import models
from database import get_db, commit_with_retry
from schemas import TeamTime, TeamMemberResponse, TeamTimeResponse, TeamMemberAssign, TeamMemberCreate
from api.auth import get_current_user, require_csrf

router = APIRouter(prefix="/api/team", tags=["team"], dependencies=[Depends(get_current_user)])

@router.get("", response_model=List[TeamMemberResponse])
def get_team(db: Session = Depends(get_db)):
    team = db.query(models.TeamMember, models.Team.name.label("team_name")).outerjoin(
        models.Team, models.TeamMember.team_id == models.Team.id
    ).order_by(models.TeamMember.name).all()
    
    return [
        {
            "name": t.TeamMember.name, 
            "time_logged": t.TeamMember.time_logged,
            "team_id": t.TeamMember.team_id,
            "team_name": t.team_name
        } 
        for t in team
    ]

@router.post("", response_model=TeamMemberResponse, dependencies=[Depends(require_csrf)])
def add_team_member(payload: TeamMemberCreate, db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
        
    existing = db.query(models.TeamMember).filter(models.TeamMember.name == name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Team member already exists")
        
    if payload.team_id is not None:
        team = db.query(models.Team).filter(models.Team.id == payload.team_id).first()
        if not team:
            raise HTTPException(status_code=400, detail="Team not found")
            
    member = models.TeamMember(name=name, time_logged=0, team_id=payload.team_id)
    db.add(member)
    commit_with_retry(db)
    
    team_name = None
    if member.team_id:
        team = db.query(models.Team).filter(models.Team.id == member.team_id).first()
        if team:
            team_name = team.name
            
    return {
        "name": member.name,
        "time_logged": member.time_logged,
        "team_id": member.team_id,
        "team_name": team_name
    }

@router.post("/time", response_model=TeamTimeResponse, dependencies=[Depends(require_csrf)])
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

@router.patch("/{name}", response_model=TeamMemberResponse, dependencies=[Depends(require_csrf)])
def update_team_member(
    name: str,
    payload: TeamMemberAssign,
    db: Session = Depends(get_db),
):
    member = db.query(models.TeamMember).filter(models.TeamMember.name == name).first()
    if not member:
        raise HTTPException(status_code=404, detail="Team member not found")
        
    if payload.team_id is not None:
        team = db.query(models.Team).filter(models.Team.id == payload.team_id).first()
        if not team:
            raise HTTPException(status_code=400, detail="Team not found")
            
    member.team_id = payload.team_id
    commit_with_retry(db)
    
    team_name = None
    if member.team_id:
        team = db.query(models.Team).filter(models.Team.id == member.team_id).first()
        if team:
            team_name = team.name
            
    return {
        "name": member.name,
        "time_logged": member.time_logged,
        "team_id": member.team_id,
        "team_name": team_name
    }

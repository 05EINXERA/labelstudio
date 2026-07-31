from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

import models
from database import get_db, commit_with_retry
from schemas import TeamTime, TeamMemberResponse, TeamTimeResponse, TeamMemberAssign, TeamMemberCreate
from api.auth import get_current_user, require_csrf, get_current_annotator

router = APIRouter(prefix="/api/team", tags=["team"], dependencies=[Depends(get_current_user)])

@router.get("", response_model=List[TeamMemberResponse])
def get_team(db: Session = Depends(get_db), annotator = Depends(get_current_annotator)):
    if annotator is None:
        return []
        
    members_query = db.query(models.TeamMember)
    
    annotator_teams = db.query(models.TeamMemberAssociation.team_id).filter(
        models.TeamMemberAssociation.member_name == annotator.name
    ).all()
    annotator_team_ids = [t[0] for t in annotator_teams]
    
    if not annotator_team_ids:
        return []
        
    # User can only see his team members
    visible_member_names = db.query(models.TeamMemberAssociation.member_name).filter(
        models.TeamMemberAssociation.team_id.in_(annotator_team_ids)
    ).subquery()
    
    members = members_query.filter(
        models.TeamMember.name.in_(db.query(visible_member_names.c.member_name))
    ).order_by(models.TeamMember.name).all()
    
    if not members:
        return []
        
    member_names = [m.name for m in members]
    associations = db.query(models.TeamMemberAssociation, models.Team).join(
        models.Team, models.TeamMemberAssociation.team_id == models.Team.id
    ).filter(
        models.TeamMemberAssociation.member_name.in_(member_names)
    ).all()
    
    teams_by_member = {name: [] for name in member_names}
    for assoc, team in associations:
        teams_by_member[assoc.member_name].append({
            "id": team.id,
            "name": team.name,
            "creator": team.creator,
            "created_at": team.created_at
        })
        
    return [
        {
            "name": m.name, 
            "time_logged": m.time_logged,
            "teams": teams_by_member[m.name]
        } 
        for m in members
    ]

@router.post("", response_model=TeamMemberResponse, dependencies=[Depends(require_csrf)])
def add_team_member(payload: TeamMemberCreate, request: Request, db: Session = Depends(get_db), annotator = Depends(get_current_annotator)):
    if not annotator:
        raise HTTPException(status_code=403, detail="Must be logged in as an annotator")
        
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
        
    registered_user = db.query(models.User).filter(models.User.username == name).first()
    if not registered_user:
        raise HTTPException(status_code=400, detail="Only registered users can be added as team members")
        
    annotator_teams = db.query(models.Team).filter(models.Team.creator == annotator.name).all()
    owned_team_ids = {t.id for t in annotator_teams}
    
    if payload.team_ids:
        unowned_adds = set(payload.team_ids) - owned_team_ids
        if unowned_adds:
            raise HTTPException(status_code=403, detail="You can only add members to teams you created")
            
    existing = db.query(models.TeamMember).filter(models.TeamMember.name == name).first()
    if not existing:
        member = models.TeamMember(name=name, time_logged=0)
        db.add(member)
    else:
        member = existing
    
    if payload.team_ids:
        db_teams = db.query(models.Team).filter(models.Team.id.in_(payload.team_ids)).all()
        if len(db_teams) != len(payload.team_ids):
            raise HTTPException(status_code=400, detail="One or more teams not found")
            
        current_associations = set([a[0] for a in db.query(models.TeamMemberAssociation.team_id).filter(
            models.TeamMemberAssociation.member_name == name
        ).all()])
            
        for t in db_teams:
            if t.id not in current_associations:
                assoc = models.TeamMemberAssociation(member_name=name, team_id=t.id)
                db.add(assoc)
            
    commit_with_retry(db)
    
    # Return all teams they are in
    all_teams_for_member = db.query(models.Team).join(
        models.TeamMemberAssociation, models.TeamMemberAssociation.team_id == models.Team.id
    ).filter(models.TeamMemberAssociation.member_name == name).all()
    
    teams_resp = [{
        "id": t.id,
        "name": t.name,
        "creator": t.creator,
        "created_at": t.created_at
    } for t in all_teams_for_member]
    
    return {
        "name": member.name,
        "time_logged": member.time_logged,
        "teams": teams_resp
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
    request: Request,
    db: Session = Depends(get_db),
    annotator = Depends(get_current_annotator)
):
    if not annotator:
        raise HTTPException(status_code=403, detail="Must be logged in as an annotator")
        
    member = db.query(models.TeamMember).filter(models.TeamMember.name == name).first()
    if not member:
        raise HTTPException(status_code=404, detail="Team member not found")
        
    annotator_teams = db.query(models.Team).filter(models.Team.creator == annotator.name).all()
    owned_team_ids = {t.id for t in annotator_teams}
    
    current_associations = db.query(models.TeamMemberAssociation).filter(models.TeamMemberAssociation.member_name == name).all()
    current_team_ids = {a.team_id for a in current_associations}
    
    new_team_ids = set(payload.team_ids) if payload.team_ids else set()
    
    added_teams = new_team_ids - current_team_ids
    if added_teams - owned_team_ids:
        raise HTTPException(status_code=403, detail="You can only add members to teams you created")
        
    final_team_ids = (current_team_ids - owned_team_ids) | (new_team_ids & owned_team_ids)
        
    # Remove existing associations
    db.query(models.TeamMemberAssociation).filter(models.TeamMemberAssociation.member_name == name).delete()
    
    teams = []
    if final_team_ids:
        db_teams = db.query(models.Team).filter(models.Team.id.in_(final_team_ids)).all()
        for t in db_teams:
            assoc = models.TeamMemberAssociation(member_name=name, team_id=t.id)
            db.add(assoc)
            teams.append({
                "id": t.id,
                "name": t.name,
                "creator": t.creator,
                "created_at": t.created_at
            })
            
    commit_with_retry(db)
    
    return {
        "name": member.name,
        "time_logged": member.time_logged,
        "teams": teams
    }

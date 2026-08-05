from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

import models
from database import get_db, commit_with_retry
from schemas import TeamCreate, TeamUpdate, TeamResponse, TeamTransferOwnership
from api.auth import get_current_user, require_csrf, get_current_annotator

router = APIRouter(prefix="/api/teams", tags=["teams"], dependencies=[Depends(get_current_user)])

@router.get("", response_model=List[TeamResponse])
def get_teams(db: Session = Depends(get_db), annotator = Depends(get_current_annotator)):
    if annotator is None:
        return []
        
    query = db.query(models.Team)
    annotator_team_ids = [t[0] for t in db.query(models.TeamMemberAssociation.team_id).filter(
        models.TeamMemberAssociation.member_name == annotator.name
    ).all()]
    
    query = query.filter(
        (models.Team.id.in_(annotator_team_ids)) | 
        (models.Team.creator == annotator.name)
    )
    teams = query.order_by(models.Team.name).all()
    return teams

@router.post("", response_model=TeamResponse, dependencies=[Depends(require_csrf)])
def create_team(payload: TeamCreate, request: Request, db: Session = Depends(get_db), annotator = Depends(get_current_annotator)):
    annotator_name = request.headers.get("X-Annotator-Name")
    if not annotator_name:
        raise HTTPException(status_code=401, detail="Missing X-Annotator-Name header")
        
    existing_name = db.query(models.Team).filter(models.Team.name == payload.name).first()
    if existing_name:
        raise HTTPException(status_code=400, detail="A team with this name already exists")

    team = models.Team(name=payload.name, creator=annotator_name)
    db.add(team)
    
    # Need to commit the team to get its ID first
    commit_with_retry(db)
    db.refresh(team)
    
    if annotator is not None:
        # Assign them to this team
        assoc = models.TeamMemberAssociation(member_name=annotator.name, team_id=team.id)
        db.add(assoc)
        commit_with_retry(db)
    else:
        # If the user doesn't exist yet but we know their name, create them and assign them
        if annotator_name:
            new_annotator = models.TeamMember(name=annotator_name, time_logged=0)
            db.add(new_annotator)
            assoc = models.TeamMemberAssociation(member_name=annotator_name, team_id=team.id)
            db.add(assoc)
            commit_with_retry(db)
        
    return team

@router.patch("/{team_id}/transfer-ownership", response_model=TeamResponse, dependencies=[Depends(require_csrf)])
@router.post("/{team_id}/transfer-ownership", response_model=TeamResponse, dependencies=[Depends(require_csrf)])
def transfer_team_ownership(
    team_id: int,
    payload: TeamTransferOwnership,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    annotator = Depends(get_current_annotator),
):
    team = db.query(models.Team).filter(models.Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")

    caller_names = set()
    if annotator:
        caller_names.add(annotator.name)
    if user:
        caller_names.add(user.username)
    header_name = request.headers.get("X-Annotator-Name")
    if header_name:
        caller_names.add(header_name)

    if not team.creator or team.creator not in caller_names:
        raise HTTPException(status_code=403, detail="Only the team creator can transfer ownership")

    new_owner = payload.new_owner.strip()
    if not new_owner:
        raise HTTPException(status_code=400, detail="New owner name cannot be empty")

    if new_owner == team.creator:
        raise HTTPException(status_code=400, detail="User is already the team creator")

    target_user = db.query(models.User).filter(models.User.username == new_owner).first()
    target_member = db.query(models.TeamMember).filter(models.TeamMember.name == new_owner).first()
    if not target_user and not target_member:
        raise HTTPException(status_code=400, detail="Target user does not exist")

    if not target_member:
        target_member = models.TeamMember(name=new_owner, time_logged=0)
        db.add(target_member)

    existing_assoc = db.query(models.TeamMemberAssociation).filter(
        models.TeamMemberAssociation.team_id == team.id,
        models.TeamMemberAssociation.member_name == new_owner
    ).first()
    if not existing_assoc:
        assoc = models.TeamMemberAssociation(member_name=new_owner, team_id=team.id)
        db.add(assoc)

    team.creator = new_owner

    # Transfer ownership of all projects belonging to this team
    team_projects = db.query(models.Project).filter(models.Project.team_id == team.id).all()
    for proj in team_projects:
        proj.creator = new_owner
        if target_user:
            proj.owner_id = target_user.id

    commit_with_retry(db)
    db.refresh(team)
    return team

@router.delete("/{team_id}", dependencies=[Depends(require_csrf)])
def delete_team(team_id: int, request: Request, db: Session = Depends(get_db)):
    team = db.query(models.Team).filter(models.Team.id == team_id).first()
    if not team:
        raise HTTPException(status_code=404, detail="Team not found")
        
    # X-Annotator-Name is used for UI filtering but is not a security boundary.
    
    # Unassign all members from this team by deleting associations
    db.query(models.TeamMemberAssociation).filter(models.TeamMemberAssociation.team_id == team_id).delete()
        
    db.delete(team)
    commit_with_retry(db)
    return {"status": "ok"}

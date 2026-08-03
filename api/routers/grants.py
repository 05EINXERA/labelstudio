"""Project grants — which teams may work on a project, and in what role.

Implements .devnotes/teams/03_API.md § 3. A grant is the access boundary: it is
what makes a project visible and writable to anyone other than its owner
(01_DESIGN.md § 3.1). Task-level assignment is a separate, narrower thing —
distributing work *within* a project both teams can already reach.

**Granting is owner-only, not manager.** A project manager who could grant could
grant their own team `manager` elsewhere, or add teams the owner never intended.
Privilege-granting is the one power that must not be delegable without an
explicit ownership transfer (§ 3).

Mounted under `/api/projects/{project_id}/grants` because a grant is a property
of a project, not a free-standing resource.
"""
import logging
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
from api.auth import get_current_user, require_csrf
from api.permissions import ProjectRole, require_project, require_team
from api.permissions import TeamRole
from database import commit_with_retry, get_db
from schemas import (
    AssignableMember,
    GrantCreate,
    GrantOut,
    GrantRevokeResult,
    GrantRoleUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/projects",
    tags=["grants"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)


def _grant_out(grant: models.ProjectGrant, team: models.Team) -> GrantOut:
    return GrantOut(
        project_id=grant.project_id,
        team_id=grant.team_id,
        team_name=team.name if team else None,
        team_slug=team.slug if team else None,
        role=grant.role,
        granted_by=grant.granted_by,
        created_at=grant.created_at,
    )


def _rows(db: Session, project_id: int) -> List[GrantOut]:
    rows = (
        db.query(models.ProjectGrant, models.Team)
        .join(models.Team, models.Team.id == models.ProjectGrant.team_id)
        .filter(models.ProjectGrant.project_id == project_id)
        .order_by(models.Team.name)
        .all()
    )
    return [_grant_out(grant, team) for grant, team in rows]


@router.get("/{project_id}/grants", response_model=List[GrantOut])
def list_grants(
    project_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Teams granted on this project.

    Visible to any project member, not just the owner: people working on a
    project can reasonably see who else is on it, and it is the only way a
    manager can tell whether a team they want to assign work to has access.
    """
    require_project(project_id, user, db, minimum=ProjectRole.VIEWER)
    return _rows(db, project_id)


@router.post("/{project_id}/grants", response_model=GrantOut)
def create_grant(
    project_id: int,
    payload: GrantCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Grant a team access to this project, or update its role.

    **Upsert, not insert.** One row per (project, team): re-granting a different
    role updates in place. A second row would leave the revoked higher role
    alive through the resolver's max-over-grants (02_SCHEMA.md § 4).
    """
    require_project(project_id, user, db, minimum=ProjectRole.OWNER)
    # E-16: you may only grant to a team you are in. Otherwise a project owner
    # could probe which team ids exist by watching this endpoint's status codes.
    # require_team 404s for non-members, identical to a nonexistent id.
    team = require_team(payload.team_id, user, db, minimum=TeamRole.MEMBER)

    existing = (
        db.query(models.ProjectGrant)
        .filter(
            models.ProjectGrant.project_id == project_id,
            models.ProjectGrant.team_id == payload.team_id,
        )
        .first()
    )
    if existing is not None:
        existing.role = payload.role
        existing.granted_by = user.id
        commit_with_retry(db)
        return _grant_out(existing, team)

    grant = models.ProjectGrant(
        project_id=project_id,
        team_id=payload.team_id,
        role=payload.role,
        granted_by=user.id,
    )
    db.add(grant)
    try:
        commit_with_retry(db)
    except IntegrityError:
        # Two owners granting the same team at once. The unique constraint is
        # the real defence; re-read and apply the requested role so the caller's
        # intent still wins rather than returning an error for a race.
        db.rollback()
        existing = (
            db.query(models.ProjectGrant)
            .filter(
                models.ProjectGrant.project_id == project_id,
                models.ProjectGrant.team_id == payload.team_id,
            )
            .first()
        )
        if existing is None:
            raise
        existing.role = payload.role
        existing.granted_by = user.id
        commit_with_retry(db)
        return _grant_out(existing, team)

    db.refresh(grant)
    return _grant_out(grant, team)


@router.patch("/{project_id}/grants/{team_id}", response_model=GrantOut)
def update_grant(
    project_id: int,
    team_id: int,
    payload: GrantRoleUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    require_project(project_id, user, db, minimum=ProjectRole.OWNER)

    grant = (
        db.query(models.ProjectGrant)
        .filter(
            models.ProjectGrant.project_id == project_id,
            models.ProjectGrant.team_id == team_id,
        )
        .first()
    )
    if grant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That team does not have access to this project.",
        )

    grant.role = payload.role
    grant.granted_by = user.id
    commit_with_retry(db)
    return _grant_out(grant, db.get(models.Team, team_id))


@router.delete("/{project_id}/grants/{team_id}", response_model=GrantRevokeResult)
def revoke_grant(
    project_id: int,
    team_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Revoke a team's access to this project.

    Also returns that team's tasks *on this project* to the shared pool (E-08).
    Leaving them assigned would point at a team that can no longer see the
    project — invisible work, the worst kind of silent failure. Tasks and their
    annotations are never deleted.

    The revoke bites on the member's very next request: the resolver has no
    cross-request cache. Their unsaved work survives in the per-task
    localStorage draft (rule 18).
    """
    require_project(project_id, user, db, minimum=ProjectRole.OWNER)

    grant = (
        db.query(models.ProjectGrant)
        .filter(
            models.ProjectGrant.project_id == project_id,
            models.ProjectGrant.team_id == team_id,
        )
        .first()
    )
    if grant is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That team does not have access to this project.",
        )

    tasks_unassigned = (
        db.query(models.Task)
        .filter(
            models.Task.project_id == project_id,
            models.Task.assigned_team_id == team_id,
        )
        .update({models.Task.assigned_team_id: None}, synchronize_session=False)
    )
    db.delete(grant)
    commit_with_retry(db)

    return GrantRevokeResult(status="ok", tasks_unassigned=tasks_unassigned)


@router.get("/{project_id}/assignable-members", response_model=List[AssignableMember])
def list_assignable_members(
    project_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Everyone who can be assigned a task on this project.

    That is: the members of every team holding a grant here, deduplicated —
    someone in two granted teams is one person. Each row carries the team they
    were reached through so the Tasks view can group the picker by team, and so
    assigning "a member of Team Alpha" is a visible choice rather than a flat
    list of names.

    **Why this endpoint exists rather than reusing `/api/teams/{id}/members`:**
    the caller assigning work is the project owner or a project manager, who is
    not necessarily a member of the teams they are distributing to — and
    `/api/teams/{id}/members` deliberately 404s for non-members (E-16). Scoping
    the disclosure to "people already granted access to a project you manage"
    keeps that anti-enumeration property intact while making assignment
    possible.

    Readable at `viewer` so the Tasks view can render an Assignee column with
    real names for everyone, not just managers.
    """
    require_project(project_id, user, db, minimum=ProjectRole.VIEWER)

    rows = (
        db.query(
            models.User.id,
            models.User.username,
            models.Team.id,
            models.Team.name,
            models.TeamMembership.role,
        )
        .join(models.TeamMembership, models.TeamMembership.user_id == models.User.id)
        .join(models.Team, models.Team.id == models.TeamMembership.team_id)
        .join(models.ProjectGrant, models.ProjectGrant.team_id == models.Team.id)
        .filter(models.ProjectGrant.project_id == project_id)
        .order_by(models.Team.name, models.User.username)
        .all()
    )

    # Deduplicate on user id, keeping the first team encountered. A person in
    # two granted teams should appear once; which team is shown is cosmetic.
    seen = set()
    members = []
    for user_id, username, team_id, team_name, team_role in rows:
        if user_id in seen:
            continue
        seen.add(user_id)
        members.append(
            AssignableMember(
                user_id=user_id,
                username=username,
                team_id=team_id,
                team_name=team_name,
                team_role=team_role,
            )
        )
    return members

"""Team management — create teams, manage their rosters.

Implements .devnotes/teams/03_API.md § 2. This router covers the *team* axis
only: who is in a team and what they may do to the team itself. What a team may
do on a project is the grant axis and lands in Phase 3
(.devnotes/teams/01_DESIGN.md § 2) — nothing here changes project access.

Two things to keep in mind when editing:

- **A team always has exactly one owner.** `Team.owner_id` and the `owner`
  membership row are two representations of that fact and must move together,
  in one transaction (E-05, E-23).
- **The delete cascade is written out explicitly** rather than left to the
  database, because SQLite only enforces `ondelete` with
  `PRAGMA foreign_keys=ON` and the tasks `SET NULL` is far too important to
  depend on a pragma (§ 4.4).
"""
import logging
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
from api.auth import get_current_user, require_csrf
from api.permissions import TeamRole, require_team
from api.rate_limit import check_rate_limit
from config import (
    ADD_MEMBER_RATE_LIMIT,
    ADD_MEMBER_RATE_WINDOW_SECONDS,
    MAX_TEAMS_PER_USER,
)
from database import commit_with_retry, get_db
from schemas import (
    TeamCreate,
    TeamDeleteResult,
    TeamDetail,
    TeamMemberAdd,
    TeamMemberOut,
    TeamMemberRoleUpdate,
    TeamProjectOut,
    TeamSummary,
    TeamUpdate,
    TransferOwnership,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/teams",
    tags=["teams"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)

_TEAM_RANK = {TeamRole.MEMBER: 0, TeamRole.MANAGER: 1, TeamRole.OWNER: 2}

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _slugify(name: str) -> str:
    """A URL-safe stem for `name`. Never empty."""
    slug = _SLUG_STRIP.sub("-", name.lower()).strip("-")
    # A name of only punctuation ("!!!") would otherwise produce an empty slug,
    # which is a uniqueness key the next such team would collide with.
    return slug[:120] or "team"


def _unique_slug(db: Session, name: str, exclude_team_id: Optional[int] = None) -> str:
    """`_slugify(name)` with a numeric suffix if that is already taken.

    Collisions are resolved server-side rather than surfaced: two people naming
    a team "Reviewers" is normal, and a 409 for something the user did not type
    and cannot fix is a dead end (.devnotes/teams/02_SCHEMA.md § 2).
    """
    stem = _slugify(name)
    query = db.query(models.Team.slug).filter(models.Team.slug.like(f"{stem}%"))
    if exclude_team_id is not None:
        query = query.filter(models.Team.id != exclude_team_id)
    taken = {slug for (slug,) in query.all()}

    if stem not in taken:
        return stem
    suffix = 2
    while f"{stem}-{suffix}" in taken:
        suffix += 1
    return f"{stem}-{suffix}"


def _member_rows(db: Session, team_id: int) -> List[TeamMemberOut]:
    rows = (
        db.query(models.TeamMembership, models.User.username)
        .join(models.User, models.User.id == models.TeamMembership.user_id)
        .filter(models.TeamMembership.team_id == team_id)
        .order_by(models.TeamMembership.created_at)
        .all()
    )
    return [
        TeamMemberOut(
            user_id=m.user_id,
            username=username,
            role=m.role,
            added_by=m.added_by,
            created_at=m.created_at,
        )
        for m, username in rows
    ]


def _project_rows(db: Session, team_id: int) -> List[TeamProjectOut]:
    rows = (
        db.query(models.ProjectGrant, models.Project)
        .join(models.Project, models.Project.id == models.ProjectGrant.project_id)
        .filter(models.ProjectGrant.team_id == team_id)
        .all()
    )
    return [
        TeamProjectOut(
            project_id=grant.project_id,
            name=project.name,
            slug=project.slug,
            role=grant.role,
        )
        for grant, project in rows
    ]


def _summary(db: Session, team: models.Team, my_role: str) -> TeamSummary:
    member_count = (
        db.query(func.count(models.TeamMembership.id))
        .filter(models.TeamMembership.team_id == team.id)
        .scalar()
    ) or 0
    project_count = (
        db.query(func.count(models.ProjectGrant.id))
        .filter(models.ProjectGrant.team_id == team.id)
        .scalar()
    ) or 0
    return TeamSummary(
        id=team.id,
        name=team.name,
        slug=team.slug,
        description=team.description,
        my_role=my_role,
        is_owner=my_role == TeamRole.OWNER.value,
        member_count=member_count,
        project_count=project_count,
        created_at=team.created_at,
    )


@router.get("", response_model=List[TeamSummary])
def list_teams(
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Teams the caller belongs to — never every team on the instance."""
    rows = (
        db.query(models.Team, models.TeamMembership.role)
        .join(
            models.TeamMembership,
            models.TeamMembership.team_id == models.Team.id,
        )
        .filter(models.TeamMembership.user_id == user.id)
        .order_by(models.Team.name)
        .all()
    )
    return [_summary(db, team, role) for team, role in rows]


@router.post("", response_model=TeamDetail, status_code=status.HTTP_201_CREATED)
def create_team(
    payload: TeamCreate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Create a team. The caller becomes its owner.

    The `owner` membership row is written in the same transaction as the team:
    a team whose owner is not a member of it would be invisible in every roster
    query and unmanageable through the members endpoints.
    """
    owned = (
        db.query(func.count(models.Team.id))
        .filter(models.Team.owner_id == user.id)
        .scalar()
    ) or 0
    if owned >= MAX_TEAMS_PER_USER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"You may own at most {MAX_TEAMS_PER_USER} teams.",
        )

    team = models.Team(
        name=payload.name,
        slug=_unique_slug(db, payload.name),
        description=payload.description,
        owner_id=user.id,
    )
    db.add(team)
    db.flush()

    db.add(
        models.TeamMembership(
            team_id=team.id,
            user_id=user.id,
            role=TeamRole.OWNER.value,
            added_by=user.id,
        )
    )
    commit_with_retry(db)
    db.refresh(team)

    return TeamDetail(
        **_summary(db, team, TeamRole.OWNER.value).model_dump(),
        members=_member_rows(db, team.id),
        projects=_project_rows(db, team.id),
    )


@router.get("/{team_id}", response_model=TeamDetail)
def get_team(
    team_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    team = require_team(team_id, user, db, minimum=TeamRole.MEMBER)
    my_role = _my_role(db, team_id, user)
    return TeamDetail(
        **_summary(db, team, my_role).model_dump(),
        members=_member_rows(db, team.id),
        projects=_project_rows(db, team.id),
    )


@router.patch("/{team_id}", response_model=TeamSummary)
def update_team(
    team_id: int,
    payload: TeamUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    team = require_team(team_id, user, db, minimum=TeamRole.MANAGER)

    if payload.name is not None:
        team.name = payload.name
        # Re-slug on rename so the URL keeps matching the name, still resolving
        # collisions rather than raising one at the user.
        team.slug = _unique_slug(db, payload.name, exclude_team_id=team.id)
    if payload.description is not None:
        team.description = payload.description

    commit_with_retry(db)
    db.refresh(team)
    return _summary(db, team, _my_role(db, team_id, user))


@router.delete("/{team_id}", response_model=TeamDeleteResult)
def delete_team(
    team_id: int,
    confirm: str = Query(..., description="The team's slug, typed to confirm"),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Delete a team, its grants and its memberships. Owner only.

    Requires `?confirm=<slug>`: deleting a team revokes project access for
    everyone in it and returns its tasks to the shared pool, which is not
    something to do on a mis-click (E-06).

    The cascade is explicit, in this order, in one transaction:
    grants → tasks SET NULL → memberships → team. Annotations are never touched.
    """
    team = require_team(team_id, user, db, minimum=TeamRole.OWNER)

    if confirm != team.slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Type the team's slug ('{team.slug}') to confirm deletion.",
        )

    grants_removed = (
        db.query(models.ProjectGrant)
        .filter(models.ProjectGrant.team_id == team_id)
        .delete(synchronize_session=False)
    )
    # SET NULL, never delete: a task assigned to this team returns to the shared
    # pool. Deleting them here would destroy annotation work.
    tasks_unassigned = (
        db.query(models.Task)
        .filter(models.Task.assigned_team_id == team_id)
        .update({models.Task.assigned_team_id: None}, synchronize_session=False)
    )
    members_removed = (
        db.query(models.TeamMembership)
        .filter(models.TeamMembership.team_id == team_id)
        .delete(synchronize_session=False)
    )
    db.delete(team)
    commit_with_retry(db)

    return TeamDeleteResult(
        status="ok",
        grants_removed=grants_removed,
        tasks_unassigned=tasks_unassigned,
        members_removed=members_removed,
    )


@router.get("/{team_id}/members", response_model=List[TeamMemberOut])
def list_members(
    team_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    require_team(team_id, user, db, minimum=TeamRole.MEMBER)
    return _member_rows(db, team_id)


@router.post("/{team_id}/members", response_model=TeamMemberOut)
def add_member(
    team_id: int,
    payload: TeamMemberAdd,
    request: Request,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Add a user to the team by exact username.

    Rate limited per caller: this endpoint necessarily discloses whether a
    username exists, and the limit is what keeps that a scoped disclosure rather
    than a bulk enumeration oracle (E-14).
    """
    team = require_team(team_id, user, db, minimum=TeamRole.MANAGER)

    allowed, retry_after = check_rate_limit(
        "add_member", user.id, ADD_MEMBER_RATE_LIMIT, ADD_MEMBER_RATE_WINDOW_SECONDS
    )
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many members added too quickly. Try again shortly.",
            headers={"Retry-After": str(retry_after)},
        )

    _guard_role_escalation(db, team_id, user, payload.role)

    target = (
        db.query(models.User)
        .filter(models.User.username == payload.username)
        .first()
    )
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No user named '{payload.username}'.",
        )

    membership = models.TeamMembership(
        team_id=team.id,
        user_id=target.id,
        role=payload.role,
        added_by=user.id,
    )
    db.add(membership)
    try:
        commit_with_retry(db)
    except IntegrityError:
        # E-01/E-21: already a member. Adding someone twice is a double-click,
        # not an error — the caller's intent is already satisfied. Handled here
        # rather than with a "does this row exist" pre-check, which two managers
        # acting at once would both pass.
        db.rollback()
        existing = (
            db.query(models.TeamMembership)
            .filter(
                models.TeamMembership.team_id == team.id,
                models.TeamMembership.user_id == target.id,
            )
            .first()
        )
        if existing is None:
            raise
        return TeamMemberOut(
            user_id=target.id,
            username=target.username,
            role=existing.role,
            added_by=existing.added_by,
            created_at=existing.created_at,
        )

    db.refresh(membership)
    return TeamMemberOut(
        user_id=target.id,
        username=target.username,
        role=membership.role,
        added_by=membership.added_by,
        created_at=membership.created_at,
    )


@router.patch("/{team_id}/members/{user_id}", response_model=TeamMemberOut)
def update_member_role(
    team_id: int,
    user_id: int,
    payload: TeamMemberRoleUpdate,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Change a member's role. Never to `owner` — that is transfer-only."""
    team = require_team(team_id, user, db, minimum=TeamRole.MANAGER)
    _guard_role_escalation(db, team_id, user, payload.role)

    membership = _get_membership(db, team_id, user_id)

    if membership.role == TeamRole.OWNER.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transfer ownership to change the owner's role.",
        )

    membership.role = payload.role
    commit_with_retry(db)

    target = db.get(models.User, user_id)
    return TeamMemberOut(
        user_id=user_id,
        username=target.username if target else "",
        role=membership.role,
        added_by=membership.added_by,
        created_at=membership.created_at,
    )


@router.delete("/{team_id}/members/me")
def leave_team(
    team_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Remove yourself from a team.

    The safety valve that makes "a manager adds you with no acceptance step"
    acceptable (.devnotes/teams/01_DESIGN.md § 5.2): nobody is trapped in a team.
    The owner is the exception and must transfer first — a team always has
    exactly one owner.
    """
    require_team(team_id, user, db, minimum=TeamRole.MEMBER)
    membership = _get_membership(db, team_id, user.id)

    if membership.role == TeamRole.OWNER.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transfer ownership before leaving this team.",
        )

    db.delete(membership)
    commit_with_retry(db)
    return {"status": "ok"}


@router.delete("/{team_id}/members/{user_id}")
def remove_member(
    team_id: int,
    user_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Remove someone else from the team. Managers are peers and may remove
    each other (E-04); only the owner is protected."""
    require_team(team_id, user, db, minimum=TeamRole.MANAGER)
    membership = _get_membership(db, team_id, user_id)

    if membership.role == TeamRole.OWNER.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The team owner cannot be removed. Transfer ownership first.",
        )

    db.delete(membership)
    commit_with_retry(db)
    return {"status": "ok"}


@router.post("/{team_id}/transfer", response_model=TeamDetail)
def transfer_ownership(
    team_id: int,
    payload: TransferOwnership,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Hand ownership to another member; the caller becomes a manager.

    `Team.owner_id` and both membership rows change in **one** transaction
    (E-05, E-23). A partial application here is what "two owners" or "no owner"
    looks like, and the denormalised `owner_id` exists precisely so that
    invariant has one authoritative home.
    """
    team = require_team(team_id, user, db, minimum=TeamRole.OWNER)

    target = (
        db.query(models.User)
        .filter(models.User.username == payload.username)
        .first()
    )
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No user named '{payload.username}'.",
        )
    if target.id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You already own this team.",
        )

    new_owner_membership = (
        db.query(models.TeamMembership)
        .filter(
            models.TeamMembership.team_id == team_id,
            models.TeamMembership.user_id == target.id,
        )
        .first()
    )
    if new_owner_membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"'{payload.username}' is not a member of this team.",
        )

    old_owner_membership = _get_membership(db, team_id, user.id)

    new_owner_membership.role = TeamRole.OWNER.value
    old_owner_membership.role = TeamRole.MANAGER.value
    team.owner_id = target.id
    commit_with_retry(db)
    db.refresh(team)

    return TeamDetail(
        **_summary(db, team, TeamRole.MANAGER.value).model_dump(),
        members=_member_rows(db, team.id),
        projects=_project_rows(db, team.id),
    )


@router.get("/{team_id}/projects", response_model=List[TeamProjectOut])
def list_team_projects(
    team_id: int,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    require_team(team_id, user, db, minimum=TeamRole.MEMBER)
    return _project_rows(db, team_id)


# --- helpers -----------------------------------------------------------------


def _my_role(db: Session, team_id: int, user: models.User) -> str:
    membership = (
        db.query(models.TeamMembership)
        .filter(
            models.TeamMembership.team_id == team_id,
            models.TeamMembership.user_id == user.id,
        )
        .first()
    )
    return membership.role if membership else TeamRole.MEMBER.value


def _get_membership(db: Session, team_id: int, user_id: int) -> models.TeamMembership:
    membership = (
        db.query(models.TeamMembership)
        .filter(
            models.TeamMembership.team_id == team_id,
            models.TeamMembership.user_id == user_id,
        )
        .first()
    )
    if membership is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="That user is not a member of this team.",
        )
    return membership


def _guard_role_escalation(
    db: Session, team_id: int, user: models.User, target_role: str
) -> None:
    """E-03: nobody may grant a role above their own.

    Without this a manager can self-escalate by proxy — promote an accomplice to
    owner, or simply mint another manager and have them do what the manager
    could not. The Pydantic `Literal` already blocks `owner`; this covers the
    remaining case of a manager assigning `manager`.
    """
    caller_role = _my_role(db, team_id, user)
    if _TEAM_RANK[TeamRole(target_role)] > _TEAM_RANK[TeamRole(caller_role)]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"You cannot grant a role above your own ({caller_role}).",
        )

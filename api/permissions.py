"""The permission resolver — the single place that decides who may do what.

Two orthogonal role axes (.devnotes/teams/01_DESIGN.md § 2):

- **`ProjectRole`** — what you may do on one project. Reached by being the
  project's owner, or through a `ProjectGrant` on a team you are a member of.
- **`TeamRole`** — your standing inside a team (add members, rename it). It says
  nothing about annotation: owning a team grants nothing on projects that team
  can reach.

This module replaces `get_owned_project` / `_get_owned_task`, mirroring their
shape (return the row, or raise) so the call-site migration is mechanical rather
than a rewrite.

**Dependency direction** (docs/ARCHITECTURE.md § 2): this imports `models`,
`database` and `fastapi` and *nothing* from `api/routers/`. Routers import this;
never the reverse.

**Client-side mirror:** `frontend/js/permissions.js` duplicates the `_RANK` and
`_TEAM_RANK` orderings below, helper for helper. The duplication is deliberate —
there is no build step to share a module across both — and that file is for
**rendering only**: it answers "should I draw this button?", never "is this
allowed?".

**This module is the security boundary.** Never remove a check here because the
client already hides the control; a stale JS bundle is a cosmetic bug (E-17),
a missing server check is a vulnerability. If you change the ranking here,
change `frontend/js/permissions.js` to match.
"""
from enum import Enum
from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

import models
from schemas import is_approved


class ProjectRole(str, Enum):
    """What a user may do on one project. Ordered by `_RANK` below."""

    VIEWER = "viewer"
    ANNOTATOR = "annotator"
    REVIEWER = "reviewer"
    MANAGER = "manager"
    OWNER = "owner"


class TeamRole(str, Enum):
    """A user's standing inside a team. Unrelated to project access."""

    MEMBER = "member"
    MANAGER = "manager"
    OWNER = "owner"


# The ranking lives here as a plain dict rather than as a SQL CASE expression:
# it is a code-level ordering of string values, and encoding it in SQL too would
# put the truth in two places that can drift. Kept in sync by hand with
# frontend/js/permissions.js.
_RANK = {
    ProjectRole.VIEWER: 0,
    ProjectRole.ANNOTATOR: 1,
    ProjectRole.REVIEWER: 2,
    ProjectRole.MANAGER: 3,
    ProjectRole.OWNER: 4,
}

_TEAM_RANK = {
    TeamRole.MEMBER: 0,
    TeamRole.MANAGER: 1,
    TeamRole.OWNER: 2,
}

# Human-readable role names for 403 messages. A permission error the user cannot
# act on is a support ticket, so the message always names the role required.
_ROLE_LABEL = {
    ProjectRole.VIEWER: "Viewer",
    ProjectRole.ANNOTATOR: "Annotator",
    ProjectRole.REVIEWER: "Reviewer",
    ProjectRole.MANAGER: "Manager",
    ProjectRole.OWNER: "Owner",
}

_TEAM_ROLE_LABEL = {
    TeamRole.MEMBER: "Member",
    TeamRole.MANAGER: "Manager",
    TeamRole.OWNER: "Owner",
}

_CACHE_ATTR = "__perm_cache__"


def rank(role: ProjectRole) -> int:
    """Comparable rank of a project role."""
    return _RANK[ProjectRole(role)]


def at_least(role: Optional[ProjectRole], minimum: ProjectRole) -> bool:
    """True when `role` is present and ranks at or above `minimum`."""
    if role is None:
        return False
    return _RANK[ProjectRole(role)] >= _RANK[ProjectRole(minimum)]


def _cache(request: Optional[Request]) -> Optional[dict]:
    """The per-request role cache, created on first use.

    **Request-scoped only.** Caching across requests would break the property
    that a revoked grant takes effect immediately (.devnotes/teams/01_DESIGN.md
    § 6.3, E-08) — the same reason `get_current_user` re-reads the user row every
    request rather than trusting the token's claims.
    """
    if request is None:
        return None
    cache = getattr(request.state, _CACHE_ATTR, None)
    if cache is None:
        cache = {}
        setattr(request.state, _CACHE_ATTR, cache)
    return cache


def effective_project_role(
    user: models.User,
    project_id: int,
    db: Session,
    request: Optional[Request] = None,
) -> Optional[ProjectRole]:
    """The user's role on one project, or None for "no access at all".

    Resolution order (.devnotes/teams/01_DESIGN.md § 2.3):

    1. **Owner short-circuit.** Must be first: an owner who is also in a team
       holding a lower grant is never demoted by it (E-13).
    2. **Maximum over grants** reachable through the user's team memberships.
       Max, not minimum — "most restrictive wins" produces the baffling outcome
       that joining an extra team *reduces* your access.
    3. **`visibility == "org"`** floors the result at VIEWER. Applied *after*
       the grant maximum, and it never implies write access.
    """
    cache = _cache(request)
    if cache is not None and project_id in cache:
        return cache[project_id]

    role = _resolve_project_role(user, project_id, db)

    if cache is not None:
        cache[project_id] = role
    return role


def _resolve_project_role(
    user: models.User, project_id: int, db: Session
) -> Optional[ProjectRole]:
    project = db.get(models.Project, project_id)
    if project is None:
        return None

    if project.owner_id is not None and project.owner_id == user.id:
        return ProjectRole.OWNER

    grant_roles = [
        row[0]
        for row in db.query(models.ProjectGrant.role)
        .join(
            models.TeamMembership,
            models.TeamMembership.team_id == models.ProjectGrant.team_id,
        )
        .filter(
            models.ProjectGrant.project_id == project_id,
            models.TeamMembership.user_id == user.id,
        )
        .all()
    ]

    # The max is taken in Python, not SQL: the row count here is tiny (grants on
    # one project ∩ this user's memberships), and a SQL CASE would duplicate the
    # ranking that _RANK already owns. Unknown role strings are skipped rather
    # than crashing the request — a bad row must not lock a user out of a
    # project they legitimately reach by another grant.
    best: Optional[ProjectRole] = None
    for value in grant_roles:
        try:
            candidate = ProjectRole(value)
        except ValueError:
            continue
        if best is None or _RANK[candidate] > _RANK[best]:
            best = candidate

    if best is None and project.visibility == "org":
        return ProjectRole.VIEWER
    return best


def require_project(
    project_id: int,
    user: models.User,
    db: Session,
    minimum: ProjectRole = ProjectRole.VIEWER,
    request: Optional[Request] = None,
) -> models.Project:
    """Return the project, or raise.

    - **404** when the user has no role at all — identical to a nonexistent id,
      preserving the existing anti-enumeration property of `get_owned_project`.
    - **403** when they have a role but not a high enough one. Not 404: they can
      already see the project, so pretending it vanished when they click Approve
      is a bug report, not security.
    """
    role = effective_project_role(user, project_id, db, request=request)
    if role is None:
        raise HTTPException(status_code=404, detail="Project not found")
    if not at_least(role, minimum):
        raise HTTPException(
            status_code=403,
            detail=(
                f"This action requires the {_ROLE_LABEL[ProjectRole(minimum)]} "
                "role on this project."
            ),
        )
    project = db.get(models.Project, project_id)
    if project is None:
        # Deleted between the resolve and here. Same answer as never existing.
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def require_task(
    task_id: int,
    user: models.User,
    db: Session,
    minimum: ProjectRole = ProjectRole.VIEWER,
    request: Optional[Request] = None,
) -> models.Task:
    """Return the task, resolving permission through its project.

    Same 404/403 contract as `require_project`, but the 404 detail says "Task
    not found" so the message matches what the caller asked for.
    """
    task = db.get(models.Task, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")

    role = effective_project_role(user, task.project_id, db, request=request)
    if role is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if not at_least(role, minimum):
        raise HTTPException(
            status_code=403,
            detail=(
                f"This action requires the {_ROLE_LABEL[ProjectRole(minimum)]} "
                "role on this project."
            ),
        )
    return task


def accessible_project_ids(user: models.User, db: Session) -> list:
    """Every project id the user has any role on: owned ∪ granted ∪ org-visible.

    Replaces `_owned_project_ids` and the inline `owner_id == user.id` filters on
    the list endpoints. Returns ids rather than rows so callers keep building
    their own queries (some of them deliberately avoid loading annotations —
    CLAUDE.md rule 17).
    """
    ids = {
        pid
        for (pid,) in db.query(models.Project.id)
        .filter(models.Project.owner_id == user.id)
        .all()
    }

    ids.update(
        pid
        for (pid,) in db.query(models.ProjectGrant.project_id)
        .join(
            models.TeamMembership,
            models.TeamMembership.team_id == models.ProjectGrant.team_id,
        )
        .filter(models.TeamMembership.user_id == user.id)
        .all()
    )

    # Any authenticated user gets an implicit viewer role on an org-visible
    # project, so those ids belong in every caller's list.
    ids.update(
        pid
        for (pid,) in db.query(models.Project.id)
        .filter(models.Project.visibility == "org")
        .all()
    )

    return sorted(ids)


def effective_team_role(
    user: models.User, team_id: int, db: Session
) -> Optional[TeamRole]:
    """The user's role inside a team, or None if they are not a member.

    Read from the membership row rather than `Team.owner_id`: the two are kept
    consistent in one transaction on transfer, and the membership row is the one
    that carries manager as well as owner.
    """
    membership = (
        db.query(models.TeamMembership)
        .filter(
            models.TeamMembership.team_id == team_id,
            models.TeamMembership.user_id == user.id,
        )
        .first()
    )
    if membership is None:
        return None
    try:
        return TeamRole(membership.role)
    except ValueError:
        # An unrecognised role string still means they belong to the team; treat
        # it as the least privilege rather than as no membership at all.
        return TeamRole.MEMBER


def require_team(
    team_id: int,
    user: models.User,
    db: Session,
    minimum: TeamRole = TeamRole.MEMBER,
) -> models.Team:
    """Return the team, or raise.

    **404** when the caller is not a member — a non-member team id is
    indistinguishable from a nonexistent one, which is what stops a project owner
    probing team ids through the grants endpoint (E-16). **403** when they are a
    member but below `minimum`.
    """
    team = db.get(models.Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")

    role = effective_team_role(user, team_id, db)
    if role is None:
        raise HTTPException(status_code=404, detail="Team not found")
    if _TEAM_RANK[role] < _TEAM_RANK[TeamRole(minimum)]:
        raise HTTPException(
            status_code=403,
            detail=(
                f"This action requires the {_TEAM_ROLE_LABEL[TeamRole(minimum)]} "
                "role in this team."
            ),
        )
    return team


def can_write_task(
    task: models.Task,
    user: models.User,
    role: Optional[ProjectRole],
    db: Session,
) -> bool:
    """Whether `user` may save this task.

    Returns a bool rather than raising so the caller can attach the team or
    assignee name to its own 403 message.

    The rule, in order:

    - Below ANNOTATOR: never.
    - Manager or owner: always, and never partitioned by assignment.
    - **Assigned to a specific person: only that person.**
    - **Unassigned: nobody below manager.** Work has to be handed to someone
      before it can be started.
    - Otherwise the writer must be a member of the assigned team.

    **Assignment is enforced, not advisory — including the unassigned case.**
    .devnotes/teams/01_DESIGN.md § 3.4 made individual assignment advisory, and
    § 3.3 made unassigned tasks a shared pool open to every annotator. Both were
    reconsidered: the point of handing someone a task is that the others leave
    it alone, and a shared pool means an annotator can start work nobody asked
    them to do, on a task a manager is about to assign elsewhere. Recorded as a
    deviation in PLAN.md § 8.

    Note this no longer consults `restrict_to_assigned_team`. That flag made
    team partitioning opt-in and defaulted to false with no UI to set it, so in
    practice it disabled the entire feature everywhere. Team assignment is now
    unconditional; the column survives only until a migration drops it (F10).

    The escape hatch is deliberate: a project manager or owner can always write,
    so a sick-day handover is a reassignment away rather than a lockout, and
    someone can always get at an unassigned task to triage it.
    """
    if not at_least(role, ProjectRole.ANNOTATOR):
        return False

    # An approved-group status freezes the task for everyone below reviewer.
    # Sign-off is the point at which the work stops being the annotator's to
    # change: the reviewer accepted a specific state, and any later edit — an
    # object, a class, a comment, or a drained timer second — would silently
    # change what was signed off. Reviewers, managers and owners stay writable,
    # or nobody could ever correct or un-approve a task.
    #
    # Placed before the manager short-circuit so the ordering reads as the rule
    # does; the check itself already exempts reviewer and above.
    if is_approved(task.status) and not at_least(role, ProjectRole.REVIEWER):
        return False

    # Managers and owners are never partitioned by assignment, so this check
    # comes first and covers every branch below it.
    if at_least(role, ProjectRole.MANAGER):
        return True

    # Reviewers need write access to annotate tasks they are reviewing — they
    # cannot give meaningful feedback on work they cannot interact with. They
    # are not partitioned by individual/team assignment for the same reason as
    # managers: a reviewer may be asked to review any task in the project.
    if at_least(role, ProjectRole.REVIEWER):
        return True

    if task.assignee_user_id is not None:
        return task.assignee_user_id == user.id

    # Unassigned is no longer a shared pool: an annotator writes only what has
    # been handed to them. Work nobody has been given is work nobody below
    # manager may start.
    if task.assigned_team_id is None:
        return False

    membership = (
        db.query(models.TeamMembership)
        .filter(
            models.TeamMembership.team_id == task.assigned_team_id,
            models.TeamMembership.user_id == user.id,
        )
        .first()
    )
    return membership is not None

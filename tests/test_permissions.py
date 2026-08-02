"""The permission resolver: `api/permissions.py`.

Spec: .devnotes/teams/03_API.md § 9, rationale .devnotes/teams/01_DESIGN.md § 6.

These tests drive the resolver directly against a session rather than through
HTTP. It is a pure function of (user, project, grants, memberships) and testing
it at that level keeps the cases about the *rule* rather than about routing —
the router call sites are Phase 3's problem.
"""
import pytest
from fastapi import HTTPException

import models
from api.permissions import (
    ProjectRole,
    TeamRole,
    accessible_project_ids,
    at_least,
    can_write_task,
    effective_project_role,
    require_project,
    require_task,
    require_team,
)
from database import SessionLocal


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


class FakeRequest:
    """Stands in for a Starlette Request; the resolver only touches `.state`."""

    class _State:
        pass

    def __init__(self):
        self.state = self._State()


_seq = iter(range(1, 10_000))


def make_user(db, prefix="u"):
    user = models.User(username=f"perm-{prefix}-{next(_seq)}", hashed_password="x")
    db.add(user)
    db.flush()
    return user


def make_project(db, owner, visibility="private", restrict=False):
    project = models.Project(
        name="p",
        slug=f"p-{next(_seq)}",
        type="detection",
        status="active",
        owner_id=owner.id if owner else None,
        visibility=visibility,
        restrict_to_assigned_team=restrict,
        require_distinct_reviewer=False,
    )
    db.add(project)
    db.flush()
    return project


def make_team(db, owner):
    team = models.Team(name="t", slug=f"t-{next(_seq)}", owner_id=owner.id)
    db.add(team)
    db.flush()
    return team


def add_member(db, team, user, role="member"):
    membership = models.TeamMembership(team_id=team.id, user_id=user.id, role=role)
    db.add(membership)
    db.flush()
    return membership


def grant(db, project, team, role):
    row = models.ProjectGrant(project_id=project.id, team_id=team.id, role=role)
    db.add(row)
    db.flush()
    return row


def make_task(db, project, assigned_team=None):
    task = models.Task(
        project_id=project.id,
        image_path="a.jpg",
        status="New",
        assigned_team_id=assigned_team.id if assigned_team else None,
    )
    db.add(task)
    db.flush()
    return task


# --- the owner short-circuit -------------------------------------------------


def test_project_owner_gets_owner_role(db):
    owner = make_user(db, "owner")
    project = make_project(db, owner)

    assert effective_project_role(owner, project.id, db) is ProjectRole.OWNER


def test_owner_not_demoted_by_lower_grant(db):
    """E-13: the owner short-circuit must be the *first* branch.

    An owner who is also in a team holding a viewer grant on their own project
    would otherwise resolve to viewer and lose access to their own work.
    """
    owner = make_user(db, "owner")
    project = make_project(db, owner)
    team = make_team(db, owner)
    add_member(db, team, owner)
    grant(db, project, team, "viewer")

    assert effective_project_role(owner, project.id, db) is ProjectRole.OWNER


# --- max over grants ---------------------------------------------------------


def test_effective_role_is_max_across_teams(db):
    """E-07: reaching one project through several teams takes the highest role.

    "Most restrictive wins" would mean joining an extra team *reduces* your
    access, which is the surprising behaviour § 2.3 rules out.
    """
    owner = make_user(db, "owner")
    member = make_user(db, "member")
    project = make_project(db, owner)

    low = make_team(db, owner)
    high = make_team(db, owner)
    add_member(db, low, member)
    add_member(db, high, member)
    grant(db, project, low, "annotator")
    grant(db, project, high, "reviewer")

    assert effective_project_role(member, project.id, db) is ProjectRole.REVIEWER


def test_single_grant_resolves_to_that_role(db):
    owner = make_user(db, "owner")
    member = make_user(db, "member")
    project = make_project(db, owner)
    team = make_team(db, owner)
    add_member(db, team, member)
    grant(db, project, team, "annotator")

    assert effective_project_role(member, project.id, db) is ProjectRole.ANNOTATOR


def test_no_grant_returns_none(db):
    """No membership path to the project means no access at all, not viewer."""
    owner = make_user(db, "owner")
    stranger = make_user(db, "stranger")
    project = make_project(db, owner)

    assert effective_project_role(stranger, project.id, db) is None


def test_team_membership_without_a_grant_gives_nothing(db):
    """Being in a team is not access; the team needs a grant on the project."""
    owner = make_user(db, "owner")
    member = make_user(db, "member")
    project = make_project(db, owner)
    team = make_team(db, owner)
    add_member(db, team, member, role="owner")

    assert effective_project_role(member, project.id, db) is None


def test_missing_project_returns_none(db):
    stranger = make_user(db, "stranger")

    assert effective_project_role(stranger, 9_999_999, db) is None


# --- org visibility ----------------------------------------------------------


def test_org_visibility_floors_at_viewer(db):
    owner = make_user(db, "owner")
    stranger = make_user(db, "stranger")
    project = make_project(db, owner, visibility="org")

    assert effective_project_role(stranger, project.id, db) is ProjectRole.VIEWER


def test_org_visibility_never_implies_write(db):
    """The org floor is read-only: it must not satisfy an annotator minimum."""
    owner = make_user(db, "owner")
    stranger = make_user(db, "stranger")
    project = make_project(db, owner, visibility="org")

    role = effective_project_role(stranger, project.id, db)

    assert not at_least(role, ProjectRole.ANNOTATOR)
    with pytest.raises(HTTPException) as excinfo:
        require_project(project.id, stranger, db, minimum=ProjectRole.ANNOTATOR)
    assert excinfo.value.status_code == 403


def test_org_visibility_does_not_lower_an_existing_grant(db):
    """The floor is applied *after* the grant max, so it can only raise."""
    owner = make_user(db, "owner")
    member = make_user(db, "member")
    project = make_project(db, owner, visibility="org")
    team = make_team(db, owner)
    add_member(db, team, member)
    grant(db, project, team, "manager")

    assert effective_project_role(member, project.id, db) is ProjectRole.MANAGER


# --- require_project / require_task 404 vs 403 -------------------------------


def test_require_project_404_when_no_role(db):
    """404, not 403: a project you cannot reach is indistinguishable from one
    that does not exist, which is the anti-enumeration property the old
    `get_owned_project` had and this must preserve."""
    owner = make_user(db, "owner")
    stranger = make_user(db, "stranger")
    project = make_project(db, owner)

    with pytest.raises(HTTPException) as excinfo:
        require_project(project.id, stranger, db)

    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "Project not found"


def test_require_project_403_when_below_minimum(db):
    """403, not 404: they can already see the project, so hiding it here would
    be a bug report rather than security. The message names the role needed."""
    owner = make_user(db, "owner")
    member = make_user(db, "member")
    project = make_project(db, owner)
    team = make_team(db, owner)
    add_member(db, team, member)
    grant(db, project, team, "viewer")

    with pytest.raises(HTTPException) as excinfo:
        require_project(project.id, member, db, minimum=ProjectRole.MANAGER)

    assert excinfo.value.status_code == 403
    assert "Manager" in excinfo.value.detail


def test_require_project_returns_the_project_when_permitted(db):
    owner = make_user(db, "owner")
    project = make_project(db, owner)

    assert require_project(project.id, owner, db, minimum=ProjectRole.OWNER).id == project.id


def test_require_task_resolves_through_its_project(db):
    owner = make_user(db, "owner")
    member = make_user(db, "member")
    project = make_project(db, owner)
    team = make_team(db, owner)
    add_member(db, team, member)
    grant(db, project, team, "annotator")
    task = make_task(db, project)

    assert require_task(task.id, member, db, minimum=ProjectRole.ANNOTATOR).id == task.id

    with pytest.raises(HTTPException) as excinfo:
        require_task(task.id, member, db, minimum=ProjectRole.REVIEWER)
    assert excinfo.value.status_code == 403


def test_require_task_404_for_unreachable_project(db):
    owner = make_user(db, "owner")
    stranger = make_user(db, "stranger")
    project = make_project(db, owner)
    task = make_task(db, project)

    with pytest.raises(HTTPException) as excinfo:
        require_task(task.id, stranger, db)

    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "Task not found"


# --- the request-scoped cache ------------------------------------------------


def test_role_cached_within_request(db):
    """A second resolve in the same request must not re-query.

    Verified by revoking the grant behind the resolver's back: a cached answer
    is the *stale* one, which is exactly what proves the cache was consulted.
    """
    owner = make_user(db, "owner")
    member = make_user(db, "member")
    project = make_project(db, owner)
    team = make_team(db, owner)
    add_member(db, team, member)
    row = grant(db, project, team, "reviewer")
    request = FakeRequest()

    first = effective_project_role(member, project.id, db, request=request)
    db.delete(row)
    db.flush()
    second = effective_project_role(member, project.id, db, request=request)

    assert first is ProjectRole.REVIEWER
    assert second is ProjectRole.REVIEWER


def test_role_not_cached_across_requests(db):
    """E-22 / E-08: a revoked grant must bite on the very next request.

    This is why the cache hangs off `request.state` and never off the module or
    the session — revocation taking effect immediately is a security property,
    not a nicety.
    """
    owner = make_user(db, "owner")
    member = make_user(db, "member")
    project = make_project(db, owner)
    team = make_team(db, owner)
    add_member(db, team, member)
    row = grant(db, project, team, "reviewer")

    first = effective_project_role(member, project.id, db, request=FakeRequest())
    db.delete(row)
    db.flush()
    second = effective_project_role(member, project.id, db, request=FakeRequest())

    assert first is ProjectRole.REVIEWER
    assert second is None


def test_cache_is_keyed_per_project(db):
    """One cached project must not answer for another."""
    owner = make_user(db, "owner")
    member = make_user(db, "member")
    reachable = make_project(db, owner)
    unreachable = make_project(db, owner)
    team = make_team(db, owner)
    add_member(db, team, member)
    grant(db, reachable, team, "annotator")
    request = FakeRequest()

    assert effective_project_role(member, reachable.id, db, request=request) is ProjectRole.ANNOTATOR
    assert effective_project_role(member, unreachable.id, db, request=request) is None


# --- accessible_project_ids --------------------------------------------------


def test_accessible_ids_include_owned_and_granted(db):
    owner = make_user(db, "owner")
    member = make_user(db, "member")
    own = make_project(db, member)
    granted = make_project(db, owner)
    hidden = make_project(db, owner)
    team = make_team(db, owner)
    add_member(db, team, member)
    grant(db, granted, team, "viewer")

    ids = accessible_project_ids(member, db)

    assert own.id in ids
    assert granted.id in ids
    assert hidden.id not in ids


def test_accessible_ids_include_org_visible_projects(db):
    owner = make_user(db, "owner")
    stranger = make_user(db, "stranger")
    org = make_project(db, owner, visibility="org")

    assert org.id in accessible_project_ids(stranger, db)


def test_accessible_ids_have_no_duplicates(db):
    """Owned *and* granted *and* org-visible is still one id."""
    member = make_user(db, "member")
    project = make_project(db, member, visibility="org")
    team = make_team(db, member)
    add_member(db, team, member)
    grant(db, project, team, "annotator")

    ids = accessible_project_ids(member, db)

    assert ids.count(project.id) == 1


# --- team roles --------------------------------------------------------------


def test_require_team_404_for_non_member(db):
    """E-16: a team you are not in is indistinguishable from one that does not
    exist, so a project owner cannot probe team ids through the grants API."""
    owner = make_user(db, "owner")
    stranger = make_user(db, "stranger")
    team = make_team(db, owner)
    add_member(db, team, owner, role="owner")

    with pytest.raises(HTTPException) as excinfo:
        require_team(team.id, stranger, db)

    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "Team not found"


def test_require_team_403_when_below_minimum(db):
    owner = make_user(db, "owner")
    member = make_user(db, "member")
    team = make_team(db, owner)
    add_member(db, team, owner, role="owner")
    add_member(db, team, member, role="member")

    with pytest.raises(HTTPException) as excinfo:
        require_team(team.id, member, db, minimum=TeamRole.MANAGER)

    assert excinfo.value.status_code == 403
    assert "Manager" in excinfo.value.detail


def test_team_role_does_not_grant_project_access(db):
    """The two axes are independent: owning a team gives nothing on projects."""
    owner = make_user(db, "owner")
    team_owner = make_user(db, "teamowner")
    project = make_project(db, owner)
    team = make_team(db, team_owner)
    add_member(db, team, team_owner, role="owner")

    assert effective_project_role(team_owner, project.id, db) is None


# --- can_write_task / restrict_to_assigned_team ------------------------------


def test_viewer_cannot_write_task(db):
    owner = make_user(db, "owner")
    project = make_project(db, owner)
    task = make_task(db, project)

    assert can_write_task(task, owner, ProjectRole.VIEWER, db) is False


def test_unassigned_task_is_the_shared_pool(db):
    """§ 3.3: a task with no team is writable by any annotate-capable member,
    even with the restrict flag on. Every pre-teams task is in this state, which
    is what makes the migration behaviour-neutral."""
    owner = make_user(db, "owner")
    member = make_user(db, "member")
    project = make_project(db, owner, restrict=True)
    task = make_task(db, project, assigned_team=None)

    assert can_write_task(task, member, ProjectRole.ANNOTATOR, db) is True


def test_restrict_flag_off_allows_any_annotator(db):
    """Default false = today's behaviour: assignment is advisory."""
    owner = make_user(db, "owner")
    outsider = make_user(db, "outsider")
    project = make_project(db, owner, restrict=False)
    team = make_team(db, owner)
    task = make_task(db, project, assigned_team=team)

    assert can_write_task(task, outsider, ProjectRole.ANNOTATOR, db) is True


def test_restrict_flag_blocks_other_team(db):
    """E-24: with the flag on, a non-member of the assigned team is refused."""
    owner = make_user(db, "owner")
    outsider = make_user(db, "outsider")
    project = make_project(db, owner, restrict=True)
    team = make_team(db, owner)
    task = make_task(db, project, assigned_team=team)

    assert can_write_task(task, outsider, ProjectRole.ANNOTATOR, db) is False


def test_restrict_flag_allows_assigned_team_member(db):
    owner = make_user(db, "owner")
    member = make_user(db, "member")
    project = make_project(db, owner, restrict=True)
    team = make_team(db, owner)
    add_member(db, team, member)
    task = make_task(db, project, assigned_team=team)

    assert can_write_task(task, member, ProjectRole.ANNOTATOR, db) is True


def test_restrict_flag_allows_project_manager_from_another_team(db):
    """A project manager is not partitioned by task assignment."""
    owner = make_user(db, "owner")
    manager = make_user(db, "manager")
    project = make_project(db, owner, restrict=True)
    team = make_team(db, owner)
    task = make_task(db, project, assigned_team=team)

    assert can_write_task(task, manager, ProjectRole.MANAGER, db) is True


# --- ranking helpers ---------------------------------------------------------


@pytest.mark.parametrize(
    "role,minimum,expected",
    [
        (ProjectRole.VIEWER, ProjectRole.VIEWER, True),
        (ProjectRole.VIEWER, ProjectRole.ANNOTATOR, False),
        (ProjectRole.ANNOTATOR, ProjectRole.VIEWER, True),
        (ProjectRole.REVIEWER, ProjectRole.ANNOTATOR, True),
        (ProjectRole.MANAGER, ProjectRole.REVIEWER, True),
        (ProjectRole.OWNER, ProjectRole.MANAGER, True),
        (ProjectRole.REVIEWER, ProjectRole.MANAGER, False),
        (None, ProjectRole.VIEWER, False),
    ],
)
def test_at_least_at_every_boundary(role, minimum, expected):
    assert at_least(role, minimum) is expected

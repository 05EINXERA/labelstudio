from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from database import Base

class WorkspaceData(Base):
    """Per-user key/value workspace state.

    `owner_id` is part of the primary key: this table used to be keyed on
    `key` alone, which made it a single global blackboard — every annotator
    read and overwrote everyone else's UI state. Scoping it per user is what
    makes the table safe on a shared instance.
    """
    __tablename__ = "workspace_data"
    key = Column(String, primary_key=True, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), primary_key=True, index=True)
    value = Column(Text)

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String)
    slug = Column(String)
    type = Column(String)
    status = Column(String)
    # Display name of the creator. Retained for existing UI; authorization is
    # keyed on owner_id, never on this string.
    creator = Column(String)
    owner_id = Column(Integer, ForeignKey("users.id"), index=True)
    created_at = Column(DateTime, server_default=func.now())
    assignee = Column(String)
    # When true, a save to a task that has an assigned_team_id is rejected
    # unless the writer is in that team (or is a project manager/owner).
    # Default false because that is exactly today's behaviour: assignment is
    # advisory, any annotate-capable member may edit any task. Making this
    # opt-in per project is what keeps the teams migration behaviour-neutral.
    # See .devnotes/teams/01_DESIGN.md § 3.4.
    restrict_to_assigned_team = Column(Boolean, nullable=False, server_default=false())
    # Reserved for the Phase 5 "a reviewer may not approve their own work" rule.
    # The column ships now so the check does not need a second migration later;
    # nothing reads it yet. See .devnotes/teams/01_DESIGN.md § 4.1.
    require_distinct_reviewer = Column(Boolean, nullable=False, server_default=false())
    # "private" (grants only) | "org" (any authenticated user gets an implicit
    # viewer role). The org floor is applied *after* the grant maximum and never
    # implies write access. Default "private" = today's behaviour.
    # See .devnotes/teams/02_SCHEMA.md § 6.
    visibility = Column(String(20), nullable=False, server_default="private")

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)
    image_path = Column(String)
    description = Column(String)
    status = Column(String)
    assignee = Column(String)
    time_spent = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    annotations = Column(Text)
    # Pixel dimensions of the image at image_path, captured at upload.
    # Nullable because rows predating this column have never been measured;
    # formats.common.image_size() backfills them lazily. YOLO normalization and
    # mask rasterization divide by these, so a missing value is a skip, not a
    # guess. See .devnotes/data-refactor/01_PLAN.md § 1.1.
    image_width = Column(Integer, nullable=True)
    image_height = Column(Integer, nullable=True)
    # The client (browser tab) that last wrote this row. Conflict detection
    # only fires when the incoming write comes from a *different* client, so a
    # tab never 409s against its own earlier save. Nullable: rows written
    # before this column existed have no recorded writer, and a write with no
    # client_id skips the check entirely.
    last_client_id = Column(String(64), nullable=True)
    # Which team this task is distributed to. NULL means the shared pool: every
    # team with an annotate-capable grant may work it, which is what every
    # pre-teams row is and therefore what keeps the migration a no-op.
    #
    # ondelete="SET NULL", never CASCADE: deleting a team must return its tasks
    # to the pool, not destroy annotation work. This is the single most
    # consequential cascade decision in the schema.
    # See .devnotes/teams/02_SCHEMA.md § 5.
    assigned_team_id = Column(
        Integer, ForeignKey("teams.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The typed successor to `assignee` (free text, written by clients from
    # localStorage and retained unchanged so a mid-upgrade deployment keeps
    # working). Individual assignment stays advisory in all cases — it is never
    # enforced on write. See .devnotes/teams/01_DESIGN.md § 3.4.
    assignee_user_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

class TimeLog(Base):
    """Accumulated seconds logged against a name.

    Formerly `TeamMember` / `team_members`, which was never a team in the sense
    the Teams feature means — no FK to users, no project relation, just a
    free-text name and a counter. Renamed so it does not collide with
    `TeamMembership` (.devnotes/teams/01_DESIGN.md § 8).

    `name` remains the primary key: rewriting the accumulate logic in the same
    change that renamed the table would be two risky things at once.
    """

    __tablename__ = "time_logs"
    name = Column(String, primary_key=True, index=True)
    time_logged = Column(Integer, default=0)
    # The typed successor to `name`. Nullable because historical rows are
    # free-text names that may match no account, and guessing at a match would
    # corrupt attribution — the backfill matches usernames exactly or not at all.
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

class Label(Base):
    __tablename__ = "labels"
    id = Column(String, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)
    name = Column(String)
    color = Column(String)

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    username = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    created_at = Column(DateTime, server_default=func.now())


# --- Teams -------------------------------------------------------------------
#
# Two orthogonal role axes (.devnotes/teams/01_DESIGN.md § 2):
#   TeamMembership.role  — your standing *inside a team* (owner/manager/member).
#                          Says nothing about annotation.
#   ProjectGrant.role    — what a team may do *on one project*
#                          (viewer/annotator/reviewer/manager).
# Project ownership (Project.owner_id) sits above both and is not a grant row.
#
# New tables use DateTime(timezone=True); the older tables above use bare
# DateTime, which on Postgres is `timestamp without time zone`. That is a known
# wart (CLAUDE.md rule 7) not migrated as part of this feature — mixing the two
# is safe as long as every write is datetime.now(timezone.utc).


class Team(Base):
    """A named group of people. Grants are made to teams, never to individuals."""

    __tablename__ = "teams"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(120), nullable=False)
    # Globally unique, unlike `name`: two people naming a team "Reviewers" is
    # normal, two teams ambiguous in a URL or an audit line is not. Derived
    # server-side with a numeric suffix on collision, so a user never sees a 409
    # for something they did not type.
    slug = Column(String(140), nullable=False, index=True)
    description = Column(String(500), nullable=True)
    # Denormalised from the `owner` row in team_memberships, deliberately: it
    # makes "teams I own" one indexed query instead of a join with a role
    # filter, and gives the "exactly one owner" invariant somewhere to live that
    # a race cannot violate. A transfer updates both in one transaction.
    owner_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (UniqueConstraint("slug", name="uq_teams_slug"),)


class TeamMembership(Base):
    """A user's membership of, and role within, one team."""

    __tablename__ = "team_memberships"
    id = Column(Integer, primary_key=True, autoincrement=True)
    team_id = Column(
        Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # owner | manager | member. String rather than a DB enum, matching how
    # Task.status and Project.status are already stored: adding a value later is
    # a code change, not a Postgres ALTER TYPE that SQLite cannot mirror.
    # Validated by a Pydantic Literal at the API boundary, which is where that
    # enforcement belongs.
    role = Column(String(20), nullable=False, default="member")
    # Provenance for the roster UI only. Nullable and never blocking: the adding
    # user may later be deleted.
    added_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        # The real defence against double-add races — not a "does this row
        # exist" pre-check in the endpoint, which two concurrent managers can
        # both pass. The endpoint treats IntegrityError as idempotent success.
        UniqueConstraint("team_id", "user_id", name="uq_team_membership"),
        # user_id leads: the resolver's hot path is "all teams for this user".
        Index("ix_team_memberships_user_team", "user_id", "team_id"),
    )


class ProjectGrant(Base):
    """What one team may do on one project. The access boundary."""

    __tablename__ = "project_grants"
    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(
        Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    team_id = Column(
        Integer, ForeignKey("teams.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # viewer | annotator | reviewer | manager. Never "owner": ownership is
    # Project.owner_id, full stop, and a grant that could say owner would give a
    # project two owners with no tiebreak. Enforced by a Pydantic Literal.
    role = Column(String(20), nullable=False, default="annotator")
    granted_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    __table_args__ = (
        # One grant per (project, team). Re-granting a different role is an
        # UPDATE, not a second row — otherwise "max over grants" silently keeps
        # a revoked higher role alive.
        UniqueConstraint("project_id", "team_id", name="uq_project_grant"),
        # team_id leads: the resolver goes user → memberships → team_ids →
        # grants.
        Index("ix_project_grants_team_project", "team_id", "project_id"),
    )


class TaskReview(Base):
    """Append-only log of approval transitions on a task.

    Not the authority on the current state — `Task.status` is
    (.devnotes/teams/01_DESIGN.md § 4.2). Two columns that can disagree about
    whether a task is approved is a bug generator. This is the history, written
    in the same transaction as the status change it records.

    No UPDATE and no DELETE from application code.
    """

    __tablename__ = "task_reviews"
    id = Column(Integer, primary_key=True, autoincrement=True)
    # CASCADE: deleting a task deletes its review history. The reviews describe
    # a task that no longer exists, and keeping them would require soft-deleting
    # tasks, which this codebase does nowhere.
    task_id = Column(
        Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Nullable with no cascade: if a user is ever deleted the audit line
    # survives with a null actor rather than vanishing. (User deletion is not
    # implemented today; this is defensive.)
    reviewer_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    action = Column(String(20), nullable=False)  # approved | rejected | reopened
    note = Column(String(1000), nullable=True)
    previous_status = Column(String(30), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import relationship
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
    # No `onupdate=func.now()`, deliberately. This column is the
    # optimistic-concurrency token (CLAUDE.md rule 11), not a passive audit
    # field, so *when* it moves is a decision the router makes explicitly —
    # every write path assigns it.
    #
    # An implicit onupdate broke that in two ways at once. It fired on any
    # UPDATE to the row, so a save that changed nothing but `last_client_id`
    # still rotated the token (.devnotes/unwanted-time-change/01_DIAGNOSIS.md),
    # defeating the router's "only bump when something changed" gate from
    # underneath it. And `func.now()` resolves to the database's second-
    # resolution clock, so it silently truncated the microseconds the router
    # had just written — leaving the value the response reported and the value
    # actually stored different, which makes the token comparison fail for a
    # client that did nothing wrong.
    updated_at = Column(DateTime, server_default=func.now())
    # The legacy annotation blob. Superseded by the `annotations` table
    # (`annotation_rows` below); retained through the migration as the rollback
    # path and as the fallback source for any task the backfill could not
    # convert. Renamed to `annotations_legacy` in Phase F, once dead.
    #
    # Do NOT read this directly in new code — go through
    # `formats.common.annotation_dicts(task)`, which knows which source is
    # authoritative at this point in the migration.
    annotations = Column(Text)
    # One row per shape. `lazy="selectin"` so loading N tasks costs one extra
    # query rather than N: the gallery and every export iterate tasks, and a
    # default lazy load would turn those into per-task round-trips.
    #
    # Ordered by `seq` — the position the annotation held in the payload.
    #
    # The blob was a JSON array, so it carried an implicit order that decides
    # which shape paints over which (formats.common.ordered_annotations) and
    # what an export emits. Rows have no inherent order, and the database is
    # free to return them in any order at all without an ORDER BY, which would
    # make export output vary run to run.
    #
    # `id` is not a substitute: it is a uuid, or client text like "obj-2999"
    # that sorts lexically ("obj-999" > "obj-2999"), so ordering by it
    # reshuffles the set.
    annotation_rows = relationship(
        "Annotation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="(Annotation.seq, Annotation.id)",
    )
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


class TaskAnnotationHistory(Base):
    """Append-only log of superseded annotation blobs for a task.

    Not the authority on current annotations — `Task.annotations` is. Each row
    holds what the task contained immediately *before* a replacing write, so
    the newest row is the previous value, never the current one.

    Why this exists: annotations are one JSON blob replaced wholesale on every
    save, so a single empty or stale write destroys the task and the prior
    value is gone once the commit returns. That has been fixed three times on
    three different paths (INCIDENT_692, task 707's stale undo stack, and the
    2026-08-11 pre-hydration save). Every fix was a guard, and a guard only
    covers the path someone thought of; this makes the outcome recoverable
    whichever path produced it. See .devnotes/task-history/01_DESIGN.md.

    Deliberately NOT read by the application. Restoring is a human decision
    driven by scripts/, because the server cannot tell a wipe from a genuine
    delete-all — both arrive as `[]` against a task that had work, and guessing
    is what caused the incident this guards against (01_DESIGN.md § 3).

    No UPDATE from application code. DELETE only via the retention prune in
    api/routers/tasks.py, never to rewrite what happened.
    """

    __tablename__ = "task_annotation_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # CASCADE, matching task_reviews: the history describes a task that no
    # longer exists once the task is deleted, and keeping it would require
    # soft-deleting tasks, which this codebase does nowhere.
    task_id = Column(
        Integer, ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The superseded blob, verbatim. Text rather than JSON because it is never
    # queried by content — only read back whole — and storing the exact bytes
    # avoids a re-serialisation quietly changing what gets restored.
    annotations = Column(Text, nullable=False)
    # Denormalised so the "what got wiped" scan never parses JSON across the
    # table. This is the column scripts/find_annotation_loss.py reads.
    annotation_count = Column(Integer, nullable=False)
    # The count the *replacing* write carried. With annotation_count above, a
    # wipe is self-evident from one row: 403 → 0.
    replaced_with_count = Column(Integer, nullable=False)
    # Nullable with no cascade, as in task_reviews: if a user is ever deleted
    # the audit line survives with a null actor rather than vanishing.
    replaced_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    client_id = Column(String(64), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    __table_args__ = (
        # The two access patterns, both per task and newest-first: the retention
        # prune, and --list-history / --from-history in
        # scripts/restore_task_annotations.py.
        Index("ix_task_annotation_history_task_id_id", "task_id", "id"),
    )


class Annotation(Base):
    """One annotation shape, one row.

    Replaces `Task.annotations` — a single JSON blob rewritten in full on every
    save. Blobs reached 15.6 MB in production, and because a save parsed and
    rewrote all of it while holding the GIL, each concurrent save added ~700 ms
    of latency to *every other request in the process*. See
    .devnotes/server-issue-diagnosis/evidence/06_ROOT_CAUSE_CONFIRMED.md and
    .devnotes/performance-fixes/03_NORMALIZE_ANNOTATIONS.md.

    The column set is derived from a survey of the real data, not from the
    client's type declarations — see 06_PROGRESS.md D1 for the field counts
    that decided each nullability below.
    """

    __tablename__ = "annotations"
    # We delete through the relationship *and* rely on the FK's ON DELETE
    # CASCADE. SQLAlchemy's post-delete row-count check warns when the database
    # got there first, which is expected here rather than a fault.
    __mapper_args__ = {"confirm_deleted_rows": False}

    # Client-generated (uuid4), not autoincrement: the browser mints ids
    # offline before any round-trip and the offline queue replays them later,
    # so a server-assigned id would break `.devnotes/offline/`.
    id = Column(String(64), primary_key=True)
    # Part of the primary key, deliberately. 678 annotation ids in the dev
    # data appear on more than one task (70 tasks across 67 projects) — real
    # copy-paste between tasks, not test noise. A bare `id` PK cannot represent
    # that and the backfill would fail outright on it. No id repeats *within*
    # a task, so (id, task_id) is sound. See 06_PROGRESS.md D2.
    task_id = Column(
        Integer, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    # SET NULL, never CASCADE: deleting a class must orphan the shape, not
    # destroy it. This replaces purge_annotations_for_labels()'s project-wide
    # blob rewrite with a foreign key.
    label_id = Column(
        String, ForeignKey("labels.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Nullable, unlike trackTime's NOT NULL: 4,059 of 17,245 real annotations
    # carry no `type` at all. `formats.common.is_annotation()` reads an absent
    # type as a real shape (`!= "comment"`), so defaulting these to 'polygon'
    # at backfill would be a silent data change. NULL preserves the fact.
    type = Column(String(32), nullable=True)
    # The vertex list, stored as the exact JSON bytes the client sent.
    #
    # Two shapes exist in the real data — [{"x":..,"y":..}, ...] (14,173) and
    # [[x, y], ...] (3,001) — and `formats.common.points_of()` already copes
    # with both. Normalising them to one form here would smuggle a behaviour
    # change into a storage migration, so the bytes are preserved verbatim.
    #
    # Kept as one column rather than a vertices table on purpose: this is what
    # keeps a 500-vertex polygon one row instead of 500, so row counts stay in
    # the millions rather than the hundreds of millions while counting and
    # per-label queries still become plain SQL.
    points = Column(Text, nullable=True)
    x = Column(Float, nullable=True)
    y = Column(Float, nullable=True)
    width = Column(Float, nullable=True)
    height = Column(Float, nullable=True)
    text = Column(Text, nullable=True)
    color = Column(String(16), nullable=True)
    order = Column(Integer, nullable=True)
    # Position in the payload the annotation arrived in.
    #
    # Distinct from `order`, which is a client-supplied paint index present on
    # only 2 of 17,245 real annotations. `seq` is always set, and it is what
    # reproduces the JSON array's implicit ordering now that the array is gone.
    # Without it a task's shapes come back in whatever order the heap scan
    # yields, which changes what an overlap renders and makes exports unstable.
    seq = Column(Integer, nullable=True)
    group_id = Column(String(36), nullable=True, index=True)
    # Every field the columns above do not model, as a JSON object.
    #
    # The real data carries `label`, `visible`, `promptPoints`, `promptLabels`,
    # `source`, `author`, `w` and `h` — none of which were in the design, and
    # all of which some client depends on. This column is what makes the
    # migration lossless without the schema having to enumerate whatever the
    # canvas adds next; the round-trip check in the backfill is what proves it.
    extra = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # The comment-count and per-type aggregates filter on exactly this
        # pair; without it they degrade to a scan of every row for the task.
        Index("ix_annotations_task_id_type", "task_id", "type"),
    )

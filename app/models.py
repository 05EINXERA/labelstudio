"""SQLAlchemy ORM models for database persistence."""
from sqlalchemy import Column, Integer, String, DateTime, func, Text, ForeignKey, Float, LargeBinary

# All timestamp columns store timezone-aware UTC (CLAUDE.md rule 7). Without
# timezone=True, Postgres silently strips tzinfo on write and hands back a
# naive datetime on read — every conflict-detection comparison in
# api/routers/tasks.py that re-attaches tzinfo=utc on a naive read was only
# safe by coincidence of this server's timezone being UTC.
UTCDateTime = DateTime(timezone=True)
from sqlalchemy.orm import relationship
try:
    from app.database import Base
except ImportError:
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
    creator = Column(String, index=True)
    owner_id = Column(Integer, ForeignKey("users.id"), index=True)
    created_at = Column(UTCDateTime, server_default=func.now())
    team_id = Column(Integer, ForeignKey("teams.id"), nullable=True, index=True)

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), index=True)
    image_path = Column(String)
    description = Column(String)
    status = Column(String, index=True)
    assignee = Column(String, index=True)
    time_spent = Column(Integer, default=0)
    created_at = Column(UTCDateTime, server_default=func.now())
    updated_at = Column(UTCDateTime, server_default=func.now(), onupdate=func.now())
    annotations_legacy = Column("annotations_legacy", Text, nullable=True)
    annotations = relationship("Annotation", cascade="all, delete-orphan", passive_deletes=True)
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

class Team(Base):
    __tablename__ = "teams"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    name = Column(String, unique=True, index=True)
    creator = Column(String, nullable=True)
    created_at = Column(UTCDateTime, server_default=func.now())

class TeamMember(Base):
    __tablename__ = "team_members"
    name = Column(String, primary_key=True, index=True)
    time_logged = Column(Integer, default=0)
    last_active_at = Column(UTCDateTime, nullable=True)

class LoginSession(Base):
    """One login→logout span for a team member, for the Teams page history.

    `last_active_at` on TeamMember is a single mutable cell: it answers "is this
    person online right now" and nothing else. This table is the append-only
    history behind it, so the day's logins and logouts survive the next ping.

    A row is opened on login and closed either by an explicit logout or by the
    sweeper in `api/routers/team.py`, which stamps `logout_at = last_seen_at`
    once the heartbeat has been silent past the presence timeout. Annotators
    close the tab far more often than they click Log out, so `ended_reason`
    records which of the two closed the row and the UI labels it.
    """
    __tablename__ = "login_sessions"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    member_name = Column(String, ForeignKey("team_members.name", ondelete="CASCADE"), index=True)
    login_at = Column(UTCDateTime, nullable=False, index=True)
    # Advanced by the heartbeat; becomes logout_at when a stale session is swept.
    last_seen_at = Column(UTCDateTime, nullable=False)
    # NULL means the session is still open.
    logout_at = Column(UTCDateTime, nullable=True)
    # 'logout' (explicit) or 'inactive' (swept). NULL while open.
    ended_reason = Column(String(16), nullable=True)

class TeamMemberAssociation(Base):
    __tablename__ = "team_member_associations"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    member_name = Column(String, ForeignKey("team_members.name", ondelete="CASCADE"), index=True)
    team_id = Column(Integer, ForeignKey("teams.id", ondelete="CASCADE"), index=True)

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
    created_at = Column(UTCDateTime, server_default=func.now())

class TaskLock(Base):
    __tablename__ = "task_locks"
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True, index=True)
    client_id = Column(String(64), nullable=False)
    claimed_at = Column(UTCDateTime, server_default=func.now(), nullable=False, index=True)

class AIJob(Base):
    __tablename__ = "ai_jobs"
    id = Column(String(36), primary_key=True, index=True)
    status = Column(String(32), default="pending", nullable=False)
    result = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(UTCDateTime, server_default=func.now(), nullable=False, index=True)

class Annotation(Base):
    __tablename__ = "annotations"
    __mapper_args__ = {"confirm_deleted_rows": False}
    id = Column(String(64), primary_key=True)
    task_id = Column(Integer, ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True, index=True)
    label_id = Column(String, ForeignKey("labels.id", ondelete="SET NULL"), nullable=True, index=True)
    type = Column(String(32), nullable=False)
    points = Column(Text, nullable=True) # JSON string
    x = Column(Float, nullable=True)
    y = Column(Float, nullable=True)
    width = Column(Float, nullable=True)
    height = Column(Float, nullable=True)
    text = Column(Text, nullable=True)
    color = Column(String(16), nullable=True)
    order = Column(Integer, nullable=True)
    group_id = Column(String(36), nullable=True, index=True)
    extra = Column(Text, nullable=True) # JSON string for unmodeled fields
    created_at = Column(UTCDateTime, server_default=func.now())

class ExportJob(Base):
    __tablename__ = "export_jobs"
    id = Column(String(36), primary_key=True, index=True)
    status = Column(String(32), default="pending", nullable=False)
    media_type = Column(String(128), nullable=True)
    filename = Column(String(255), nullable=True)
    content = Column(LargeBinary, nullable=True) # ZIP/CSV/JSON content
    meta_info = Column(Text, nullable=True) # JSON payload containing task_count, format, etc.
    error = Column(Text, nullable=True)
    created_at = Column(UTCDateTime, server_default=func.now(), nullable=False, index=True)

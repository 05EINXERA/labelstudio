from sqlalchemy import Column, Integer, String, DateTime, func, Text, ForeignKey
from database import Base

class WorkspaceData(Base):
    __tablename__ = "workspace_data"
    key = Column(String, primary_key=True, index=True)
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

class TeamMember(Base):
    __tablename__ = "team_members"
    name = Column(String, primary_key=True, index=True)
    time_logged = Column(Integer, default=0)

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

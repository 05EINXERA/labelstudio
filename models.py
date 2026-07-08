from sqlalchemy import Column, Integer, String, DateTime, func, Text
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
    creator = Column(String)
    created_at = Column(DateTime, server_default=func.now())

class Task(Base):
    __tablename__ = "tasks"
    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    project_id = Column(Integer, index=True)
    image_path = Column(String)
    description = Column(String)
    status = Column(String)
    assignee = Column(String)
    time_spent = Column(Integer, default=0)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    annotations = Column(Text)

class TeamMember(Base):
    __tablename__ = "team_members"
    name = Column(String, primary_key=True, index=True)
    time_logged = Column(Integer, default=0)

class Label(Base):
    __tablename__ = "labels"
    id = Column(String, primary_key=True, index=True)
    name = Column(String)
    color = Column(String)

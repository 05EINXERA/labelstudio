"""SQLAlchemy ORM models facade (re-exports from `app.models`).

Maintains 100% backward compatibility for legacy imports and scripts.
"""
from app.models import (
    AIJob,
    Base,
    Label,
    Project,
    Task,
    TaskLock,
    Team,
    TeamMember,
    TeamMemberAssociation,
    User,
    WorkspaceData,
)

__all__ = [
    "AIJob",
    "Base",
    "Label",
    "Project",
    "Task",
    "TaskLock",
    "Team",
    "TeamMember",
    "TeamMemberAssociation",
    "User",
    "WorkspaceData",
]

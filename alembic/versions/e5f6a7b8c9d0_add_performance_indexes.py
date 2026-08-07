"""add performance indexes on tasks, projects, task_locks, and ai_jobs

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-07 12:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add database indexes for query and filter performance."""
    op.create_index(op.f('ix_projects_creator'), 'projects', ['creator'], unique=False)
    op.create_index(op.f('ix_projects_team_id'), 'projects', ['team_id'], unique=False)
    op.create_index(op.f('ix_tasks_status'), 'tasks', ['status'], unique=False)
    op.create_index(op.f('ix_tasks_assignee'), 'tasks', ['assignee'], unique=False)
    op.create_index(op.f('ix_task_locks_claimed_at'), 'task_locks', ['claimed_at'], unique=False)
    op.create_index(op.f('ix_ai_jobs_created_at'), 'ai_jobs', ['created_at'], unique=False)


def downgrade() -> None:
    """Drop performance indexes."""
    op.drop_index(op.f('ix_ai_jobs_created_at'), table_name='ai_jobs')
    op.drop_index(op.f('ix_task_locks_claimed_at'), table_name='task_locks')
    op.drop_index(op.f('ix_tasks_assignee'), table_name='tasks')
    op.drop_index(op.f('ix_tasks_status'), table_name='tasks')
    op.drop_index(op.f('ix_projects_team_id'), table_name='projects')
    op.drop_index(op.f('ix_projects_creator'), table_name='projects')

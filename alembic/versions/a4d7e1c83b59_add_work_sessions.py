"""add work_sessions

Revision ID: a4d7e1c83b59
Revises: f3a9d2c61e57
Create Date: 2026-10-09 10:00:00.000000

One row per (user, task, continuous stretch) for team activity monitoring.
Creates the table and its two indexes only, so the chain still builds on an
empty database. `task_id` is indexed because its FK is ON DELETE SET NULL:
without the index every bulk task delete scans this table once per task.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a4d7e1c83b59'
down_revision: Union[str, Sequence[str], None] = 'f3a9d2c61e57'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'work_sessions',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('member_name', sa.String(), nullable=False),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('tasks.id', ondelete='SET NULL'), nullable=True),
        sa.Column('task_name', sa.String(length=255), nullable=True),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('active_seconds', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('objects_start', sa.Integer(), nullable=True),
        sa.Column('objects_end', sa.Integer(), nullable=True),
    )
    op.create_index('ix_work_sessions_task_id', 'work_sessions', ['task_id'])
    op.create_index('ix_work_sessions_member_started', 'work_sessions', ['member_name', 'started_at'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_work_sessions_member_started', table_name='work_sessions')
    op.drop_index('ix_work_sessions_task_id', table_name='work_sessions')
    op.drop_table('work_sessions')

"""add login_sessions table

Revision ID: f1c2d3e4a5b6
Revises: e6a762594f8e
Create Date: 2026-09-04 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1c2d3e4a5b6'
down_revision: Union[str, Sequence[str], None] = 'e6a762594f8e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'login_sessions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('member_name', sa.String(), nullable=True),
        sa.Column('login_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('logout_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_reason', sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(['member_name'], ['team_members.name'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_login_sessions_id'), 'login_sessions', ['id'])
    op.create_index(op.f('ix_login_sessions_member_name'), 'login_sessions', ['member_name'])
    op.create_index(op.f('ix_login_sessions_login_at'), 'login_sessions', ['login_at'])
    # The history query is always "this member, this day", and the sweeper scans
    # open rows; both are served by this composite.
    op.create_index(
        'ix_login_sessions_member_login', 'login_sessions', ['member_name', 'login_at']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_login_sessions_member_login', table_name='login_sessions')
    op.drop_index(op.f('ix_login_sessions_login_at'), table_name='login_sessions')
    op.drop_index(op.f('ix_login_sessions_member_name'), table_name='login_sessions')
    op.drop_index(op.f('ix_login_sessions_id'), table_name='login_sessions')
    op.drop_table('login_sessions')

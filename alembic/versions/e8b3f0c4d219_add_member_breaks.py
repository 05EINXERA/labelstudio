"""add member_breaks table

Revision ID: e8b3f0c4d219
Revises: d7a1b93c5e42
Create Date: 2026-09-24 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e8b3f0c4d219'
down_revision: Union[str, Sequence[str], None] = 'd7a1b93c5e42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'member_breaks',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('member_name', sa.String(), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('ended_reason', sa.String(length=16), nullable=True),
        sa.ForeignKeyConstraint(['member_name'], ['team_members.name'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_member_breaks_id'), 'member_breaks', ['id'])
    op.create_index(op.f('ix_member_breaks_member_name'), 'member_breaks', ['member_name'])
    op.create_index(op.f('ix_member_breaks_started_at'), 'member_breaks', ['started_at'])
    # Same access pattern as login_sessions: "this member, this day".
    op.create_index(
        'ix_member_breaks_member_started', 'member_breaks', ['member_name', 'started_at']
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_member_breaks_member_started', table_name='member_breaks')
    op.drop_index(op.f('ix_member_breaks_started_at'), table_name='member_breaks')
    op.drop_index(op.f('ix_member_breaks_member_name'), table_name='member_breaks')
    op.drop_index(op.f('ix_member_breaks_id'), table_name='member_breaks')
    op.drop_table('member_breaks')

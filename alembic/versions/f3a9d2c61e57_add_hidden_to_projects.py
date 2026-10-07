"""add hidden flag to projects

Revision ID: f3a9d2c61e57
Revises: e8b3f0c4d219
Create Date: 2026-10-07 10:00:00.000000

The owner can hide a project from everyone else. NOT NULL with a server
default so existing rows come out visible and no reader has to treat NULL as a
third state.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a9d2c61e57'
down_revision: Union[str, Sequence[str], None] = 'e8b3f0c4d219'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # batch_alter_table so the downgrade's DROP COLUMN also works on SQLite.
    with op.batch_alter_table('projects') as batch_op:
        batch_op.add_column(
            sa.Column('hidden', sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('projects') as batch_op:
        batch_op.drop_column('hidden')

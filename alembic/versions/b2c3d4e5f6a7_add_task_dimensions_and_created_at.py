"""Add image dimensions and created_at to tasks

Adds the columns the interop export formats need
(.devnotes/data-refactor/01_PLAN.md § 1.1):

- image_width / image_height: YOLO normalizes coordinates by these and mask
  rasterization sizes its canvas from them. Reading them from disk per export
  was a repeated Pillow open that silently degraded to (0, 0) for a missing
  file — which is a division by zero in YOLO. Nullable: existing rows have
  never been measured, and formats.common.image_size() backfills them lazily.

- created_at: the interop task JSON carries `createdAt`. We only tracked
  updated_at. Existing rows get the migration's own timestamp via
  server_default; a fabricated-but-plausible value beats emitting null into a
  field consumers expect to be a date.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-23 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b2c3d4e5f6a7'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.add_column(sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=True))
        batch_op.add_column(sa.Column('image_width', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('image_height', sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('tasks') as batch_op:
        batch_op.drop_column('image_height')
        batch_op.drop_column('image_width')
        batch_op.drop_column('created_at')

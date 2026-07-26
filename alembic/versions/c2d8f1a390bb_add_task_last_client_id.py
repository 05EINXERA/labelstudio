"""Add tasks.last_client_id for conflict detection

Records which browser tab last wrote a task, so optimistic-concurrency
detection can distinguish a real cross-client conflict from a client
overwriting its own previous save. The latter is routine — one tab autosaves
on a debounce, flushes a beacon on tab-hide and drains the timer every 30s —
and treating it as a conflict silently discarded annotations.

See .devnotes/deployment-hardening/04_ANNOTATION_SAVE_LOSS.md.

Revision ID: c2d8f1a390bb
Revises: b1a7c4e92f10
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c2d8f1a390bb"
down_revision: Union[str, Sequence[str], None] = "b1a7c4e92f10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable with no default: existing rows have no recorded writer, and a
    # write carrying no client_id skips the conflict check entirely, so the
    # backfill is genuinely unnecessary.
    op.add_column(
        "tasks",
        sa.Column("last_client_id", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "last_client_id")

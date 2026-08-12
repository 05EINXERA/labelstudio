"""Add task_annotation_history

An append-only log of the value `tasks.annotations` held immediately *before*
each replacing write, retained for the newest 5 writes per task.

Annotations are a single JSON blob replaced wholesale on every save, so one
empty or stale write destroys a task and the previous value is gone the moment
the commit returns. That failure has been fixed three times on three different
paths (INCIDENT_692; task 707's stale undo stack, 2026-08-06; the
pre-hydration Ctrl+S, 2026-08-11). Each fix was a guard, and a guard only
covers the path someone thought of — this makes the outcome recoverable
whichever path produced it. See .devnotes/task-history/01_DESIGN.md.

**No backfill, deliberately.** There is nothing to backfill from: the prior
values of existing rows were overwritten long ago. History accumulates from
deploy onward, so this cannot recover anything wiped before it ships.

Rule 8: creates a new table only, touching no existing one, so the chain still
builds on an empty database (Postgres deploys start empty). No batch mode is
needed — that is only required for ALTERing existing tables under SQLite.

Revision ID: b5c9e2a41f68
Revises: a4b8d3f07e54
Create Date: 2026-08-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b5c9e2a41f68"
down_revision: Union[str, Sequence[str], None] = "a4b8d3f07e54"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_annotation_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        # CASCADE, matching task_reviews: history describes a task that no
        # longer exists once the task is deleted, and keeping it would require
        # soft-deleting tasks, which this codebase does nowhere.
        sa.Column("task_id", sa.Integer(), nullable=False),
        # Verbatim bytes of the superseded blob. Text, not JSON: never queried
        # by content, only read back whole, and an exact copy means a restore
        # cannot be altered by re-serialisation.
        sa.Column("annotations", sa.Text(), nullable=False),
        # Denormalised so the wipe scan never parses JSON across the table.
        sa.Column("annotation_count", sa.Integer(), nullable=False),
        # What the replacing write carried; with annotation_count a wipe is
        # self-evident from a single row (403 → 0).
        sa.Column("replaced_with_count", sa.Integer(), nullable=False),
        # Nullable, no cascade: if a user is ever deleted the audit line
        # survives with a null actor rather than vanishing.
        sa.Column("replaced_by_user_id", sa.Integer(), nullable=True),
        sa.Column("client_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["replaced_by_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_task_annotation_history_task_id"),
        "task_annotation_history",
        ["task_id"],
    )
    op.create_index(
        op.f("ix_task_annotation_history_replaced_by_user_id"),
        "task_annotation_history",
        ["replaced_by_user_id"],
    )
    op.create_index(
        op.f("ix_task_annotation_history_created_at"),
        "task_annotation_history",
        ["created_at"],
    )
    # Both real access patterns are per task, newest first: the retention prune
    # on every write, and --list-history / --from-history in
    # scripts/restore_task_annotations.py.
    op.create_index(
        "ix_task_annotation_history_task_id_id",
        "task_annotation_history",
        ["task_id", "id"],
    )


def downgrade() -> None:
    # Safe to drop: nothing here is authoritative. Dropping loses recovery
    # capability, never live state.
    op.drop_index("ix_task_annotation_history_task_id_id", table_name="task_annotation_history")
    op.drop_index(
        op.f("ix_task_annotation_history_created_at"), table_name="task_annotation_history"
    )
    op.drop_index(
        op.f("ix_task_annotation_history_replaced_by_user_id"),
        table_name="task_annotation_history",
    )
    op.drop_index(
        op.f("ix_task_annotation_history_task_id"), table_name="task_annotation_history"
    )
    op.drop_table("task_annotation_history")

"""Drop task_annotation_history

The table kept the annotation set each save superseded. After the annotation
normalisation it was the last place that still serialised a task's *entire*
annotation set on every save: measured on production at 858 ms of GIL-held CPU
and a 22 MB row write for task 713, plus 467 ms for the append-skip guard that
decided whether to do it — ~1,325 ms per save, in 95% of cases to preserve a
single changed shape. Its retention prune was also the source of every
`DeadlockDetected` in the log.

Removed rather than optimised: the wipes it guarded against became rare, and it
had grown to 7.9 GB (19x the `annotations` table it was meant to protect).
Recovery for a wipe is now the hourly backup in E:/annotation-backups, restored
with `scripts/restore_task_annotations.py --file`.
See .devnotes/remove-annotation-history/.

**This is the one step that is not reversible in practice.** `downgrade()`
recreates the table, but the rows are gone — no downgrade can bring back what
the DROP removed. Deploy it *after* the code change has run long enough to be
trusted: the code stops writing history immediately, so the existing rows stay
readable and recoverable-from for as long as this migration is held back. That
ordering is deliberate and is the whole reason this is a separate phase
(.devnotes/remove-annotation-history/02_PLAN.md § 4).

Rule 8: the chain must still build on an empty database. `b5c9e2a41f68` creates
this table and is left untouched, so a fresh Postgres deploy creates it and this
revision drops it again — correct, if briefly wasteful, and far safer than
editing a migration that has already run everywhere.

Revision ID: d4b8f3c07e19
Revises: c7d1e4a82b95
Create Date: 2026-09-06
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4b8f3c07e19"
down_revision: Union[str, Sequence[str], None] = "c7d1e4a82b95"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("task_annotation_history")


def downgrade() -> None:
    """Recreate the table, empty.

    Restores the schema so the chain is walkable in both directions. It does
    NOT restore the data, which the upgrade destroyed — recovering that means
    restoring a backup taken before the upgrade ran.

    Mirrors b5c9e2a41f68 exactly, so a database downgraded to this point is
    structurally identical to one that never ran the drop.
    """
    op.create_table(
        "task_annotation_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("annotations", sa.Text(), nullable=False),
        sa.Column("annotation_count", sa.Integer(), nullable=False),
        sa.Column("replaced_with_count", sa.Integer(), nullable=False),
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
        "ix_task_annotation_history_task_id", "task_annotation_history",
        ["task_id"],
    )
    op.create_index(
        "ix_task_annotation_history_replaced_by_user_id",
        "task_annotation_history", ["replaced_by_user_id"],
    )
    op.create_index(
        "ix_task_annotation_history_created_at", "task_annotation_history",
        ["created_at"],
    )
    op.create_index(
        "ix_task_annotation_history_task_id_id", "task_annotation_history",
        ["task_id", "id"],
    )

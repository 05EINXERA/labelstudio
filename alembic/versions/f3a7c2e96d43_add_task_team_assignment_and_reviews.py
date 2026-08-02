"""Add task team assignment and task_reviews (Teams M3)

Adds the work-distribution half of the feature:

- tasks.assigned_team_id — which team a task is distributed to. NULL means the
  shared pool, which is what every existing row is after this migration, and
  therefore why there is no backfill: NULL is already the correct value.

  **ondelete="SET NULL", never CASCADE.** Deleting a team must return its tasks
  to the pool; CASCADE here would delete thousands of annotated tasks along with
  the team. This is the most consequential cascade decision in the schema
  (.devnotes/teams/02_SCHEMA.md § 5).

- tasks.assignee_user_id — the typed successor to the free-text `assignee`,
  which is retained unchanged so clients running a cached JS bundle keep
  working. Individual assignment stays advisory; it is never enforced on write.

- task_reviews — append-only log of approval transitions. Not the authority on
  the current state (Task.status is); this is the history, written in the same
  transaction as the status change it records.

Batch mode on both add_column calls: SQLite cannot add an FK-bearing column with
a plain ALTER, and this is the most likely place for these migrations to break
across the two backends.

Revision ID: f3a7c2e96d43
Revises: e2f6b1d85c32
Create Date: 2026-08-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f3a7c2e96d43"
down_revision: Union[str, Sequence[str], None] = "e2f6b1d85c32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.add_column(sa.Column("assigned_team_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("assignee_user_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            op.f("ix_tasks_assigned_team_id"), ["assigned_team_id"], unique=False
        )
        batch_op.create_index(
            op.f("ix_tasks_assignee_user_id"), ["assignee_user_id"], unique=False
        )
        batch_op.create_foreign_key(
            "fk_tasks_assigned_team_id_teams", "teams", ["assigned_team_id"], ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_tasks_assignee_user_id_users", "users", ["assignee_user_id"], ["id"],
            ondelete="SET NULL",
        )

    op.create_table(
        "task_reviews",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("reviewer_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("note", sa.String(length=1000), nullable=True),
        sa.Column("previous_status", sa.String(length=30), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"],
            name="fk_task_reviews_task_id_tasks", ondelete="CASCADE",
        ),
        # No cascade on reviewer_id: if a user is ever deleted the audit line
        # survives with a null actor rather than vanishing.
        sa.ForeignKeyConstraint(
            ["reviewer_id"], ["users.id"], name="fk_task_reviews_reviewer_id_users",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_task_reviews_task_id"), "task_reviews", ["task_id"], unique=False)
    op.create_index(
        op.f("ix_task_reviews_reviewer_id"), "task_reviews", ["reviewer_id"], unique=False
    )
    op.create_index(
        op.f("ix_task_reviews_created_at"), "task_reviews", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_task_reviews_created_at"), table_name="task_reviews")
    op.drop_index(op.f("ix_task_reviews_reviewer_id"), table_name="task_reviews")
    op.drop_index(op.f("ix_task_reviews_task_id"), table_name="task_reviews")
    op.drop_table("task_reviews")

    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_constraint("fk_tasks_assignee_user_id_users", type_="foreignkey")
        batch_op.drop_constraint("fk_tasks_assigned_team_id_teams", type_="foreignkey")
        batch_op.drop_index(op.f("ix_tasks_assignee_user_id"))
        batch_op.drop_index(op.f("ix_tasks_assigned_team_id"))
        batch_op.drop_column("assignee_user_id")
        batch_op.drop_column("assigned_team_id")

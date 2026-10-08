"""Add task_assignment_events (assignment history)

Append-only log of changes to a task's (team, person) assignment, shown in the
Tasks table's "Assignment history" modal. See
.devnotes/features/task-assignment-history/.

The table is seeded with one `backfill` row per task that is assigned right
now, so today's assignee is the first entry in its history. The row carries the
migration time: the true original date was never recorded, and inventing one
would make the log lie. On an empty database the INSERT matches nothing, so the
chain still builds from scratch (CLAUDE.md rule 8).

`downgrade()` drops the table and therefore the history; there is no way back.

Revision ID: e5a9c1d73b06
Revises: 72a3921edd78
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5a9c1d73b06"
down_revision: Union[str, Sequence[str], None] = "72a3921edd78"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_assignment_events",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("changed_by_id", sa.Integer(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("team_from_id", sa.Integer(), nullable=True),
        sa.Column("team_to_id", sa.Integer(), nullable=True),
        sa.Column("user_from_id", sa.Integer(), nullable=True),
        sa.Column("user_to_id", sa.Integer(), nullable=True),
        sa.Column("team_from_name", sa.String(), nullable=True),
        sa.Column("team_to_name", sa.String(), nullable=True),
        sa.Column("user_from_name", sa.String(), nullable=True),
        sa.Column("user_to_name", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"],
            name="fk_task_assignment_events_task_id_tasks", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_id"], ["users.id"],
            name="fk_task_assignment_events_changed_by_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["team_from_id"], ["teams.id"],
            name="fk_task_assignment_events_team_from_id_teams", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["team_to_id"], ["teams.id"],
            name="fk_task_assignment_events_team_to_id_teams", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_from_id"], ["users.id"],
            name="fk_task_assignment_events_user_from_id_users", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_to_id"], ["users.id"],
            name="fk_task_assignment_events_user_to_id_users", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_task_assignment_events_task_created",
        "task_assignment_events", ["task_id", "created_at", "id"], unique=False,
    )

    # Seed: the current assignee is the first entry. Set-based, so it is one
    # statement on SQLite and Postgres alike. LEFT JOINs keep a row whose
    # team/user has gone missing (name NULL, id kept).
    op.execute(
        """
        INSERT INTO task_assignment_events
            (task_id, source, changed_by_id,
             team_to_id, team_to_name, user_to_id, user_to_name)
        SELECT t.id, 'backfill', NULL,
               t.assigned_team_id, tm.name, t.assignee_user_id, u.username
        FROM tasks t
        LEFT JOIN teams tm ON tm.id = t.assigned_team_id
        LEFT JOIN users u ON u.id = t.assignee_user_id
        WHERE t.assigned_team_id IS NOT NULL OR t.assignee_user_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index("ix_task_assignment_events_task_created", table_name="task_assignment_events")
    op.drop_table("task_assignment_events")

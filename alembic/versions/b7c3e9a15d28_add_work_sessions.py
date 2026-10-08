"""Add work_sessions (team monitoring)

One row per user x task x continuous working stretch, filled write-behind by
api/work_sessions.py. See .devnotes/feature/team-monitoring/02_DESIGN.md § 2.

create_table only, so the chain builds on an empty database (CLAUDE.md rule 8).
`downgrade()` drops the table and the recorded sessions with it.

Revision ID: b7c3e9a15d28
Revises: e5a9c1d73b06
Create Date: 2026-10-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7c3e9a15d28"
down_revision: Union[str, Sequence[str], None] = "e5a9c1d73b06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "work_sessions",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("task_name", sa.String(length=255), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active_seconds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("objects_start", sa.Integer(), nullable=True),
        sa.Column("objects_end", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_work_sessions_user_id_users", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"],
            name="fk_work_sessions_task_id_tasks", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_work_sessions_task_id", "work_sessions", ["task_id"], unique=False)
    op.create_index(
        "ix_work_sessions_user_started", "work_sessions", ["user_id", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_work_sessions_user_started", table_name="work_sessions")
    op.drop_index("ix_work_sessions_task_id", table_name="work_sessions")
    op.drop_table("work_sessions")

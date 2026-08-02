"""Rename team_members to time_logs and link it to users (Teams M4)

`team_members` was never a team in any sense the Teams feature means: it is a
free-text name keyed at the primary key with an accumulated `time_logged`
counter, no foreign key to `users`, and no project relation. Leaving it named
`TeamMember` beside the new `TeamMembership` guarantees a future bug, so it is
renamed to what it actually is (.devnotes/teams/01_DESIGN.md § 8).

Renamed rather than dropped: the accumulated `time_logged` data is real, and the
table's one function — accumulate seconds against the authenticated username —
becomes *more* meaningful under individual accounts, where it finally is
per-person attribution.

`name` stays the primary key. Rewriting the accumulate logic in the same
migration that renames the table is two risky things at once; the typed
`user_id` is added alongside and the two coexist.

The backfill sets `user_id` where `name` matches a `users.username` **exactly**.
Unmatched rows keep `user_id = NULL` and are left alone: they are historical
free-text names, and a fuzzy match would be a guess about who did work
(.devnotes/teams/05_MIGRATION.md § 5 — a migration never guesses about
attribution). Zero matches is a valid outcome, not an error.

Downgrade restores the table name and drops `user_id`. It cannot restore rows
deleted while the table was named `time_logs`.

Revision ID: a4b8d3f07e54
Revises: f3a7c2e96d43
Create Date: 2026-08-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a4b8d3f07e54"
down_revision: Union[str, Sequence[str], None] = "f3a7c2e96d43"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.rename_table("team_members", "time_logs")

    # The index follows the table but keeps its old name; recreate it under the
    # new one so the schema matches what models.py declares.
    op.drop_index("ix_team_members_name", table_name="time_logs")

    with op.batch_alter_table("time_logs") as batch_op:
        batch_op.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_time_logs_user_id_users", "users", ["user_id"], ["id"]
        )

    op.create_index(op.f("ix_time_logs_name"), "time_logs", ["name"], unique=False)
    op.create_index(op.f("ix_time_logs_user_id"), "time_logs", ["user_id"], unique=False)

    # Exact match only, and idempotent: re-running sets the same rows to the same
    # values. Non-fatal by construction — a deployment whose time logs are all
    # historical free-text names simply matches nothing.
    op.get_bind().execute(
        sa.text(
            """
            UPDATE time_logs
               SET user_id = (
                   SELECT u.id FROM users u WHERE u.username = time_logs.name
               )
             WHERE user_id IS NULL
            """
        )
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_time_logs_user_id"), table_name="time_logs")
    op.drop_index(op.f("ix_time_logs_name"), table_name="time_logs")

    with op.batch_alter_table("time_logs") as batch_op:
        batch_op.drop_constraint("fk_time_logs_user_id_users", type_="foreignkey")
        batch_op.drop_column("user_id")

    op.rename_table("time_logs", "team_members")
    op.create_index("ix_team_members_name", "team_members", ["name"], unique=False)

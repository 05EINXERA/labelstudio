"""Add project_grants and the project access flags (Teams M2)

Adds the access boundary: a `project_grants` row says what one team may do on one
project (viewer/annotator/reviewer/manager). Project ownership stays
`Project.owner_id` and is never a grant row — a grant that could say "owner"
would give a project two owners with no tiebreak.

Also adds three flags to `projects`:

- restrict_to_assigned_team — opt-in enforcement of task assignment. Default
  false, which is exactly today's behaviour (assignment is advisory), so this
  migration changes how nothing behaves.
- require_distinct_reviewer — reserved for the Phase 5 self-approval rule. The
  column ships now so that check needs no second migration; nothing reads it.
- visibility — "private" (grants only) | "org" (implicit viewer for any
  authenticated user). Default "private" = today's behaviour.

Every column is added with a **server_default**, not just a Python-side default:
a Python default does not populate existing rows during an ALTER TABLE, and
nullable=False without a server default fails outright on a non-empty table.

See .devnotes/teams/02_SCHEMA.md §§ 4, 6.

Revision ID: e2f6b1d85c32
Revises: d1e5a0c74b21
Create Date: 2026-08-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e2f6b1d85c32"
down_revision: Union[str, Sequence[str], None] = "d1e5a0c74b21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "project_grants",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("granted_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"], ["projects.id"],
            name="fk_project_grants_project_id_projects", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"],
            name="fk_project_grants_team_id_teams", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by"], ["users.id"], name="fk_project_grants_granted_by_users",
        ),
        sa.PrimaryKeyConstraint("id"),
        # One row per (project, team): re-granting updates the role in place.
        # A second row would keep a revoked higher role alive through the max.
        sa.UniqueConstraint("project_id", "team_id", name="uq_project_grant"),
    )
    op.create_index(
        op.f("ix_project_grants_project_id"), "project_grants", ["project_id"], unique=False
    )
    op.create_index(
        op.f("ix_project_grants_team_id"), "project_grants", ["team_id"], unique=False
    )
    # team_id leads: the resolver goes user → memberships → team_ids → grants.
    op.create_index(
        "ix_project_grants_team_project", "project_grants", ["team_id", "project_id"], unique=False
    )

    # Batch mode so SQLite (dev) can add NOT NULL columns; a no-op rewrite
    # wrapper on Postgres (production), where these are plain ALTERs.
    with op.batch_alter_table("projects") as batch_op:
        batch_op.add_column(
            sa.Column(
                "restrict_to_assigned_team",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "require_distinct_reviewer",
                sa.Boolean(),
                server_default=sa.false(),
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "visibility",
                sa.String(length=20),
                server_default="private",
                nullable=False,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("projects") as batch_op:
        batch_op.drop_column("visibility")
        batch_op.drop_column("require_distinct_reviewer")
        batch_op.drop_column("restrict_to_assigned_team")

    op.drop_index("ix_project_grants_team_project", table_name="project_grants")
    op.drop_index(op.f("ix_project_grants_team_id"), table_name="project_grants")
    op.drop_index(op.f("ix_project_grants_project_id"), table_name="project_grants")
    op.drop_table("project_grants")

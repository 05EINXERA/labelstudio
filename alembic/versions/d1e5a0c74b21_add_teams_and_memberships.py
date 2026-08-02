"""Add teams and team_memberships (Teams M1)

Introduces the group half of the teams feature: a `teams` table and the
membership rows that put users in them, with a role *inside the team*
(owner/manager/member). That axis is deliberately independent of what a team may
do on a project, which is M2's `project_grants`.

Nothing reads these tables yet — Phase 1 lands the schema and the resolver only,
so this migration is behaviour-neutral. See .devnotes/teams/02_SCHEMA.md §§ 2–3.

No backfill: there are no teams until someone creates one, and a deployment that
never creates one behaves exactly as before.

Revision ID: d1e5a0c74b21
Revises: c2d8f1a390bb
Create Date: 2026-08-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1e5a0c74b21"
down_revision: Union[str, Sequence[str], None] = "c2d8f1a390bb"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("slug", sa.String(length=140), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=True),
        sa.Column("owner_id", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_teams_owner_id_users"),
        sa.PrimaryKeyConstraint("id"),
        # Globally unique so a team is never ambiguous in a URL or an audit line.
        sa.UniqueConstraint("slug", name="uq_teams_slug"),
    )
    op.create_index(op.f("ix_teams_slug"), "teams", ["slug"], unique=False)
    op.create_index(op.f("ix_teams_owner_id"), "teams", ["owner_id"], unique=False)

    op.create_table(
        "team_memberships",
        sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
        sa.Column("team_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("added_by", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["team_id"], ["teams.id"],
            name="fk_team_memberships_team_id_teams", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_team_memberships_user_id_users", ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["added_by"], ["users.id"], name="fk_team_memberships_added_by_users",
        ),
        sa.PrimaryKeyConstraint("id"),
        # The real defence against two managers adding the same user at once.
        sa.UniqueConstraint("team_id", "user_id", name="uq_team_membership"),
    )
    op.create_index(
        op.f("ix_team_memberships_team_id"), "team_memberships", ["team_id"], unique=False
    )
    op.create_index(
        op.f("ix_team_memberships_user_id"), "team_memberships", ["user_id"], unique=False
    )
    # user_id leads: the resolver's hot path is "all teams for this user".
    op.create_index(
        "ix_team_memberships_user_team", "team_memberships", ["user_id", "team_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_team_memberships_user_team", table_name="team_memberships")
    op.drop_index(op.f("ix_team_memberships_user_id"), table_name="team_memberships")
    op.drop_index(op.f("ix_team_memberships_team_id"), table_name="team_memberships")
    op.drop_table("team_memberships")

    op.drop_index(op.f("ix_teams_owner_id"), table_name="teams")
    op.drop_index(op.f("ix_teams_slug"), table_name="teams")
    op.drop_table("teams")

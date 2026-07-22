"""Add owner_id to projects and backfill from creator

Revision ID: a1b2c3d4e5f6
Revises: 118c799d161c
Create Date: 2026-07-22 00:00:00.000000

`projects.creator` is free-text and carries no referential integrity, so it
cannot be used for authorization. This adds a real FK to users.id and backfills
it by matching creator against users.username case-insensitively.

Verified against the current database before writing: all 9 project rows match
an existing user, so the backfill leaves no NULLs. Rows that fail to match
(possible on other deployments) are left NULL and reported by the guard below
rather than being silently reassigned.

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '118c799d161c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table('projects') as batch_op:
        batch_op.add_column(sa.Column('owner_id', sa.Integer(), nullable=True))
        batch_op.create_index(op.f('ix_projects_owner_id'), ['owner_id'], unique=False)
        batch_op.create_foreign_key('fk_projects_owner_id_users', 'users', ['owner_id'], ['id'])

    conn = op.get_bind()
    conn.execute(sa.text(
        """
        UPDATE projects
           SET owner_id = (
               SELECT u.id FROM users u
                WHERE lower(u.username) = lower(projects.creator)
           )
         WHERE creator IS NOT NULL
        """
    ))

    orphaned = conn.execute(sa.text(
        "SELECT count(*) FROM projects WHERE owner_id IS NULL"
    )).scalar()
    if orphaned:
        # Not fatal: these rows keep their creator string and can be reassigned
        # manually. They will not be visible to any owner-scoped endpoint until
        # then, so surface the count loudly instead of failing the deploy.
        print(
            f"WARNING: {orphaned} project(s) have no matching user and were left "
            "unowned. They will be inaccessible via the API until owner_id is set."
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table('projects') as batch_op:
        batch_op.drop_constraint('fk_projects_owner_id_users', type_='foreignkey')
        batch_op.drop_index(op.f('ix_projects_owner_id'))
        batch_op.drop_column('owner_id')

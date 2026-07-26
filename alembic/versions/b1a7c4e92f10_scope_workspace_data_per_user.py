"""Scope workspace_data per user

`workspace_data` was keyed on `key` alone, making it a single global blackboard:
on a shared instance every annotator read and overwrote everyone else's rows.
This adds `owner_id` and makes it part of the primary key.

Existing rows predate any notion of ownership. They are assigned to the lowest
user id (the instance's first account, in practice the operator) rather than
deleted, so a single-user instance upgrading in place keeps its state. If there
are no users at all, the rows are dropped — they cannot be attributed to anyone
and are only UI state.

Revision ID: b1a7c4e92f10
Revises: b2c3d4e5f6a7
Create Date: 2026-07-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b1a7c4e92f10'
down_revision: Union[str, Sequence[str], None] = 'b2c3d4e5f6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    connection = op.get_bind()

    # Attribute pre-existing rows to the first account, or discard them if the
    # instance has no users yet.
    first_user_id = connection.execute(
        sa.text("SELECT id FROM users ORDER BY id LIMIT 1")
    ).scalar()

    if first_user_id is None:
        connection.execute(sa.text("DELETE FROM workspace_data"))

    with op.batch_alter_table('workspace_data') as batch_op:
        batch_op.add_column(sa.Column('owner_id', sa.Integer(), nullable=True))

    if first_user_id is not None:
        connection.execute(
            sa.text("UPDATE workspace_data SET owner_id = :uid WHERE owner_id IS NULL"),
            {"uid": first_user_id},
        )

    # Rebuild the primary key as (key, owner_id). Batch mode recreates the table
    # on SQLite; on Postgres these are real ALTERs.
    with op.batch_alter_table('workspace_data') as batch_op:
        batch_op.alter_column('owner_id', existing_type=sa.Integer(), nullable=False)
        batch_op.create_primary_key('pk_workspace_data', ['key', 'owner_id'])
        batch_op.create_foreign_key(
            'fk_workspace_data_owner_id_users', 'users', ['owner_id'], ['id']
        )
        batch_op.create_index(
            op.f('ix_workspace_data_owner_id'), ['owner_id'], unique=False
        )


def downgrade() -> None:
    # Collapsing back to a global table would make `key` ambiguous across users.
    # Keep only the first user's rows so the restored primary key is valid.
    connection = op.get_bind()
    first_user_id = connection.execute(
        sa.text("SELECT id FROM users ORDER BY id LIMIT 1")
    ).scalar()
    if first_user_id is not None:
        connection.execute(
            sa.text("DELETE FROM workspace_data WHERE owner_id != :uid"),
            {"uid": first_user_id},
        )

    with op.batch_alter_table('workspace_data') as batch_op:
        batch_op.drop_index(op.f('ix_workspace_data_owner_id'))
        batch_op.drop_constraint('fk_workspace_data_owner_id_users', type_='foreignkey')
        batch_op.drop_constraint('pk_workspace_data', type_='primary')
        batch_op.create_primary_key('pk_workspace_data', ['key'])
        batch_op.drop_column('owner_id')

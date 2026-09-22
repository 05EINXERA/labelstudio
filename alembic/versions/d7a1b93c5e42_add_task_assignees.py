"""Add task_assignees + task_assignment_events: many assignees per task, with history

Revision ID: d7a1b93c5e42
Revises: c9e2f4a71b86
Create Date: 2026-09-22 10:00:00.000000

An image is worked by different people at different times, and sometimes by
several at once, but `tasks.assignee` is a single cell: reassigning overwrote
the previous name and left no record that person ever held the task. These two
tables make assignment a set (`task_assignees`) with an append-only record of
every change (`task_assignment_events`).

This migration is deliberately **additive**. `tasks.assignee` is not dropped or
altered: it stays as a denormalized mirror of the primary assignee, because
project access, the `?assignee=` filter, the Teams per-member list, the exports
and any browser tab still running cached JS all read it. The deployment is live
and cannot take downtime, so the old column has to keep answering correctly
throughout — before, during and after this runs.

The backfill copies each already-assigned task's current assignee into the new
table at position 0, so the feature never shows a blank history for work that
predates it. Those rows are stamped `assigned_by = NULL` and get no event row:
we know who holds each task, but not who assigned it or when, and inventing a
timestamp would be worse than leaving the history to start at the first real
change. `assigned_at` falls back to the task's `updated_at` where available
for the same reason — it is the closest true bound we have.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd7a1b93c5e42'
down_revision: Union[str, Sequence[str], None] = 'c9e2f4a71b86'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'task_assignees',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('member_name', sa.String(), nullable=False),
        sa.Column('position', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('assigned_by', sa.String(), nullable=True),
        sa.Column('assigned_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('task_id', 'member_name', name='uq_task_assignee'),
    )
    op.create_index(op.f('ix_task_assignees_id'), 'task_assignees', ['id'])
    op.create_index(op.f('ix_task_assignees_task_id'), 'task_assignees', ['task_id'])
    op.create_index(op.f('ix_task_assignees_member_name'), 'task_assignees', ['member_name'])

    op.create_table(
        'task_assignment_events',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('task_id', sa.Integer(), nullable=False),
        sa.Column('member_name', sa.String(), nullable=False),
        sa.Column('action', sa.String(length=16), nullable=False),
        sa.Column('actor_name', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['task_id'], ['tasks.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_task_assignment_events_id'), 'task_assignment_events', ['id'])
    op.create_index(op.f('ix_task_assignment_events_task_id'), 'task_assignment_events', ['task_id'])
    op.create_index(op.f('ix_task_assignment_events_member_name'), 'task_assignment_events', ['member_name'])
    op.create_index(op.f('ix_task_assignment_events_created_at'), 'task_assignment_events', ['created_at'])

    # Backfill the current assignee as the primary row. Written as one INSERT
    # ... SELECT so it does not load the task table into Python; on an empty
    # database (a fresh Postgres deploy) it simply inserts nothing.
    #
    # trim() guards against the whitespace variants that already exist in this
    # data (see api/auth.get_current_annotator, which resolves " Sanjita" to
    # "Sanjita"): a padded assignee string must not become a distinct assignee
    # row that no roster name matches.
    op.execute(
        """
        INSERT INTO task_assignees (task_id, member_name, position, assigned_by, assigned_at)
        SELECT id, trim(assignee), 0, NULL, COALESCE(updated_at, CURRENT_TIMESTAMP)
        FROM tasks
        WHERE assignee IS NOT NULL AND trim(assignee) <> ''
        """
    )


def downgrade() -> None:
    # tasks.assignee was never stopped being written, so dropping these tables
    # returns the app to single-assignee behaviour with the primary assignee
    # intact. Only the extra assignees and the history are lost.
    op.drop_index(op.f('ix_task_assignment_events_created_at'), table_name='task_assignment_events')
    op.drop_index(op.f('ix_task_assignment_events_member_name'), table_name='task_assignment_events')
    op.drop_index(op.f('ix_task_assignment_events_task_id'), table_name='task_assignment_events')
    op.drop_index(op.f('ix_task_assignment_events_id'), table_name='task_assignment_events')
    op.drop_table('task_assignment_events')

    op.drop_index(op.f('ix_task_assignees_member_name'), table_name='task_assignees')
    op.drop_index(op.f('ix_task_assignees_task_id'), table_name='task_assignees')
    op.drop_index(op.f('ix_task_assignees_id'), table_name='task_assignees')
    op.drop_table('task_assignees')

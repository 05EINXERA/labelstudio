"""Partial index for the comment-annotation count on the task gallery

Revision ID: b8d4e1f70a23
Revises: a7f3c9d20e14
Create Date: 2026-09-13 11:30:00.000000

`GET /api/tasks` counts comment annotations per task for the gallery, which
every open gallery re-polls every 30s. Comments are a rounding error in the
table — 282 rows out of 374,580 — but they live alongside the polygons, so
finding them means fetching every annotation heap block belonging to the page's
tasks.

A partial index on the `comment` rows only lets that count be answered from an
index a few hundred kB in size instead of riding along with the full per-task
heap scan. It is partial (not a plain index on `type`) because indexing 374k
polygon rows to find 282 comments would cost more to maintain on every
annotation write than it saves on read.

Postgres-only: SQLite accepts the partial-index syntax too, but the dev
database is small enough that it never mattered there.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'b8d4e1f70a23'
down_revision: Union[str, Sequence[str], None] = 'a7f3c9d20e14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add the partial indexes.

    The predicates are plain strings, not `op.inline_literal(...)`: that helper
    renders a quoted *value*, which Postgres then rejects as a boolean
    (`WHERE 'type = ''comment'''`).
    """
    op.create_index(
        'ix_annotations_task_id_comment',
        'annotations',
        ['task_id'],
        unique=False,
        postgresql_where="type = 'comment'",
        sqlite_where="type = 'comment'",
    )

    # The presence sweep (`api/presence.py close_stale_sessions`) runs on every
    # /api/team/ping and every /api/team read, filtering `logout_at IS NULL`.
    # There was no index for it, so each call seq-scanned the whole append-only
    # table. Only the handful of currently-open sessions are indexed.
    op.create_index(
        'ix_login_sessions_open',
        'login_sessions',
        ['last_seen_at'],
        unique=False,
        postgresql_where="logout_at IS NULL",
        sqlite_where="logout_at IS NULL",
    )


def downgrade() -> None:
    """Drop the partial indexes."""
    op.drop_index('ix_login_sessions_open', table_name='login_sessions')
    op.drop_index('ix_annotations_task_id_comment', table_name='annotations')

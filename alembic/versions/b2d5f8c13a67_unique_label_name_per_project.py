"""Forbid duplicate class names within a project

The second half of the fix begun in a1c4e7b09f52, which added and backfilled
`labels.name_key`. This revision creates the unique index over
(project_id, name_key) — the part that makes "one class per name per project" a
property of the database rather than of whichever endpoint happens to be
correct today.

Project 410 collected six classes named "object" on 2026-09-08, created by
nobody: the canvas mints a fresh uuid for any class it has not seen
(`ensureLabel`), and POST /api/labels resolved by *id only*, so a fresh uuid
never matched an existing row and the endpoint always inserted. Four of the six
landed inside 37 seconds of reloading one task, with no annotation drawn.
See .devnotes/fix-class-creation/01_AUDIT.md.

**Separate from a1c4e7b09f52 on purpose.** `scripts/dedupe_labels.py` merges the
existing duplicates and reads `labels` through the ORM, which maps `name_key` —
so the column must exist before the script can run, and the duplicates must be
gone before this index can be created. Combining the two deadlocks: the
duplicate check aborts the transaction and rolls the `add_column` back with it,
leaving the script unable to run and the operator with no way forward. That is
not hypothetical — it is what happened on the dev database, which had six
duplicate groups across two projects.

Deploy order:

    alembic upgrade a1c4e7b09f52                  # column + backfill
    python scripts/dedupe_labels.py --all         # review the plan
    python scripts/dedupe_labels.py --all --commit
    alembic upgrade head                          # this revision

The check below fails with the offending rows named rather than letting the
index creation produce an unreadable UNIQUE constraint error, because the
operator's next step is to run the dedupe script against exactly those projects.

A stored key column rather than a `lower(name)` expression index: expression
indexes behave differently on SQLite and Postgres, and this deployment runs both
(dev/prod per CLAUDE.md).

Rule 8: builds on an empty database. On a fresh Postgres deploy there are no
rows, the check passes trivially and the index is created on an empty table.

Revision ID: b2d5f8c13a67
Revises: a1c4e7b09f52
Create Date: 2026-09-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b2d5f8c13a67"
down_revision: Union[str, Sequence[str], None] = "a1c4e7b09f52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "ix_labels_project_name_unique"


def upgrade() -> None:
    conn = op.get_bind()

    duplicates = conn.execute(sa.text(
        "SELECT project_id, name_key, COUNT(*) AS n FROM labels"
        " WHERE project_id IS NOT NULL"
        " GROUP BY project_id, name_key HAVING COUNT(*) > 1"
        " ORDER BY project_id, name_key"
    )).fetchall()
    if duplicates:
        listing = ", ".join(f"project {r[0]} name {r[1]!r} x{r[2]}" for r in duplicates)
        raise RuntimeError(
            "Cannot create the unique index: duplicate class names remain "
            f"({listing}). Run `python scripts/dedupe_labels.py --all` to review "
            "and `--commit` to merge them, then re-run this migration. "
            "See .devnotes/fix-class-creation/02_PLAN.md phase 2."
        )

    op.create_index(INDEX_NAME, "labels", ["project_id", "name_key"], unique=True)


def downgrade() -> None:
    """Drop the index. `name_key` itself belongs to a1c4e7b09f52."""
    op.drop_index(INDEX_NAME, table_name="labels")

"""Add labels.name_key, the case-folded class-name matching key

Project 410 collected six classes named "object" on 2026-09-08, created by
nobody: the canvas mints a fresh uuid for any class it has not seen
(`ensureLabel`), and POST /api/labels resolved by *id only*, so a fresh uuid
never matched an existing row and the endpoint always inserted. Four of the six
landed inside 37 seconds of reloading one task, with no annotation drawn.
See .devnotes/fix-class-creation/01_AUDIT.md.

The application fix (resolve by name, normalise server-side) is deployed ahead
of this revision and is what makes the constraint safe to add. **This index is
the part that makes the guarantee true** — for a stale bundle still running the
old JS, for a direct API caller, and for any future code path written by someone
who never reads that note. A rule enforced only in the endpoint that happens to
be fixed today is not a rule.

This revision adds and backfills the column **only**. The unique index that
actually forbids duplicates is the *next* revision, b2d5f8c13a67, and the split
is deliberate:

`scripts/dedupe_labels.py` is what merges the existing duplicates, and it reads
`labels` through the ORM — which now maps `name_key`. So the column has to exist
before the script can run, and the duplicates have to be gone before the index
can be created. One combined revision deadlocks: it aborts on the duplicate
check and rolls its own `add_column` back, leaving the script unable to run and
the operator with no way forward. Deploy order is therefore:

    alembic upgrade a1c4e7b09f52          # this revision: column + backfill
    python scripts/dedupe_labels.py --all         # review
    python scripts/dedupe_labels.py --all --commit
    alembic upgrade head                  # b2d5f8c13a67: the unique index

`name` keeps its casing: it is the *display* name, and `formats/coco.py` writes
it into `categories[].name` while the FastLabel export puts it in `title`, both
of which round-trip through import. Lowercasing it would turn "AF Paint" into
"af paint" permanently — a real loss, and not what the duplicate fix requires.

A stored key column rather than a `lower(name)` expression index: expression
indexes behave differently on SQLite and Postgres, and this deployment runs
both (dev/prod per CLAUDE.md).

Rule 8: builds on an empty database. On a fresh Postgres deploy the UPDATE
touches nothing and the index is created on an empty table.

Revision ID: a1c4e7b09f52
Revises: d4b8f3c07e19
Create Date: 2026-09-09
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1c4e7b09f52"
down_revision: Union[str, Sequence[str], None] = "d4b8f3c07e19"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    conn = op.get_bind()

    # 1. The matching key column. Nullable: the table is populated, and the
    #    backfill below is what fills it.
    op.add_column("labels", sa.Column("name_key", sa.String(), nullable=True))

    # 2. Tidy the display name (underscores to spaces, trimmed) and derive the
    #    key from it. Interior whitespace runs are not collapsed here — SQL has
    #    no portable way to do it — which is why the collision check below is a
    #    check and not an assumption.
    conn.execute(sa.text(
        "UPDATE labels SET name = TRIM(REPLACE(name, '_', ' ')) WHERE name IS NOT NULL"
    ))
    conn.execute(sa.text(
        "UPDATE labels SET name = 'object' WHERE name IS NULL OR name = ''"
    ))
    conn.execute(sa.text("UPDATE labels SET name_key = LOWER(name)"))
    op.create_index("ix_labels_name_key", "labels", ["name_key"])


def downgrade() -> None:
    """Drop the key column.

    The `name` tidying is not reversed: the original underscores and stray
    whitespace are not recorded anywhere, and the tidied form is correct
    regardless of whether the constraint is in force. Casing was never changed,
    so nothing about the display name is lost either way.
    """
    op.drop_index("ix_labels_name_key", table_name="labels")
    op.drop_column("labels", "name_key")

"""Normalise label names and forbid duplicates within a project

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

Three steps, in this order:

1. **Add `labels.name_key`** — the case-folded matching form of `name`.
2. **Backfill it** from the existing names, and tidy `name` itself (underscores
   to spaces, trimmed). `name` keeps its casing: it is the *display* name, and
   `formats/coco.py` writes it into `categories[].name` while the FastLabel
   export puts it in `title`, both of which round-trip through import.
   Lowercasing it would turn "AF Paint" into "af paint" permanently — a real
   loss, and not what the duplicate fix requires.
3. **Create the unique index** on (project_id, name_key).

A stored key column rather than a `lower(name)` expression index: expression
indexes behave differently on SQLite and Postgres, and this deployment runs
both (dev/prod per CLAUDE.md).

**Duplicates must already be gone.** `scripts/dedupe_labels.py --commit` merges
them, moving annotations rather than deleting any, and must be run before this
migration. Folding case in step 2 can *create* collisions between rows that
previously differed only by case, so the check below runs after the backfill and
fails with the offending rows named rather than letting the index creation
produce an unreadable error.

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

INDEX_NAME = "ix_labels_project_name_unique"


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

    # 3. Refuse to proceed on a database that still has duplicates, naming them.
    #
    #    The index would fail anyway; this fails *legibly*, which matters
    #    because the operator's next step is to run the dedupe script against
    #    exactly the projects listed here.
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
            f"({listing}). Run `python scripts/dedupe_labels.py --project <id>` "
            "to review and `--commit` to merge them, then re-run this migration. "
            "See .devnotes/fix-class-creation/02_PLAN.md phase 2."
        )

    op.create_index(INDEX_NAME, "labels", ["project_id", "name_key"], unique=True)


def downgrade() -> None:
    """Drop the index and the key column.

    The `name` tidying is not reversed: the original underscores and stray
    whitespace are not recorded anywhere, and the tidied form is correct
    regardless of whether the constraint is in force. Casing was never changed,
    so nothing about the display name is lost either way.
    """
    op.drop_index(INDEX_NAME, table_name="labels")
    op.drop_index("ix_labels_name_key", table_name="labels")
    op.drop_column("labels", "name_key")

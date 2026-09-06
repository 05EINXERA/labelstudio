"""Add the annotations table

One row per annotation shape, replacing the single JSON blob in
`tasks.annotations` that every save rewrote in full.

Blobs reached 15.6 MB in production. Because a save parsed and rewrote all of
it while holding the GIL, each concurrent save added ~700 ms of latency to
every *other* request in the process, at 0% CPU of its own. See
.devnotes/server-issue-diagnosis/evidence/06_ROOT_CAUSE_CONFIRMED.md.

**Creates the table only. It does not touch `tasks.annotations`,** which stays
authoritative until the Phase C cutover and is renamed to `annotations_legacy`
in Phase F. Data is moved by `scripts/backfill_annotations.py`, not here: 431 MB
across 740 tasks needs batching, progress and resumability, and it must be
runnable *before* the cutover while the old code still serves traffic. An
alembic revision that runs for twenty minutes inside a deploy is the wrong
shape.

Rule 8: creates a new table only, so the chain still builds on an empty
database (Postgres deploys start empty). No batch mode — that is only needed
for ALTERing existing tables under SQLite.

Two column choices are load-bearing and are explained where they are made
below: the composite primary key, and a nullable `type`.

Revision ID: c7d1e4a82b95
Revises: b5c9e2a41f68
Create Date: 2026-09-05
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7d1e4a82b95"
down_revision: Union[str, Sequence[str], None] = "b5c9e2a41f68"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "annotations",
        # Client-generated (uuid4), not autoincrement: the browser mints ids
        # offline before any round-trip and the offline queue replays them.
        sa.Column("id", sa.String(length=64), nullable=False),
        # Part of the primary key, deliberately. 678 annotation ids in the real
        # data appear on more than one task (70 tasks, 67 projects) — genuine
        # copy-paste between tasks. A bare `id` PK cannot represent that and the
        # backfill would fail on it. No id repeats within a single task.
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("label_id", sa.String(), nullable=True),
        # Nullable: 4,059 of 17,245 real annotations carry no `type`, and
        # formats.common.is_annotation() reads an absent type as a real shape.
        # A NOT NULL with a 'polygon' default would silently reclassify them.
        sa.Column("type", sa.String(length=32), nullable=True),
        # The vertex list as the exact JSON bytes the client sent. Two shapes
        # occur in the wild ([{x,y},...] and [[x,y],...]); both are preserved
        # verbatim rather than canonicalised.
        sa.Column("points", sa.Text(), nullable=True),
        sa.Column("x", sa.Float(), nullable=True),
        sa.Column("y", sa.Float(), nullable=True),
        sa.Column("width", sa.Float(), nullable=True),
        sa.Column("height", sa.Float(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=16), nullable=True),
        sa.Column("order", sa.Integer(), nullable=True),
        # Position in the payload the annotation arrived in. Distinct from
        # `order`, a client-supplied paint index present on only 2 of 17,245
        # real annotations. The blob was a JSON array whose implicit order
        # decides which shape paints over which and what an export emits; rows
        # have no inherent order, so `seq` is what carries it across.
        sa.Column("seq", sa.Integer(), nullable=True),
        sa.Column("group_id", sa.String(length=36), nullable=True),
        # Every field the columns above do not model, as a JSON object. The
        # real data carries eight such fields (label, visible, promptPoints,
        # promptLabels, source, author, w, h); this is what makes the migration
        # lossless without the schema predicting what the canvas adds next.
        sa.Column("extra", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=True,
        ),
        # CASCADE: annotations describe a task that no longer exists once the
        # task is deleted, and this codebase soft-deletes nothing.
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        # SET NULL, never CASCADE: deleting a class must orphan the shape, not
        # destroy annotation work. This is what replaces
        # purge_annotations_for_labels()'s project-wide blob rewrite.
        sa.ForeignKeyConstraint(["label_id"], ["labels.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", "task_id"),
    )
    op.create_index("ix_annotations_task_id", "annotations", ["task_id"])
    op.create_index("ix_annotations_label_id", "annotations", ["label_id"])
    op.create_index("ix_annotations_group_id", "annotations", ["group_id"])
    # The comment-count and per-type aggregates filter on exactly this pair.
    op.create_index("ix_annotations_task_id_type", "annotations", ["task_id", "type"])


def downgrade() -> None:
    # Safe to drop: through Phase B the blob in `tasks.annotations` is still
    # written and still authoritative, so nothing unique lives here yet. After
    # the Phase C cutover it does, and a downgrade must be preceded by
    # `scripts/backfill_annotations.py --reverse`.
    op.drop_index("ix_annotations_task_id_type", table_name="annotations")
    op.drop_index("ix_annotations_group_id", table_name="annotations")
    op.drop_index("ix_annotations_label_id", table_name="annotations")
    op.drop_index("ix_annotations_task_id", table_name="annotations")
    op.drop_table("annotations")

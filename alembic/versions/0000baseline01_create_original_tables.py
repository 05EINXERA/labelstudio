"""Create the five original tables (pre-baseline)

Revision ID: 0000baseline01
Revises:
Create Date: 2026-08-02

Why this exists
---------------
`97472310f3a2` ("Initial migration") is unedited `--autogenerate` output produced
against an already-populated SQLite database. It only `ALTER`s columns and
`CREATE INDEX`es — it never `CREATE TABLE`s anything. Production's schema was
built by the old `Base.metadata.create_all()` and then stamped, so production has
never exercised the chain from empty and is unaffected.

Against a genuinely empty database — what a Postgres deploy starts from, and what
CLAUDE.md rule 8 requires — the chain died on its first statement:

    psycopg.errors.UndefinedTable: relation "projects" does not exist
    [SQL: ALTER TABLE projects ALTER COLUMN id SET NOT NULL]

This revision inserts the missing first link.

The shape these tables are created in
-------------------------------------
They are created in their **post-`97472310f3a2`** shape, not the older one that
revision expects to alter. That is deliberate, and it is why `97472310f3a2`
carries a guard that turns it into a no-op when it finds the tables already
current (see the comment there).

The alternative — creating the *older* shape so `97472310f3a2` could do its work
— does not run on SQLite at all: that revision's `op.alter_column` calls are bare
rather than wrapped in `op.batch_alter_table`, and SQLite cannot `ALTER COLUMN`.
It has never been runnable on SQLite; it only ever "ran" on the pre-populated
database that was later `stamp`ed. Every subsequent revision in this chain does
use batch mode, so this is a defect isolated to the first one.

`97472310f3a2`'s body is deliberately **not** rewritten: production has it
stamped, and rewriting applied history desynchronises the two databases. It gains
only a skip-if-already-current guard, which is inert on any database that
actually needs its ALTERs.

Idempotent: each `CREATE TABLE` is skipped if the table is already present, so an
operator who runs `upgrade head` against a database that predates this revision
(rather than `stamp`ing it) does not crash.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0000baseline01"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# The tables this revision owns, in creation order (FK dependencies first).
_TABLES = ("users", "projects", "tasks", "labels", "team_members", "workspace_data")


def _existing_tables() -> set:
    return set(sa.inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    existing = _existing_tables()

    if "users" not in existing:
        op.create_table(
            "users",
            sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
            sa.Column("username", sa.String(), nullable=True),
            sa.Column("hashed_password", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_users_id"), "users", ["id"], unique=False)
        op.create_index(op.f("ix_users_username"), "users", ["username"], unique=True)

    if "projects" not in existing:
        # `time_spent` is absent and `id` is NOT NULL: this is the shape
        # 97472310f3a2 leaves behind, not the one it starts from.
        op.create_table(
            "projects",
            sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("slug", sa.String(), nullable=True),
            sa.Column("type", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=True),
            sa.Column("creator", sa.String(), nullable=True),
            sa.Column("assignee", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_projects_id"), "projects", ["id"], unique=False)

    if "tasks" not in existing:
        op.create_table(
            "tasks",
            sa.Column("id", sa.Integer(), nullable=False, autoincrement=True),
            sa.Column("project_id", sa.Integer(), nullable=True),
            sa.Column("image_path", sa.String(), nullable=True),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("status", sa.String(), server_default=sa.text("'New'"), nullable=True),
            sa.Column("assignee", sa.String(), nullable=True),
            sa.Column("time_spent", sa.Integer(), server_default=sa.text("0"), nullable=True),
            sa.Column("annotations", sa.Text(), nullable=True),
            # `updated_at` predates the migration chain: it was in the original
            # create_all() schema and no revision ever adds it, so it has to be
            # created here or the built schema drifts from models.Task.
            sa.Column(
                "updated_at",
                sa.DateTime(),
                server_default=sa.func.now(),
                onupdate=sa.func.now(),
                nullable=True,
            ),
            sa.ForeignKeyConstraint(["project_id"], ["projects.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_tasks_id"), "tasks", ["id"], unique=False)
        op.create_index(op.f("ix_tasks_project_id"), "tasks", ["project_id"], unique=False)

    if "labels" not in existing:
        # `project_id` is added by 118c799d161c, so it is absent here.
        op.create_table(
            "labels",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("name", sa.String(), nullable=True),
            sa.Column("color", sa.String(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(op.f("ix_labels_id"), "labels", ["id"], unique=False)

    if "team_members" not in existing:
        op.create_table(
            "team_members",
            sa.Column("name", sa.String(), nullable=False),
            sa.Column("time_logged", sa.Integer(), server_default=sa.text("0"), nullable=True),
            sa.PrimaryKeyConstraint("name"),
        )
        op.create_index(op.f("ix_team_members_name"), "team_members", ["name"], unique=False)

    if "workspace_data" not in existing:
        # `owner_id` joins the primary key in b1a7c4e92f10, so it is absent here.
        # The primary key is named explicitly: b1a7c4e92f10 rebuilds it as
        # (key, owner_id) under the name `pk_workspace_data` and its downgrade
        # drops that name. Letting Postgres assign its default
        # `workspace_data_pkey` here would leave that migration unable to find
        # the constraint it needs to replace.
        op.create_table(
            "workspace_data",
            sa.Column("key", sa.String(), nullable=False),
            sa.Column("value", sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint("key", name="pk_workspace_data"),
        )
        op.create_index(op.f("ix_workspace_data_key"), "workspace_data", ["key"], unique=False)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_table(table)

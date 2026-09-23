"""Add attendance_observations, attendance_days and users.is_admin (R1)

The schema for the annotator attendance register
(.devnotes/attendance-feature/04-decision-and-impl-plan.md § 3). This revision
is schema only — no code reads or writes either table yet, and `is_admin` is
false for everyone, so the migration changes how nothing behaves. That is what
makes R1 independently revertible (§ 7).

Three things worth knowing before changing any of it:

- **`attendance_observations` is strictly append-only.** No UPDATE and no
  DELETE from application code, with the 31-day retention prune (Q1) as the
  sole exception. A corrected break is a new row pair, never an edit (Q24).
- **Both `user_id` FKs are `ON DELETE SET NULL`, never CASCADE** (Q23).
  Attendance is evidence about hours worked and a departing employee's record
  is the one most likely to be wanted afterwards. `attendance_days.username` is
  the denormalised snapshot that keeps such a row readable once the account is
  gone (Q30) — a null FK with no name is storage without evidence.
- **Every NOT NULL column added or created here carries a `server_default`.**
  A Python-side `default=` does not populate existing rows during an ALTER
  TABLE, and on `users` — 327 live rows — `nullable=False` without one fails
  outright. Same reasoning as e2f6b1d85c32.

`attendance_days` is the permanent record: raw observations prune at 31 days,
so the rollup must always run ahead of the prune. `uq_attendance_day` is what
makes the rollup an idempotent UPSERT, which in turn is what lets a day be
re-rolled when a manual break is backdated into it (Q25).

Revision ID: 72a3921edd78
Revises: b2d5f8c13a67
Create Date: 2026-09-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "72a3921edd78"
down_revision: Union[str, Sequence[str], None] = "b2d5f8c13a67"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "attendance_observations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        # UTC and tz-aware (CLAUDE.md rule 7). Bucketing into local days is a
        # read-time concern using ZoneInfo("Asia/Kathmandu") — never a numeric
        # offset, because Nepal is +05:45 and a whole-hour assumption passes
        # every UTC-written test while being 45 minutes wrong on real data.
        sa.Column("seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("instance_id", sa.String(length=64), nullable=False),
        # seen | active | login | logout | break_start | break_end
        #      | break_manual_start | break_manual_end
        sa.Column(
            "kind", sa.String(length=24), server_default="seen", nullable=False
        ),
        # When the ROW was written, distinct from `seen_at` (when the observed
        # moment was). Equal for observed rows; hours apart for a break entered
        # from the profile page after the fact. That gap is how an admin sees a
        # break was reconstructed rather than declared (Q16), with no approval
        # queue and no mutable state.
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("entered_by", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_attendance_obs_user_id_users", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"], ["tasks.id"],
            name="fk_attendance_obs_task_id_tasks", ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["entered_by"], ["users.id"],
            name="fk_attendance_obs_entered_by_users", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    # The retention prune and the whole-instance day query filter on seen_at
    # alone.
    op.create_index(
        "ix_attendance_obs_seen_at", "attendance_observations", ["seen_at"],
        unique=False,
    )
    # user_id leads: sessionisation reads one user's ordered day. No separate
    # single-column index on user_id — this one serves a user_id-only lookup
    # from its leading column, and a redundant index is write cost on the
    # table that takes every observation.
    op.create_index(
        "ix_attendance_obs_user_seen", "attendance_observations",
        ["user_id", "seen_at"], unique=False,
    )

    op.create_table(
        "attendance_days",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        # Denormalised so a kept row stays readable after the account is gone
        # (Q23 + Q30).
        sa.Column("username", sa.String(), nullable=False),
        # The local (Asia/Kathmandu) day this record covers.
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("instance_id", sa.String(length=64), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        # logout | timeout | open. A timeout-derived end is never rendered as a
        # plain logout time: that overstates the precision of an attendance
        # record (02-requirements-definitions.md § 2).
        sa.Column("end_reason", sa.String(length=16), nullable=False),
        sa.Column(
            "session_count", sa.Integer(), server_default="0", nullable=False
        ),
        # Present time EXCLUDES declared breaks.
        sa.Column(
            "present_seconds", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "break_seconds", sa.Integer(), server_default="0", nullable=False
        ),
        # Counted separately from break_seconds so the admin can see how much
        # of a day was reconstructed rather than observed (Q16).
        sa.Column(
            "manual_break_seconds", sa.Integer(), server_default="0", nullable=False
        ),
        # From `active`-kind observations, NOT from time_logs, which is a
        # lifetime total with no date column at all (Q18).
        sa.Column(
            "active_seconds", sa.Integer(), server_default="0", nullable=False
        ),
        # "Touched", never "completed": no author column exists on the
        # annotation write path, so per-user completion is not derivable and no
        # column may imply it (CLAUDE.md rule 11a).
        sa.Column(
            "tasks_touched", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column(
            "tasks_reviewed", sa.Integer(), server_default="0", nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"],
            name="fk_attendance_days_user_id_users", ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        # One row per person per local day per instance. The rollup is
        # re-runnable, so it must UPSERT rather than accumulate duplicates —
        # which is also what lets a day be re-rolled after a late manual break
        # is backdated into it (Q25).
        sa.UniqueConstraint(
            "user_id", "local_date", "instance_id", name="uq_attendance_day"
        ),
    )
    op.create_index(
        "ix_attendance_days_local_date", "attendance_days", ["local_date"],
        unique=False,
    )
    op.create_index(
        "ix_attendance_days_user_id", "attendance_days", ["user_id"], unique=False
    )

    # Batch mode so SQLite (dev) can add a NOT NULL column; a plain ALTER on
    # Postgres (production). server_default=false() is what keeps the 327
    # existing rows valid and the migration behaviour-neutral — no admin is
    # created here. The first one is granted by scripts/grant_admin.py, which
    # is the only writer of this column anywhere (Q13): with no API path to it,
    # privilege escalation over HTTP is impossible.
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_admin", sa.Boolean(), server_default=sa.false(), nullable=False
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("is_admin")

    op.drop_index("ix_attendance_days_user_id", table_name="attendance_days")
    op.drop_index("ix_attendance_days_local_date", table_name="attendance_days")
    op.drop_table("attendance_days")

    op.drop_index(
        "ix_attendance_obs_user_seen", table_name="attendance_observations"
    )
    op.drop_index("ix_attendance_obs_seen_at", table_name="attendance_observations")
    op.drop_table("attendance_observations")

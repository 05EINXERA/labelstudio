"""The work_sessions revision applies on top of its parent and reverses.

The full chain cannot be replayed on SQLite from empty: an older revision issues
the Postgres-only `ALTER COLUMN id SET NOT NULL`. That predates this feature, so
this test builds the two parent tables from the models, stamps the parent head,
and exercises only this revision. (Run `alembic upgrade head` on a throwaway
Postgres for the whole-chain check; see .devnotes/team-monitoring/02_PLAN.md.)
"""
import os
import subprocess
import sys
import tempfile

import sqlalchemy as sa

import models

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARENT = "f3a9d2c61e57"


def _alembic(url, data_dir, *args):
    # Never let a scratch run touch anything but the throwaway file.
    assert url.startswith("sqlite:///") and data_dir.replace(os.sep, "/") in url
    env = dict(os.environ, DATABASE_URL=url, DATA_DIR=data_dir)
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=300,
    )


def test_revision_applies_on_parent_creates_indexes_and_reverses():
    with tempfile.TemporaryDirectory() as d:
        url = "sqlite:///" + os.path.join(d, "m.db").replace(os.sep, "/")
        eng = sa.create_engine(url)
        # users + tasks (and tasks' own parents) exist at the parent revision.
        parents = [t for n, t in models.Base.metadata.tables.items() if n != "work_sessions"]
        models.Base.metadata.create_all(eng, tables=parents)
        eng.dispose()

        res = _alembic(url, d, "stamp", PARENT)
        assert res.returncode == 0, res.stderr
        res = _alembic(url, d, "upgrade", "head")
        assert res.returncode == 0, res.stderr

        eng = sa.create_engine(url)
        insp = sa.inspect(eng)
        assert "work_sessions" in insp.get_table_names()
        idx = {i["name"]: i["column_names"] for i in insp.get_indexes("work_sessions")}
        assert idx["ix_work_sessions_task_id"] == ["task_id"]
        assert idx["ix_work_sessions_member_started"] == ["member_name", "started_at"]
        fks = {fk["referred_table"]: fk["options"].get("ondelete") for fk in insp.get_foreign_keys("work_sessions")}
        assert fks == {"users": "SET NULL", "tasks": "SET NULL"}
        eng.dispose()

        res = _alembic(url, d, "downgrade", "-1")
        assert res.returncode == 0, res.stderr
        eng = sa.create_engine(url)
        assert "work_sessions" not in sa.inspect(eng).get_table_names()
        eng.dispose()

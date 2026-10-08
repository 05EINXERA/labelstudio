"""The assignment-history migration seeds today's assignee as the first entry.

.devnotes/features/task-assignment-history/02_DESIGN.md § 7. Runs against a
throwaway SQLite file, upgraded to the revision *before* the new one so there is
data for the backfill to find.
"""
import pathlib

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PREVIOUS = "72a3921edd78"


@pytest.fixture
def cfg_url(tmp_path):
    url = f"sqlite:///{(tmp_path / 'chain.db').as_posix()}"
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.attributes["sqlalchemy.url"] = url
    return cfg, url


def test_backfill_seeds_only_assigned_tasks(cfg_url):
    cfg, url = cfg_url
    command.upgrade(cfg, PREVIOUS)

    engine = sa.create_engine(url)
    with engine.begin() as c:
        c.execute(sa.text("INSERT INTO users (id, username) VALUES (1, 'alice')"))
        c.execute(sa.text("INSERT INTO teams (id, name, slug, owner_id) VALUES (1, 'Team X', 'team-x', 1)"))
        for tid, team, user in [(1, 1, 1), (2, 1, None), (3, None, None)]:
            c.execute(
                sa.text(
                    "INSERT INTO tasks (id, assigned_team_id, assignee_user_id) "
                    "VALUES (:i, :t, :u)"
                ),
                {"i": tid, "t": team, "u": user},
            )
    engine.dispose()

    command.upgrade(cfg, "head")

    engine = sa.create_engine(url)
    with engine.connect() as c:
        rows = c.execute(
            sa.text(
                "SELECT task_id, source, changed_by_id, team_to_name, user_to_name, "
                "team_from_id, user_from_id, created_at "
                "FROM task_assignment_events ORDER BY task_id"
            )
        ).fetchall()
    engine.dispose()

    assert [r.task_id for r in rows] == [1, 2]  # task 3 is unassigned: no seed
    assert all(r.source == "backfill" and r.changed_by_id is None for r in rows)
    assert (rows[0].team_to_name, rows[0].user_to_name) == ("Team X", "alice")
    assert (rows[1].team_to_name, rows[1].user_to_name) == ("Team X", None)
    assert all(r.team_from_id is None and r.user_from_id is None for r in rows)
    assert all(r.created_at is not None for r in rows)


def test_downgrade_drops_the_table(cfg_url):
    cfg, url = cfg_url
    command.upgrade(cfg, "head")
    command.downgrade(cfg, PREVIOUS)
    engine = sa.create_engine(url)
    try:
        assert "task_assignment_events" not in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()

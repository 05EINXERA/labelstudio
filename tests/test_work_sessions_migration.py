"""The work_sessions migration builds from empty, carries both indexes, and reverses.

.devnotes/feature/team-monitoring/02_DESIGN.md § 2. Runs against a throwaway
SQLite file.
"""
import pathlib

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PREVIOUS = "e5a9c1d73b06"


@pytest.fixture
def cfg_url(tmp_path):
    url = f"sqlite:///{(tmp_path / 'chain.db').as_posix()}"
    cfg = Config(str(REPO_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(REPO_ROOT / "alembic"))
    cfg.attributes["sqlalchemy.url"] = url
    return cfg, url


def _inspect(url):
    engine = sa.create_engine(url)
    try:
        return sa.inspect(engine).get_table_names(), engine
    finally:
        engine.dispose()


def test_builds_on_an_empty_database_with_both_indexes(cfg_url):
    cfg, url = cfg_url
    command.upgrade(cfg, "head")
    engine = sa.create_engine(url)
    insp = sa.inspect(engine)
    assert "work_sessions" in insp.get_table_names()
    indexed = {tuple(i["column_names"]) for i in insp.get_indexes("work_sessions")}
    # task_id must be indexed: it is an ON DELETE SET NULL FK (see the model).
    assert ("task_id",) in indexed
    assert ("user_id", "started_at") in indexed
    engine.dispose()


def test_downgrade_drops_the_table(cfg_url):
    cfg, url = cfg_url
    command.upgrade(cfg, "head")
    command.downgrade(cfg, PREVIOUS)
    names, _ = _inspect(url)
    assert "work_sessions" not in names

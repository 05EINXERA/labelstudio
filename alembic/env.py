from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

import sys
import os
sys.path.insert(0, os.path.abspath('.'))
import models
from config import DATABASE_URL

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = models.Base.metadata

# Honour DATABASE_URL, the same way database.py does.
#
# alembic.ini hardcodes `sqlite:///./workspace.db`, so without this every
# migration runs against the CWD regardless of where the database actually
# lives — silently migrating a throwaway file while the real database stays on
# an old schema. config.DATABASE_URL is the single source of truth (it resolves
# to the DATA_DIR SQLite file when the env var is unset); the ini value is only
# a fallback for a bare `alembic` invocation with no app config importable.
#
# `%` is escaped because ConfigParser would otherwise treat it as interpolation
# syntax — Postgres passwords routinely contain percent-encoded characters.
#
# An explicit URL set by a programmatic caller wins. `config` is imported at
# module scope and caches .env, so a caller that sets os.environ["DATABASE_URL"]
# after that import would otherwise be silently ignored and its migrations sent
# at whatever .env points to — in practice the live database. That is how
# tests/test_teams_migrations.py, which must only ever touch a throwaway file,
# would have run against the deployment's Postgres instance.
_explicit_url = config.attributes.get("sqlalchemy.url")
if _explicit_url:
    config.set_main_option("sqlalchemy.url", _explicit_url.replace("%", "%%"))
else:
    config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # SQLite cannot ALTER most column properties; batch mode emulates it
            # by rebuilding the table. No-op on Postgres, so it is safe to leave
            # on for both backends.
            render_as_batch=connection.dialect.name == "sqlite",
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

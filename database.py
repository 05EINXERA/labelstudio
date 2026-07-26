"""Database engine and session management.

Supports both SQLite (development, single user) and PostgreSQL (the multi-user
LAN deployment). The URL comes from `config.DATABASE_URL`; everything
dialect-specific is switched on `config.IS_SQLITE` so neither backend leaks
settings into the other.

Why Postgres for the shared deployment: SQLite serialises every write behind a
single lock, so ~30 annotators saving annotation blobs contend on one writer and
hit the busy-timeout as intermittent 500s. Postgres has real concurrent writers
and a connection pool. See .devnotes/deployment-hardening/00_AUDIT.md P1-5.
"""
import logging
import time

from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import declarative_base, sessionmaker

from config import (
    DATABASE_URL,
    DB_MAX_OVERFLOW,
    DB_POOL_RECYCLE,
    DB_POOL_SIZE,
    DB_POOL_TIMEOUT,
    IS_SQLITE,
)

logger = logging.getLogger(__name__)

SQLALCHEMY_DATABASE_URL = DATABASE_URL

if IS_SQLITE:
    # check_same_thread=False because sessions cross threadpool threads;
    # timeout is the busy-wait before SQLite gives up on a locked database.
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        connect_args={"check_same_thread": False, "timeout": 15.0},
    )

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        # Enforce the ForeignKey declarations in models.py; SQLite ignores them
        # unless this is switched on per connection.
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()
else:
    # Postgres. pool_pre_ping avoids handing out a connection the server has
    # already dropped (a laptop that slept, a restarted database).
    engine = create_engine(
        SQLALCHEMY_DATABASE_URL,
        pool_size=DB_POOL_SIZE,
        max_overflow=DB_MAX_OVERFLOW,
        pool_timeout=DB_POOL_TIMEOUT,
        pool_recycle=DB_POOL_RECYCLE,
        pool_pre_ping=True,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# How many times to retry a write that lost a lock race, and the base backoff.
_RETRY_ATTEMPTS = 4
_RETRY_BASE_DELAY = 0.05


def _is_retryable(error: OperationalError) -> bool:
    """True for transient "someone else is writing" errors, not real faults.

    A locked SQLite database and a Postgres deadlock/serialization failure are
    both worth retrying; a syntax error or a constraint violation is not.
    """
    message = str(error).lower()
    return (
        "database is locked" in message
        or "deadlock detected" in message
        or "could not serialize access" in message
    )


def commit_with_retry(db, attempts: int = _RETRY_ATTEMPTS) -> None:
    """Commit, retrying briefly when the failure is write contention.

    Under concurrent annotators a commit can lose a lock race through no fault
    of the request. Without this the caller sees a 500 for something that would
    have succeeded milliseconds later. Exponential backoff, then give up and let
    the original error propagate so a genuine fault is still visible.
    """
    for attempt in range(attempts):
        try:
            db.commit()
            return
        except OperationalError as error:
            if attempt == attempts - 1 or not _is_retryable(error):
                raise
            db.rollback()
            delay = _RETRY_BASE_DELAY * (2 ** attempt)
            logger.warning(
                "Commit lost a lock race (attempt %d/%d), retrying in %.2fs: %s",
                attempt + 1, attempts, delay, error,
            )
            time.sleep(delay)

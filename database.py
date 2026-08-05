"""Database engine and session facade (re-exports from `app.database`).

Maintains 100% backward compatibility for legacy imports and scripts.
"""
from app.database import (
    Base,
    SessionLocal,
    SQLALCHEMY_DATABASE_URL,
    _is_retryable,
    commit_with_retry,
    engine,
    get_db,
)

__all__ = [
    "Base",
    "SessionLocal",
    "SQLALCHEMY_DATABASE_URL",
    "_is_retryable",
    "commit_with_retry",
    "engine",
    "get_db",
]

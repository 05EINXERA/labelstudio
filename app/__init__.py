"""Annotation Workspace Application Package.

Provides core configuration, database persistence, domain models, Pydantic schemas,
API routers, format converters, and ML subsystems.
"""

__version__ = "1.0.0"

from app.config import (
    APP_ENV,
    APP_HOST,
    APP_PORT,
    DATA_DIR,
    DATABASE_URL,
    IS_PRODUCTION,
    IS_SQLITE,
    JWT_SECRET,
    validate_config,
)
from app.database import Base, SessionLocal, commit_with_retry, engine, get_db

__all__ = [
    "__version__",
    "APP_ENV",
    "APP_HOST",
    "APP_PORT",
    "DATA_DIR",
    "DATABASE_URL",
    "IS_PRODUCTION",
    "IS_SQLITE",
    "JWT_SECRET",
    "validate_config",
    "Base",
    "SessionLocal",
    "commit_with_retry",
    "engine",
    "get_db",
]

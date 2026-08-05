"""Runtime configuration facade (re-exports from `app.config`).

Maintains 100% backward compatibility for legacy imports and scripts.
"""
import importlib
import app.config

# Ensure app.config is fresh when config is imported or reloaded
importlib.reload(app.config)

from app.config import (
    APP_ENV,
    APP_HOST,
    APP_PORT,
    ALLOW_REGISTRATION,
    COOKIE_SAMESITE,
    COOKIE_SECURE,
    CORS_ORIGINS,
    ConfigError,
    DATA_DIR,
    DATABASE_URL,
    DB_MAX_OVERFLOW,
    DB_POOL_RECYCLE,
    DB_POOL_SIZE,
    DB_POOL_TIMEOUT,
    IS_PRODUCTION,
    IS_SQLITE,
    JWT_SECRET,
    LOG_BACKUP_COUNT,
    LOG_DIR,
    LOG_LEVEL,
    LOG_MAX_BYTES,
    MAX_IMPORT_BYTES,
    MAX_INFERENCE_CONCURRENCY,
    MAX_UPLOAD_FILES,
    MIN_PASSWORD_LENGTH,
    THREADPOOL_CAP,
    validate_config,
)

__all__ = [
    "APP_ENV",
    "APP_HOST",
    "APP_PORT",
    "ALLOW_REGISTRATION",
    "COOKIE_SAMESITE",
    "COOKIE_SECURE",
    "CORS_ORIGINS",
    "ConfigError",
    "DATA_DIR",
    "DATABASE_URL",
    "DB_MAX_OVERFLOW",
    "DB_POOL_RECYCLE",
    "DB_POOL_SIZE",
    "DB_POOL_TIMEOUT",
    "IS_PRODUCTION",
    "IS_SQLITE",
    "JWT_SECRET",
    "LOG_BACKUP_COUNT",
    "LOG_DIR",
    "LOG_LEVEL",
    "LOG_MAX_BYTES",
    "MAX_IMPORT_BYTES",
    "MAX_INFERENCE_CONCURRENCY",
    "MAX_UPLOAD_FILES",
    "MIN_PASSWORD_LENGTH",
    "THREADPOOL_CAP",
    "validate_config",
]

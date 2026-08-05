"""Logging configuration facade (re-exports from `app.logging_config`).

Maintains 100% backward compatibility for legacy imports and scripts.
"""
from app.logging_config import configure_logging

__all__ = ["configure_logging"]

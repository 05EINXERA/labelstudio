"""Test suite for app package structure and root facade backward compatibility (P3)."""
import pytest
from fastapi import FastAPI


def test_app_package_imports():
    """Verify that all app package modules import cleanly."""
    import app
    import app.config
    import app.database
    import app.models
    import app.schemas
    import app.logging_config
    import app.main

    assert hasattr(app, "__version__")
    assert isinstance(app.main.app, FastAPI)
    assert app.database.Base is not None
    assert app.models.Task is not None
    assert app.schemas.TaskDetail is not None


def test_root_facades_symbol_equality():
    """Verify that root facade modules re-export identical objects as app package modules."""
    import config as root_config
    import app.config as pkg_config
    assert root_config.DATABASE_URL == pkg_config.DATABASE_URL
    assert root_config.APP_PORT == pkg_config.APP_PORT
    assert root_config.IS_PRODUCTION == pkg_config.IS_PRODUCTION
    assert root_config.validate_config is pkg_config.validate_config

    import database as root_db
    import app.database as pkg_db
    assert root_db.Base is pkg_db.Base
    assert root_db.engine is pkg_db.engine
    assert root_db.SessionLocal is pkg_db.SessionLocal
    assert root_db.commit_with_retry is pkg_db.commit_with_retry

    import models as root_models
    import app.models as pkg_models
    assert root_models.Task is pkg_models.Task
    assert root_models.Project is pkg_models.Project
    assert root_models.User is pkg_models.User
    assert root_models.AIJob is pkg_models.AIJob
    assert root_models.TaskLock is pkg_models.TaskLock

    import schemas as root_schemas
    import app.schemas as pkg_schemas
    assert root_schemas.TaskDetail is pkg_schemas.TaskDetail
    assert root_schemas.ProjectModel is pkg_schemas.ProjectModel
    assert root_schemas.Token is pkg_schemas.Token

    import logging_config as root_log
    import app.logging_config as pkg_log
    assert root_log.configure_logging is pkg_log.configure_logging

    import main as root_main
    import app.main as pkg_main
    assert root_main.app is pkg_main.app
    assert root_main.lifespan is pkg_main.lifespan


def test_fastapi_app_routes_coverage():
    """Verify that the FastAPI app has all registered endpoints."""
    import main
    from starlette.routing import Route, Mount
    from fastapi.routing import APIRoute

    paths = []
    for r in main.app.routes:
        if isinstance(r, (Route, APIRoute)):
            paths.append(r.path)
        elif isinstance(r, Mount):
            paths.append(r.path)

    assert "/health" in paths
    assert "/uploads" in paths
    assert "/" in paths
    assert len(paths) >= 3

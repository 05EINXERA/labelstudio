"""Shared test fixtures.

`database.py` builds its engine from `config.DATA_DIR` at import time, so
DATA_DIR is redirected to a temp directory *before* the app is imported. That
keeps the real workspace.db untouched.
"""
import itertools
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP_DATA_DIR = tempfile.mkdtemp(prefix="labelstudio-tests-")
os.environ["DATA_DIR"] = _TMP_DATA_DIR
os.environ.setdefault("JWT_SECRET", "test-secret-not-used-in-production")

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
from database import Base, engine  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client():
    """Anonymous client. Its cookie jar is cleared after each auth call so a
    registration never leaves the client silently logged in — `get_token`
    prefers the cookie over the Authorization header, which would otherwise
    make every request authenticate as the last user registered.
    """
    with TestClient(main.app) as c:
        yield c


_user_seq = itertools.count()


def _register(client, prefix, password="pw-12345"):
    """Register a fresh user and return its bearer header.

    Usernames are unique per call because the schema is created once per
    session and `users.username` is UNIQUE; a fixed name would collide on the
    second test.
    """
    username = f"{prefix}-{next(_user_seq)}"
    res = client.post("/api/auth/register", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    client.cookies.clear()
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


@pytest.fixture
def alice(client):
    return _register(client, "alice")


@pytest.fixture
def bob(client):
    return _register(client, "bob")

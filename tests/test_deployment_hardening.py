"""Tests for the multi-user deployment hardening.

Covers the security and correctness changes from
.devnotes/deployment-hardening/01_HARDENING_PLAN.md: CSRF, per-user workspace
data scoping, registration gating, password policy, upload caps, the health
endpoint, and the production config validation.
"""
import importlib
import os

import pytest
from fastapi.testclient import TestClient

import main
from conftest import _register


# --- CSRF (A-4) -----------------------------------------------------------

def test_cookie_login_issues_a_csrf_cookie(client):
    """Logging in must hand back both the session and the CSRF cookie."""
    username = "csrf-user-a"
    client.post("/api/auth/register", json={"username": username, "password": "pw-12345"})
    client.cookies.clear()

    res = client.post(
        "/api/auth/token",
        data={"username": username, "password": "pw-12345"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert res.status_code == 200, res.text
    assert "csrf_token" in res.cookies
    # Also returned in the body so a non-browser client can echo it.
    assert res.json()["csrf_token"]


def test_cookie_write_without_csrf_header_is_rejected(client):
    """A cookie-authenticated write with no CSRF header must 403.

    This is the actual attack shape: a browser attaches the session cookie
    automatically, so without the echoed header the request must not proceed.
    """
    username = "csrf-user-b"
    client.post("/api/auth/register", json={"username": username, "password": "pw-12345"})
    client.cookies.clear()
    client.post(
        "/api/auth/token",
        data={"username": username, "password": "pw-12345"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    # Cookies are now set on the client; deliberately send no X-CSRF-Token.
    res = client.post("/api/data", json={"key": "k", "value": "v"})
    assert res.status_code == 403
    assert "csrf" in res.json()["detail"].lower()


def test_cookie_write_with_csrf_header_succeeds(client):
    username = "csrf-user-c"
    client.post("/api/auth/register", json={"username": username, "password": "pw-12345"})
    client.cookies.clear()
    login = client.post(
        "/api/auth/token",
        data={"username": username, "password": "pw-12345"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    token = login.json()["csrf_token"]

    res = client.post(
        "/api/data",
        json={"key": "k", "value": "v"},
        headers={"X-CSRF-Token": token},
    )
    assert res.status_code == 200, res.text


def test_mismatched_csrf_token_is_rejected(client):
    username = "csrf-user-d"
    client.post("/api/auth/register", json={"username": username, "password": "pw-12345"})
    client.cookies.clear()
    client.post(
        "/api/auth/token",
        data={"username": username, "password": "pw-12345"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    res = client.post(
        "/api/data",
        json={"key": "k", "value": "v"},
        headers={"X-CSRF-Token": "not-the-real-token"},
    )
    assert res.status_code == 403


def test_bearer_clients_are_exempt_from_csrf(client):
    """Header-authenticated clients are not forgeable by a browser, so the
    CSRF requirement must not break scripts and tests."""
    headers = _register(client, "csrf-bearer")
    res = client.post("/api/data", json={"key": "k", "value": "v"}, headers=headers)
    assert res.status_code == 200, res.text


def test_reads_do_not_require_csrf(client):
    headers = _register(client, "csrf-read")
    assert client.get("/api/data", headers=headers).status_code == 200


# --- Per-user workspace data (A-5a) ---------------------------------------

def test_workspace_data_is_scoped_per_user(client):
    """The core multi-user bug: one annotator's write must not be visible to,
    or overwrite, another's under the same key."""
    alice = _register(client, "ws-alice")
    bob = _register(client, "ws-bob")

    client.post("/api/data", json={"key": "layout", "value": "alice-value"}, headers=alice)
    client.post("/api/data", json={"key": "layout", "value": "bob-value"}, headers=bob)

    assert client.get("/api/data", headers=alice).json()["layout"] == "alice-value"
    assert client.get("/api/data", headers=bob).json()["layout"] == "bob-value"


def test_workspace_data_does_not_leak_other_users_keys(client):
    alice = _register(client, "ws-leak-a")
    bob = _register(client, "ws-leak-b")

    client.post("/api/data", json={"key": "alice-only", "value": "secret"}, headers=alice)

    assert "alice-only" not in client.get("/api/data", headers=bob).json()


def test_workspace_data_update_is_per_user(client):
    """Updating an existing key must update only the caller's row."""
    alice = _register(client, "ws-upd-a")
    bob = _register(client, "ws-upd-b")

    client.post("/api/data", json={"key": "k", "value": "a1"}, headers=alice)
    client.post("/api/data", json={"key": "k", "value": "b1"}, headers=bob)
    client.post("/api/data", json={"key": "k", "value": "a2"}, headers=alice)

    assert client.get("/api/data", headers=alice).json()["k"] == "a2"
    assert client.get("/api/data", headers=bob).json()["k"] == "b1"


# --- Password policy and registration gating (A-5b) -----------------------

def test_short_password_is_rejected(client):
    res = client.post("/api/auth/register", json={"username": "shorty", "password": "abc"})
    assert res.status_code == 422
    assert "at least" in res.json()["detail"]


def test_registration_can_be_disabled(monkeypatch, client):
    """With ALLOW_REGISTRATION off, the LAN instance stops minting accounts."""
    import api.routers.auth as auth_router
    monkeypatch.setattr(auth_router, "ALLOW_REGISTRATION", False)

    res = client.post("/api/auth/register", json={"username": "nope", "password": "pw-12345"})
    assert res.status_code == 403
    assert "disabled" in res.json()["detail"].lower()


# --- Upload caps (A-7, B-3) -----------------------------------------------

def test_upload_rejects_too_many_files(client, monkeypatch):
    """One request must not be able to queue unbounded per-file work."""
    import api.routers.projects as projects_router
    monkeypatch.setattr(projects_router, "MAX_UPLOAD_FILES", 2)

    headers = _register(client, "upload-cap")
    project = client.post(
        "/api/projects",
        json={"name": "cap", "slug": "cap", "creator": "x"},
        headers=headers,
    ).json()

    files = [("file", (f"i{n}.png", b"\x89PNG\r\n\x1a\n", "image/png")) for n in range(3)]
    res = client.post(f"/api/projects/{project['id']}/upload", files=files, headers=headers)

    assert res.status_code == 413
    assert "limit is 2" in res.json()["detail"]


@pytest.mark.anyio
async def test_read_capped_rejects_oversized_upload():
    """read_capped must refuse while reading, not after absorbing the whole body."""
    from fastapi import HTTPException
    from api.uploads import read_capped

    class _FakeUpload:
        """Yields more bytes than the cap allows, one chunk at a time."""
        def __init__(self):
            self.chunks_served = 0

        async def read(self, size=-1):
            self.chunks_served += 1
            # Never terminates on its own — the cap must be what stops it.
            return b"x" * size if size > 0 else b""

    upload = _FakeUpload()
    with pytest.raises(HTTPException) as excinfo:
        await read_capped(upload, max_bytes=2 * 1024 * 1024)

    assert excinfo.value.status_code == 413
    # Stopped early rather than reading an unbounded amount.
    assert upload.chunks_served <= 4


# --- Health endpoint (C-3) ------------------------------------------------

def test_health_reports_database_reachable(client):
    res = client.get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["database"] == "up"


def test_health_needs_no_authentication(client):
    """A supervisor probing health must not need credentials."""
    client.cookies.clear()
    assert client.get("/health").status_code == 200


# --- Production config validation (A-2, A-3) ------------------------------

def _validate_with(monkeypatch, **env):
    """Re-import config under the given environment and validate it."""
    import config
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    reloaded = importlib.reload(config)
    try:
        reloaded.validate_config()
        return None
    except reloaded.ConfigError as exc:
        return str(exc)
    finally:
        # Restore the module other tests hold references to.
        for key in env:
            monkeypatch.delenv(key, raising=False)
        importlib.reload(config)


def test_production_requires_jwt_secret(monkeypatch):
    monkeypatch.delenv("JWT_SECRET", raising=False)
    error = _validate_with(
        monkeypatch,
        APP_ENV="production",
        CORS_ORIGINS="http://192.168.1.81:8000",
    )
    assert error and "JWT_SECRET" in error


def test_production_rejects_wildcard_cors(monkeypatch):
    error = _validate_with(
        monkeypatch,
        APP_ENV="production",
        JWT_SECRET="x" * 64,
        CORS_ORIGINS="*",
    )
    assert error and "wildcard" in error.lower()


def test_production_requires_cors_origins(monkeypatch):
    error = _validate_with(
        monkeypatch,
        APP_ENV="production",
        JWT_SECRET="x" * 64,
        CORS_ORIGINS="",
    )
    assert error and "CORS_ORIGINS" in error


def test_valid_production_config_passes(monkeypatch):
    error = _validate_with(
        monkeypatch,
        APP_ENV="production",
        JWT_SECRET="x" * 64,
        CORS_ORIGINS="http://192.168.1.81:8000",
    )
    assert error is None


def test_development_config_is_not_validated(monkeypatch):
    """Development must stay frictionless — no env vars required."""
    monkeypatch.delenv("JWT_SECRET", raising=False)
    error = _validate_with(monkeypatch, APP_ENV="development", CORS_ORIGINS="")
    assert error is None


# --- Commit retry (B-4) ---------------------------------------------------

def test_commit_with_retry_retries_transient_lock_errors():
    """A lost lock race should be retried, not surfaced as a 500."""
    from sqlalchemy.exc import OperationalError
    from database import commit_with_retry

    class _FlakyDB:
        def __init__(self):
            self.commits = 0
            self.rollbacks = 0

        def commit(self):
            self.commits += 1
            if self.commits < 3:
                raise OperationalError("stmt", {}, Exception("database is locked"))

        def rollback(self):
            self.rollbacks += 1

    db = _FlakyDB()
    commit_with_retry(db)
    assert db.commits == 3
    assert db.rollbacks == 2


def test_commit_with_retry_does_not_retry_real_faults():
    """A genuine error must surface immediately rather than being retried."""
    from sqlalchemy.exc import OperationalError
    from database import commit_with_retry

    class _BrokenDB:
        def __init__(self):
            self.commits = 0

        def commit(self):
            self.commits += 1
            raise OperationalError("stmt", {}, Exception("no such column: bogus"))

        def rollback(self):
            pass

    db = _BrokenDB()
    with pytest.raises(OperationalError):
        commit_with_retry(db)
    assert db.commits == 1

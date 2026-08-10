"""Self-service password change: POST /api/auth/password.

The endpoint sits on `/api/auth`, the one router without router-level
`get_current_user`/`require_csrf` dependencies (it has to serve login), so both
are declared per-function — these tests pin that they are actually there, since
a missing one here would not be caught by the router-level audits.

The behavioural contract:
  * the current password must be re-verified, so a session cookie left on a
    shared annotator PC cannot be used to take the account over;
  * a wrong current password answers 400, not 401 — a 401 trips `apiFetch`'s
    "session expired" path and would bounce the user to the login screen over a
    typo (frontend/js/api.js);
  * the change is atomic from the caller's point of view: the old password stops
    working, the new one starts, and the calling tab keeps a usable session.
"""
import pytest

from api.auth import CSRF_COOKIE_NAME, CSRF_HEADER_NAME
from config import MIN_PASSWORD_LENGTH

OLD = "old-password-1"
NEW = "new-password-2"


def _register(client, username, password=OLD):
    res = client.post("/api/auth/register", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    assert client.cookies.get("access_token")
    assert client.cookies.get(CSRF_COOKIE_NAME)
    return res


def _csrf(client):
    return {CSRF_HEADER_NAME: client.cookies.get(CSRF_COOKIE_NAME)}


def _change(client, current, new, **kwargs):
    return client.post(
        "/api/auth/password",
        json={"current_password": current, "new_password": new},
        headers=_csrf(client),
        **kwargs,
    )


def _login(client, username, password):
    return client.post(
        "/api/auth/token", data={"username": username, "password": password}
    )


def test_password_change_succeeds_and_swaps_the_credential(client):
    """The happy path, verified against the login endpoint rather than the hash."""
    _register(client, "pw-happy")

    res = _change(client, OLD, NEW)
    assert res.status_code == 200, res.text

    # The only proof that matters: the new password authenticates and the old
    # one no longer does.
    assert _login(client, "pw-happy", NEW).status_code == 200
    assert _login(client, "pw-happy", OLD).status_code == 401


def test_wrong_current_password_is_rejected_with_400(client):
    """400, not 401 — see the module docstring. And the password is unchanged."""
    _register(client, "pw-wrong")

    res = _change(client, "not-my-password", NEW)
    assert res.status_code == 400, res.text
    assert "current password" in res.json()["detail"].lower()

    assert _login(client, "pw-wrong", OLD).status_code == 200
    assert _login(client, "pw-wrong", NEW).status_code == 401


def test_short_new_password_is_rejected(client):
    """The same minimum the register endpoint enforces, applied to changes too."""
    _register(client, "pw-short")

    res = _change(client, OLD, "a" * (MIN_PASSWORD_LENGTH - 1))
    assert res.status_code == 422, res.text
    assert _login(client, "pw-short", OLD).status_code == 200


def test_reusing_the_current_password_is_rejected(client):
    """A no-op change is a mistake, not a request; it must not report success."""
    _register(client, "pw-same")

    res = _change(client, OLD, OLD)
    assert res.status_code == 422, res.text
    assert _login(client, "pw-same", OLD).status_code == 200


def test_password_change_requires_authentication(client):
    """No session at all -> 401. `/api/auth` has no router-level dependency."""
    client.cookies.clear()
    res = client.post(
        "/api/auth/password",
        json={"current_password": OLD, "new_password": NEW},
    )
    assert res.status_code == 401


def test_password_change_requires_csrf(client):
    """A cookie-authenticated write with no CSRF echo is refused (rule 1a).

    Without this, any page the annotator visits could POST a password change
    with the ambient session cookie and lock them out.
    """
    _register(client, "pw-csrf")

    res = client.post(
        "/api/auth/password",
        json={"current_password": OLD, "new_password": NEW},
    )
    assert res.status_code == 403
    assert "csrf" in res.json()["detail"].lower()
    # And nothing was written.
    assert _login(client, "pw-csrf", OLD).status_code == 200


def test_calling_tab_keeps_a_usable_session(client):
    """The response reissues the cookie pair, so the tab is not silently dead.

    A user who changes their password and then hits save must not discover that
    the write 401s or 403s — that is the shape of the incident this app already
    has a postmortem for.
    """
    _register(client, "pw-session")

    res = _change(client, OLD, NEW)
    assert res.status_code == 200, res.text
    assert res.json()["csrf_token"] == client.cookies.get(CSRF_COOKIE_NAME)

    # An authenticated GET and a CSRF-protected write both still work.
    assert client.get("/api/auth/me").status_code == 200
    created = client.post(
        "/api/projects",
        json={"name": "after-pw", "slug": "after-pw", "creator": "pw-session"},
        headers=_csrf(client),
    )
    assert created.status_code == 200, created.text


def test_a_user_cannot_change_another_users_password(client):
    """The endpoint is self-service only: there is no target-user parameter.

    Pinning this because the obvious "improvement" is to add one, and it would
    need an admin role the app does not have.
    """
    _register(client, "pw-victim", password=OLD)
    client.cookies.clear()
    _register(client, "pw-attacker", password="attacker-pw-1")

    # The attacker's own current password is the only thing accepted, and the
    # change lands on the attacker's account.
    res = _change(client, "attacker-pw-1", NEW)
    assert res.status_code == 200, res.text

    assert _login(client, "pw-victim", OLD).status_code == 200
    assert _login(client, "pw-victim", NEW).status_code == 401

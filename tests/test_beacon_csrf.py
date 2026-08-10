"""sendBeacon writes vs. the CSRF double-submit check.

Reported from the LAN deployment: an owner opening a task produced

    POST /api/tasks/1241/release-beacon ... 403 Forbidden
    POST /api/tasks                     ... 403 Forbidden

on a project they own. The cause is structural, not a permission bug.

`require_csrf` (api/auth.py) demands an `X-CSRF-Token` header echoing the
`csrf_token` cookie. `navigator.sendBeacon` cannot set request headers at all —
that is a fixed property of the API, not something the frontend can work
around. So every beacon from a cookie-authenticated browser is rejected 403,
100% of the time, on all three beacon endpoints.

Why that matters beyond a stray log line: the beacon paths fire on `pagehide`
and `visibilitychange`, which is exactly when a normal fetch is not guaranteed
to be delivered — the unload flush is the last chance to persist work. The
client reads `sendBeacon()`'s return value as success, but that only reports
that the request was *queued*; the 403 arrives afterwards and is unreadable by
design. So the flush silently fails while the client believes it succeeded.

Fixed by allowing the token to arrive as a `csrf_token` query parameter, which
sendBeacon *can* carry. The double-submit property is untouched: the value must
still equal the non-httpOnly `csrf_token` cookie, which a cross-origin page
cannot read. The tests below pin both halves — no/!wrong token still 403s, the
correct token in the query is accepted, and the header still wins.
"""
import json

import pytest

from api.auth import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, CSRF_QUERY_PARAM


def _login_with_cookies(client, username="beacon-user", password="pw-12345"):
    """Register + log in so the client holds session AND csrf cookies.

    A browser is cookie-authenticated; the bearer header used elsewhere in the
    suite is explicitly CSRF-exempt, so it cannot reproduce this at all.
    """
    res = client.post("/api/auth/register", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    # register/token both set the session and CSRF cookies; the cookie jar is
    # deliberately NOT cleared here (unlike the shared `alice` fixture), because
    # cookie auth is the whole point of this file.
    assert client.cookies.get("access_token"), "expected a session cookie"
    assert client.cookies.get(CSRF_COOKIE_NAME), "expected a CSRF cookie"
    return res


def _csrf(client):
    return {CSRF_HEADER_NAME: client.cookies.get(CSRF_COOKIE_NAME)}


def _project(client):
    res = client.post("/api/projects", json={
        "name": "beacon-test", "slug": "beacon-test", "creator": "beacon-user",
    }, headers=_csrf(client))
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _task(client, project_id):
    res = client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": "img.jpg", "status": "New"},
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_cookie_client_with_csrf_header_is_accepted(client):
    """Baseline: the normal apiFetch path works, so a later 403 is not permissions."""
    _login_with_cookies(client, "beacon-ok")
    project_id = _project(client)
    task = _task(client, project_id)

    # `updated_at` is carried so this is a clean write rather than a 409 — the
    # point here is only that CSRF is satisfied and permissions are fine.
    res = client.post("/api/tasks", json={
        "id": task["id"], "annotations": json.dumps([{"id": "a1"}]),
        "updated_at": task["updated_at"], "client_id": "tab-A",
    }, headers=_csrf(client))
    assert res.status_code == 200, res.text


def test_task_save_is_rejected_with_no_csrf_token(client):
    """The annotation-save beacon: POST /api/tasks with no CSRF header.

    This is the unload flush — the one write that carries annotations at the
    moment the page is going away. With no token in either place it is still
    correctly refused; the fix is that the beacon can now supply one.
    """
    _login_with_cookies(client, "beacon-save")
    project_id = _project(client)
    task = _task(client, project_id)

    # Exactly what navigator.sendBeacon sends: a body, a content type, and no
    # opportunity to attach a header.
    res = client.post(
        "/api/tasks",
        content=json.dumps({
            "id": task["id"],
            "annotations": json.dumps([{"id": "a1"}]),
            "client_id": "tab-A",
        }),
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 403
    assert "csrf" in res.json()["detail"].lower()


def test_release_beacon_is_rejected_with_no_csrf_token(client):
    """The lock-release endpoint from the reported log, with no token at all."""
    _login_with_cookies(client, "beacon-release")
    project_id = _project(client)
    task = _task(client, project_id)

    res = client.post(
        f"/api/tasks/{task['id']}/release-beacon?client_id=abc",
        content="",
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 403
    assert "csrf" in res.json()["detail"].lower()


def test_time_log_is_rejected_with_no_csrf_token(client):
    """The third beacon target — the session-time flush — with no token."""
    _login_with_cookies(client, "beacon-time")

    res = client.post(
        "/api/team/time",
        content=json.dumps({"name": "beacon-time", "time_logged": 30}),
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 403
    assert "csrf" in res.json()["detail"].lower()


# --- The query-parameter fallback ------------------------------------------
#
# sendBeacon cannot set headers but can carry a query string, and the CSRF
# cookie is deliberately non-httpOnly so the page can read it. Echoing it in
# the URL therefore proves exactly what the header proved: the caller could
# read a cookie that a cross-origin page cannot.


def test_task_save_beacon_is_accepted_with_csrf_query_param(client):
    """The annotation-save beacon now survives the unload flush."""
    _login_with_cookies(client, "beacon-q-save")
    project_id = _project(client)
    task = _task(client, project_id)

    token = client.cookies.get(CSRF_COOKIE_NAME)
    res = client.post(
        f"/api/tasks?{CSRF_QUERY_PARAM}={token}",
        content=json.dumps({
            "id": task["id"],
            "annotations": json.dumps([{"id": "a1"}]),
            "updated_at": task["updated_at"],
            "client_id": "tab-A",
        }),
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 200, res.text


def test_release_beacon_is_accepted_with_csrf_query_param(client):
    """The lock-release beacon from the reported log now succeeds."""
    _login_with_cookies(client, "beacon-q-release")
    project_id = _project(client)
    task = _task(client, project_id)

    token = client.cookies.get(CSRF_COOKIE_NAME)
    res = client.post(
        f"/api/tasks/{task['id']}/release-beacon"
        f"?client_id=abc&{CSRF_QUERY_PARAM}={token}",
        content="",
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 200, res.text


def test_wrong_csrf_query_param_is_still_rejected(client):
    """The fallback checks the value; it is not a way to opt out of CSRF.

    This is the test that makes the fallback safe to have: an attacker who can
    trigger a request but cannot read the cookie still cannot guess the token,
    so a mismatched parameter must fail exactly as a mismatched header does.
    """
    _login_with_cookies(client, "beacon-q-bad")
    project_id = _project(client)
    task = _task(client, project_id)

    res = client.post(
        f"/api/tasks?{CSRF_QUERY_PARAM}=not-the-real-token",
        content=json.dumps({"id": task["id"], "client_id": "tab-A"}),
        headers={"Content-Type": "application/json"},
    )
    assert res.status_code == 403
    assert "csrf" in res.json()["detail"].lower()


def test_header_still_takes_precedence_over_query(client):
    """A valid header is honoured regardless of the query string.

    apiFetch keeps sending the header for every ordinary request; the query
    fallback must not disturb that path.
    """
    _login_with_cookies(client, "beacon-q-both")
    project_id = _project(client)
    task = _task(client, project_id)

    res = client.post(
        f"/api/tasks?{CSRF_QUERY_PARAM}=garbage",
        json={
            "id": task["id"], "annotations": json.dumps([{"id": "a1"}]),
            "updated_at": task["updated_at"], "client_id": "tab-A",
        },
        headers=_csrf(client),
    )
    assert res.status_code == 200, res.text

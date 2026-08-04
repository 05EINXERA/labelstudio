"""Soft task lock — T2.1.

Tests for POST /api/tasks/{id}/claim, POST /{id}/heartbeat,
DELETE /{id}/claim, POST /{id}/release-beacon, and GET /{id}/lock-status.

The lock is advisory: it warns a second annotator without blocking them.
It lives in an in-process dict so the single-worker constraint (CLAUDE.md
rule 9) applies — these tests cover the claim / stale-takeover / release
contract without caring about cross-process behaviour.
"""
import time

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project(client, auth):
    res = client.post("/api/projects", json={
        "name": "lock-test", "slug": "lock-test", "creator": "tester",
    }, headers=auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _task(client, auth, project_id, name="img.jpg"):
    res = client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": name, "status": "New"},
        headers=auth,
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _claim(client, auth, task_id, cid):
    return client.post(
        f"/api/tasks/{task_id}/claim?client_id={cid}", headers=auth
    )


def _heartbeat(client, auth, task_id, cid):
    return client.post(
        f"/api/tasks/{task_id}/heartbeat?client_id={cid}", headers=auth
    )


def _release(client, auth, task_id, cid):
    return client.delete(
        f"/api/tasks/{task_id}/claim?client_id={cid}", headers=auth
    )


def _release_beacon(client, auth, task_id, cid):
    return client.post(
        f"/api/tasks/{task_id}/release-beacon?client_id={cid}", headers=auth
    )


def _lock_status(client, auth, task_id):
    return client.get(f"/api/tasks/{task_id}/lock-status", headers=auth)


# ---------------------------------------------------------------------------
# Claim tests
# ---------------------------------------------------------------------------

def test_claim_on_free_task_succeeds(client, alice):
    pid = _project(client, alice)
    tid = _task(client, alice, pid)

    res = _claim(client, alice, tid, "tab-A")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_same_client_refreshing_its_own_claim_succeeds(client, alice):
    """A tab refreshing its own claim (heartbeat via claim) must get ok."""
    pid = _project(client, alice)
    tid = _task(client, alice, pid)

    assert _claim(client, alice, tid, "tab-A").json()["status"] == "ok"
    # Claim again with the same id — should just refresh the TTL.
    assert _claim(client, alice, tid, "tab-A").json()["status"] == "ok"


def test_different_client_on_held_task_gets_locked(client, alice):
    """A second tab must see 'locked', not 'ok', while the first holds the claim."""
    pid = _project(client, alice)
    tid = _task(client, alice, pid)

    _claim(client, alice, tid, "tab-A")
    res = _claim(client, alice, tid, "tab-B")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "locked"
    assert body["locked_by"] == "tab-A"
    assert body["seconds_remaining"] > 0


def test_claim_nonexistent_task_returns_404(client, alice):
    res = _claim(client, alice, 999999, "tab-X")
    assert res.status_code == 404


def test_claim_another_users_task_returns_404(client, alice, bob):
    """Ownership is enforced: bob cannot claim alice's task."""
    pid = _project(client, alice)
    tid = _task(client, alice, pid)

    res = _claim(client, bob, tid, "tab-bob")
    assert res.status_code == 404


# ---------------------------------------------------------------------------
# Release tests
# ---------------------------------------------------------------------------

def test_release_frees_the_lock(client, alice):
    pid = _project(client, alice)
    tid = _task(client, alice, pid)

    _claim(client, alice, tid, "tab-A")
    _release(client, alice, tid, "tab-A")

    # After release tab-B should be able to claim cleanly.
    res = _claim(client, alice, tid, "tab-B")
    assert res.json()["status"] == "ok"


def test_release_by_non_holder_is_a_noop(client, alice):
    """Releasing a lock you don't hold must not error or evict the holder."""
    pid = _project(client, alice)
    tid = _task(client, alice, pid)

    _claim(client, alice, tid, "tab-A")
    # tab-B tries to release — should silently succeed but not clear tab-A's lock.
    _release(client, alice, tid, "tab-B")

    status = _lock_status(client, alice, tid).json()
    assert status["locked"] is True
    assert status["locked_by"] == "tab-A"


def test_release_beacon_endpoint_works(client, alice):
    pid = _project(client, alice)
    tid = _task(client, alice, pid)

    _claim(client, alice, tid, "tab-A")
    res = _release_beacon(client, alice, tid, "tab-A")
    assert res.status_code == 200

    # Lock is gone after beacon release.
    assert _lock_status(client, alice, tid).json()["locked"] is False


# ---------------------------------------------------------------------------
# Heartbeat tests
# ---------------------------------------------------------------------------

def test_heartbeat_refreshes_claim(client, alice):
    pid = _project(client, alice)
    tid = _task(client, alice, pid)

    _claim(client, alice, tid, "tab-A")
    res = _heartbeat(client, alice, tid, "tab-A")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_heartbeat_on_unheld_task_re_claims(client, alice):
    """If the TTL expired, a heartbeat silently re-claims rather than failing."""
    pid = _project(client, alice)
    tid = _task(client, alice, pid)
    # Never claimed — heartbeat should behave like a fresh claim.
    res = _heartbeat(client, alice, tid, "tab-A")
    assert res.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Lock-status tests
# ---------------------------------------------------------------------------

def test_lock_status_free_task(client, alice):
    pid = _project(client, alice)
    tid = _task(client, alice, pid)

    body = _lock_status(client, alice, tid).json()
    assert body["locked"] is False


def test_lock_status_held_task(client, alice):
    pid = _project(client, alice)
    tid = _task(client, alice, pid)

    _claim(client, alice, tid, "tab-A")
    body = _lock_status(client, alice, tid).json()
    assert body["locked"] is True
    assert body["locked_by"] == "tab-A"
    assert body["seconds_remaining"] > 0


def test_lock_status_after_release(client, alice):
    pid = _project(client, alice)
    tid = _task(client, alice, pid)

    _claim(client, alice, tid, "tab-A")
    _release(client, alice, tid, "tab-A")

    assert _lock_status(client, alice, tid).json()["locked"] is False


# ---------------------------------------------------------------------------
# Stale-takeover test (monkeypatched TTL so the test doesn't sleep)
# ---------------------------------------------------------------------------

def test_stale_claim_is_reclaimable(client, alice):
    """A claim older than the TTL must be treated as gone."""
    from database import SessionLocal
    import models

    pid = _project(client, alice)
    tid = _task(client, alice, pid)

    _claim(client, alice, tid, "tab-A")

    # Wind the claimed_at back past the TTL in the database.
    db = SessionLocal()
    try:
        lock = db.query(models.TaskLock).filter(models.TaskLock.task_id == tid).first()
        assert lock is not None
        lock.claimed_at = lock.claimed_at.replace(
            year=lock.claimed_at.year - 1  # definitely stale
        )
        db.commit()
    finally:
        db.close()

    # tab-B should now be able to claim.
    res = _claim(client, alice, tid, "tab-B")
    assert res.json()["status"] == "ok", (
        "A stale claim must be reclaimable by a different client"
    )


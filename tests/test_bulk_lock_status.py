"""Batched lock status — GET /api/tasks/lock-status?projectId=N (T4).

The Tasks view showed a "busy" badge by calling GET /{task_id}/lock-status once
per task: 120 authenticated requests on a 120-task project to read an in-process
dict (.devnotes/server-optimization/03_TASKS_PAGE.md). This endpoint answers for
the whole project in one call.

The contract that matters, and that these tests pin:

- it agrees with the per-task endpoint, which stays the reference definition;
- it returns *only* locked tasks, so a missing id means unlocked;
- TTL expiry, release and takeover are all reflected, exactly as before;
- it never leaks a lock held on another project's task.

The lock is advisory throughout — nothing here should make it look enforcing.
"""
import datetime

import pytest

import api.routers.tasks as tasks_router


@pytest.fixture(autouse=True)
def _clean_locks():
    """The lock dict is module-global and shared across tests in a session.

    Without this a lock left behind by one test shows up in another's bulk
    response — the exact cross-talk this endpoint's project filter exists to
    prevent, which would make a real bug look like a flaky test.
    """
    tasks_router._TASK_LOCKS.clear()
    yield
    tasks_router._TASK_LOCKS.clear()


def _project(client, auth, name="bulk-lock"):
    res = client.post("/api/projects", json={
        "name": name, "slug": name, "creator": "tester",
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
    return client.post(f"/api/tasks/{task_id}/claim?client_id={cid}", headers=auth)


def _release(client, auth, task_id, cid):
    return client.delete(f"/api/tasks/{task_id}/claim?client_id={cid}", headers=auth)


def _bulk(client, auth, project_id):
    res = client.get(f"/api/tasks/lock-status?projectId={project_id}", headers=auth)
    assert res.status_code == 200, res.text
    return res.json()


def _single(client, auth, task_id):
    res = client.get(f"/api/tasks/{task_id}/lock-status", headers=auth)
    assert res.status_code == 200, res.text
    return res.json()


# --- routing --------------------------------------------------------------


def test_literal_route_is_not_shadowed_by_task_id(client, alice):
    """`/lock-status` must be declared before `/{task_id}`.

    Registered the other way round FastAPI matches it as `task_id="lock-status"`
    and fails with a 422 — a purely runtime failure that no amount of reading
    the handler would reveal.
    """
    pid = _project(client, alice)
    res = client.get(f"/api/tasks/lock-status?projectId={pid}", headers=alice)
    assert res.status_code == 200
    assert isinstance(res.json(), dict)


def test_per_task_endpoint_still_works(client, alice):
    """The batch endpoint must not have broken its per-task sibling."""
    pid = _project(client, alice)
    tid = _task(client, alice, pid)
    _claim(client, alice, tid, "tab-A")

    assert _single(client, alice, tid)["locked"] is True


# --- core behaviour -------------------------------------------------------


def test_unlocked_project_returns_empty(client, alice):
    pid = _project(client, alice)
    _task(client, alice, pid)
    _task(client, alice, pid)

    assert _bulk(client, alice, pid) == {}


def test_locked_task_is_reported(client, alice):
    pid = _project(client, alice)
    tid = _task(client, alice, pid)
    _claim(client, alice, tid, "tab-A")

    body = _bulk(client, alice, pid)
    assert str(tid) in body
    assert body[str(tid)]["locked"] is True
    assert body[str(tid)]["locked_by"] == "tab-A"
    assert body[str(tid)]["seconds_remaining"] > 0


def test_only_locked_tasks_are_returned(client, alice):
    """A free task is absent, not present-with-locked-false.

    The client treats a missing id as unlocked, so this shape is what keeps the
    response small on a project where nothing is being worked on.
    """
    pid = _project(client, alice)
    locked = _task(client, alice, pid, "locked.jpg")
    free = _task(client, alice, pid, "free.jpg")
    _claim(client, alice, locked, "tab-A")

    body = _bulk(client, alice, pid)
    assert str(locked) in body
    assert str(free) not in body


def test_agrees_with_per_task_endpoint(client, alice):
    """The per-task endpoint stays the reference definition of "locked".

    Any drift between the two would show up as a badge that disagrees with what
    the canvas says when the task is actually opened.
    """
    pid = _project(client, alice)
    held = _task(client, alice, pid, "held.jpg")
    free = _task(client, alice, pid, "free.jpg")
    _claim(client, alice, held, "tab-A")

    body = _bulk(client, alice, pid)

    single_held = _single(client, alice, held)
    assert body[str(held)]["locked"] == single_held["locked"] is True
    assert body[str(held)]["locked_by"] == single_held["locked_by"]

    assert _single(client, alice, free)["locked"] is False
    assert str(free) not in body


def test_multiple_locks_are_all_reported(client, alice):
    pid = _project(client, alice)
    a = _task(client, alice, pid, "a.jpg")
    b = _task(client, alice, pid, "b.jpg")
    c = _task(client, alice, pid, "c.jpg")
    _claim(client, alice, a, "tab-A")
    _claim(client, alice, b, "tab-B")

    body = _bulk(client, alice, pid)
    assert set(body) == {str(a), str(b)}
    assert body[str(a)]["locked_by"] == "tab-A"
    assert body[str(b)]["locked_by"] == "tab-B"
    assert str(c) not in body


def test_released_task_disappears(client, alice):
    """Release must be reflected, or the badge outlives the lock."""
    pid = _project(client, alice)
    tid = _task(client, alice, pid)
    _claim(client, alice, tid, "tab-A")
    assert str(tid) in _bulk(client, alice, pid)

    _release(client, alice, tid, "tab-A")
    assert str(tid) not in _bulk(client, alice, pid)


def test_takeover_reports_the_new_holder(client, alice):
    pid = _project(client, alice)
    tid = _task(client, alice, pid)
    _claim(client, alice, tid, "tab-A")
    _release(client, alice, tid, "tab-A")
    _claim(client, alice, tid, "tab-B")

    assert _bulk(client, alice, pid)[str(tid)]["locked_by"] == "tab-B"


# --- TTL ------------------------------------------------------------------


def test_expired_lock_is_not_reported(client, alice, monkeypatch):
    """A claim past its TTL is stale and must not show as busy.

    The claim timestamp is rewritten rather than sleeping through a 60 s TTL.
    """
    pid = _project(client, alice)
    tid = _task(client, alice, pid)
    _claim(client, alice, tid, "tab-A")

    stale = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        seconds=tasks_router.TASK_LOCK_TTL_SECONDS + 5
    )
    tasks_router._TASK_LOCKS[tid]["claimed_at"] = stale

    assert _bulk(client, alice, pid) == {}
    # And the per-task endpoint agrees, as it always did.
    assert _single(client, alice, tid)["locked"] is False


def test_expired_lock_is_evicted_from_the_dict(client, alice):
    """Reading stale entries should clean them up, as _lock_status does.

    Without this the dict grows for the life of the process on a busy project.
    """
    pid = _project(client, alice)
    tid = _task(client, alice, pid)
    _claim(client, alice, tid, "tab-A")

    stale = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        seconds=tasks_router.TASK_LOCK_TTL_SECONDS + 5
    )
    tasks_router._TASK_LOCKS[tid]["claimed_at"] = stale

    _bulk(client, alice, pid)
    assert tid not in tasks_router._TASK_LOCKS


def test_iteration_survives_eviction_during_the_scan(client, alice):
    """Evicting while iterating the lock dict must not raise.

    `_lock_status` pops stale entries, so scanning the dict live would mutate
    it mid-iteration. A mix of fresh and stale locks is what would trigger it.
    """
    pid = _project(client, alice)
    fresh = _task(client, alice, pid, "fresh.jpg")
    stale_a = _task(client, alice, pid, "stale-a.jpg")
    stale_b = _task(client, alice, pid, "stale-b.jpg")
    for tid, cid in ((fresh, "f"), (stale_a, "a"), (stale_b, "b")):
        _claim(client, alice, tid, cid)

    old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        seconds=tasks_router.TASK_LOCK_TTL_SECONDS + 5
    )
    tasks_router._TASK_LOCKS[stale_a]["claimed_at"] = old
    tasks_router._TASK_LOCKS[stale_b]["claimed_at"] = old

    body = _bulk(client, alice, pid)
    assert set(body) == {str(fresh)}


# --- isolation ------------------------------------------------------------


def test_locks_from_another_project_do_not_leak(client, alice):
    """The project filter is the reason this cannot report a foreign lock."""
    pid_a = _project(client, alice, "proj-a")
    pid_b = _project(client, alice, "proj-b")
    task_a = _task(client, alice, pid_a, "a.jpg")
    task_b = _task(client, alice, pid_b, "b.jpg")
    _claim(client, alice, task_a, "tab-A")
    _claim(client, alice, task_b, "tab-B")

    assert set(_bulk(client, alice, pid_a)) == {str(task_a)}
    assert set(_bulk(client, alice, pid_b)) == {str(task_b)}


# --- permissions ----------------------------------------------------------


def test_requires_project_access(client, alice, bob):
    """404 for a caller with no role, matching every project-scoped read."""
    pid = _project(client, alice)
    tid = _task(client, alice, pid)
    _claim(client, alice, tid, "tab-A")

    res = client.get(f"/api/tasks/lock-status?projectId={pid}", headers=bob)
    assert res.status_code == 404


def test_missing_project_id_is_rejected(client, alice):
    """projectId is required — an unscoped call would expose every lock."""
    res = client.get("/api/tasks/lock-status", headers=alice)
    assert res.status_code == 422


def test_nonexistent_project_is_404(client, alice):
    res = client.get("/api/tasks/lock-status?projectId=99999999", headers=alice)
    assert res.status_code == 404

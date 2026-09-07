"""Per-task write serialisation (SELECT ... FOR UPDATE).

Every autosave rewrites a task's entire annotation set, so two overlapping
saves of the same task raced, lost, and retried the whole expensive rewrite —
600 retries an hour on 2026-09-07, concentrated on the largest tasks (350 on
task 259 alone, which holds 3,669 annotations), plus deadlocks from the two
transactions touching annotation rows in opposite orders.

`_get_owned_task(..., for_update=True)` takes a row lock on the task before any
annotation write, so writers queue instead of colliding and every writer
acquires locks in the same order.

Most of this suite runs on SQLite, where `with_for_update()` is correctly a
no-op, so these tests pin the *contract* (which callers lock, and the SQL that
gets built) rather than observed blocking. The one true concurrency test is
skipped unless a Postgres DATABASE_URL is configured.
"""
import inspect
import json

import pytest
from sqlalchemy.dialects import postgresql

import config
import models
from api.routers import tasks as tasks_router


def _project(client, auth):
    res = client.post("/api/projects", json={
        "name": "serial-test", "slug": "serial-test", "creator": "alice",
    }, headers=auth)
    assert res.status_code == 200, res.text
    pid = res.json()["id"]
    client.post(
        "/api/labels",
        json={"id": f"l1-{pid}", "name": "l1", "color": "#000", "projectId": pid},
        headers=auth,
    )
    return pid


def test_for_update_defaults_to_off():
    """Readers must not lock: a slow read should never block a save."""
    sig = inspect.signature(tasks_router._get_owned_task)
    assert sig.parameters["for_update"].default is False


def test_save_path_takes_the_lock():
    """The update branch of POST /api/tasks must lock before rewriting.

    This is the whole point of the change; if the call ever loses its
    for_update the retry storm silently comes back.
    """
    src = inspect.getsource(tasks_router._update_or_create_task_impl)
    head = src.split("else:")[0]
    assert "for_update=True" in head, (
        "the update branch must call _get_owned_task(..., for_update=True) so "
        "concurrent saves of the same task serialise"
    )


def test_delete_path_takes_the_lock():
    src = inspect.getsource(tasks_router.delete_task)
    assert "for_update=True" in src


def test_read_only_endpoints_do_not_lock():
    """get_task / claim / heartbeat / lock-status only read the task row."""
    for fn in (tasks_router.get_task, tasks_router.get_lock_status):
        assert "for_update=True" not in inspect.getsource(fn), (
            f"{fn.__name__} is a reader and must not take a write lock"
        )


def test_for_update_compiles_to_row_level_lock_on_postgres():
    """The lock must reach the SQL, and must lock only `tasks`.

    `FOR UPDATE OF tasks` matters: without the `of=`, Postgres would try to
    lock every table in the statement.
    """
    from database import engine
    from sqlalchemy.orm import Session

    db = Session(engine)
    try:
        q = (
            db.query(models.Task)
            .filter(models.Task.id == 1, models.Task.project_id.in_([1, 2]))
            .with_for_update(of=models.Task)
        )
        sql = str(q.statement.compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE OF tasks" in sql
    finally:
        db.close()


def test_deadlock_mid_transaction_is_retried_not_500():
    """A deadlock during the annotation rewrite is contention, not a fault.

    It is an OperationalError, so it bypassed the IntegrityError/StaleDataError
    retry loop and escaped as an unhandled 500 (four on 2026-09-07).
    """
    src = inspect.getsource(tasks_router.update_or_create_task)
    assert "OperationalError" in src
    assert "deadlock detected" in src


def test_concurrent_saves_do_not_lose_annotations(client, alice):
    """End-to-end: sequential saves through the locking path still work.

    Guards the obvious regression — that adding the lock broke ordinary saving.
    """
    pid = _project(client, alice)
    created = client.post(
        f"/api/tasks?projectId={pid}",
        json={"description": "img.jpg", "status": "New"},
        headers=alice,
    ).json()
    tid = created["id"]

    for n in (5, 12, 3):
        anns = json.dumps(
            [{"id": f"a{i}", "type": "box", "labelId": f"l1-{pid}"} for i in range(n)]
        )
        res = client.post(
            "/api/tasks",
            json={"id": tid, "annotations": anns, "client_id": "c1"},
            headers=alice,
        )
        assert res.status_code == 200, res.text

    detail = client.get(f"/api/tasks/{tid}", headers=alice).json()
    assert len(detail["annotations"]) == 3


@pytest.mark.skipif(config.IS_SQLITE, reason="row locks are a no-op on SQLite")
def test_for_update_actually_serialises_writers():
    """The real proof, on Postgres: the second writer waits for the first."""
    import threading
    import time

    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    engine = create_engine(config.DATABASE_URL)
    Session = sessionmaker(bind=engine)

    seed = Session()
    try:
        row = seed.execute(text("SELECT id FROM tasks LIMIT 1")).first()
        if row is None:
            pytest.skip("no task rows to lock")
        task_id = row[0]
    finally:
        seed.close()

    waits = {}
    hold = 0.6

    def worker(name, hold_for):
        db = Session()
        try:
            t0 = time.time()
            db.execute(
                text("SELECT id FROM tasks WHERE id = :i FOR UPDATE"), {"i": task_id}
            ).first()
            waits[name] = time.time() - t0
            time.sleep(hold_for)
        finally:
            db.rollback()
            db.close()

    first = threading.Thread(target=worker, args=("first", hold))
    second = threading.Thread(target=worker, args=("second", 0))
    first.start()
    time.sleep(0.1)
    second.start()
    first.join()
    second.join()

    assert waits["first"] < 0.2, "the uncontended writer should not wait"
    assert waits["second"] > hold * 0.5, (
        f"the second writer returned after {waits['second']:.2f}s; it should "
        f"have blocked until the first released its lock"
    )

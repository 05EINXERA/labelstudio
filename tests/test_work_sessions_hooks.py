"""The task-save hook: what it records, what it must not, and its cost."""
import json

import pytest
from sqlalchemy import event

import config
import models
from api import work_sessions as ws
from database import SessionLocal, engine


def _project(client, auth):
    res = client.post("/api/projects", json={"name": "wp", "slug": "wp", "creator": "x"}, headers=auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _task(client, auth, pid):
    res = client.post("/api/tasks", params={"projectId": pid}, json={"description": "a.png"}, headers=auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _anns(n, comments=0):
    items = [{"id": f"r{i}", "type": "rectangle", "x": 1, "y": 1, "width": 2, "height": 2} for i in range(n)]
    items += [{"id": f"c{i}", "type": "comment", "x": 1, "y": 1, "text": "hi"} for i in range(comments)]
    return json.dumps(items)


def _save(client, auth, tid, **body):
    return client.post("/api/tasks", json={"id": tid, **body}, headers=auth)


def _uid(client, auth):
    username = client.get("/api/auth/me", headers=auth).json()["username"]
    with SessionLocal() as db:
        return db.query(models.User).filter(models.User.username == username).one().id


def _recs():
    return list(ws._LIVE.values())


@pytest.fixture
def setup(client, alice):
    pid = _project(client, alice)
    tid = _task(client, alice, pid)
    ws.reset_for_tests()  # creating the task goes through no hook, but be safe
    return alice, tid


def test_accepted_save_records_time_and_counts(client, monitor_on, setup):
    auth, tid = setup
    assert _save(client, auth, tid, time_spent_delta=30, annotations=_anns(2)).status_code == 200
    assert _save(client, auth, tid, time_spent_delta=20, annotations=_anns(5, comments=3)).status_code == 200
    (rec,) = _recs()
    assert rec["user_id"] == _uid(client, auth) and rec["task_id"] == tid
    assert rec["seconds"] == 50
    # Comments are not objects: 0 -> 2 on the first save, then 5 (not 8).
    assert (rec["objects_start"], rec["objects_end"]) == (0, 5)


def test_seconds_only_save_records_time_with_unknown_counts(client, monitor_on, setup):
    auth, tid = setup
    assert _save(client, auth, tid, time_spent_delta=40).status_code == 200
    (rec,) = _recs()
    assert rec["seconds"] == 40
    assert rec["objects_start"] is None and rec["objects_end"] is None


def test_save_carrying_nothing_records_nothing(client, monitor_on, setup):
    auth, tid = setup
    assert _save(client, auth, tid, status="In Progress").status_code == 200
    assert not _recs()


def test_task_creation_records_nothing(client, monitor_on, alice):
    pid = _project(client, alice)
    ws.reset_for_tests()
    res = client.post("/api/tasks", params={"projectId": pid},
                      json={"description": "n.png", "time_spent_delta": 30, "annotations": _anns(1)}, headers=alice)
    assert res.status_code == 200
    assert not _recs()


def test_refused_saves_leave_nothing(client, monitor_on, setup, bob):
    auth, tid = setup
    # 422: malformed updated_at.
    assert _save(client, auth, tid, time_spent_delta=30, annotations=_anns(1), updated_at="not-a-date").status_code == 422
    # 400: malformed annotations JSON.
    assert _save(client, auth, tid, time_spent_delta=30, annotations="{not json").status_code == 400
    # 409: a different client wrote since the caller read.
    assert _save(client, auth, tid, time_spent_delta=1, annotations=_anns(1), client_id="A").status_code == 200
    ws.reset_for_tests()
    res = _save(client, auth, tid, time_spent_delta=30, annotations=_anns(9),
                client_id="B", updated_at="2020-01-01T00:00:00+00:00")
    assert res.status_code == 409
    # 403/404: a user with no access to the task.
    res = _save(client, bob, tid, time_spent_delta=30, annotations=_anns(1))
    assert res.status_code in (403, 404)
    assert not _recs()


def test_flag_off_records_nothing(client, alice, monkeypatch):
    monkeypatch.setattr(config, "MONITOR_ENABLED", False)
    pid = _project(client, alice)
    tid = _task(client, alice, pid)
    assert _save(client, alice, tid, time_spent_delta=30, annotations=_anns(2)).status_code == 200
    assert not _recs()


def _statements_for_save(client, auth, tid):
    seen = []

    def before(conn, cursor, statement, *args):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    try:
        res = _save(client, auth, tid, time_spent_delta=30, annotations=_anns(3), client_id="A")
    finally:
        event.remove(engine, "before_cursor_execute", before)
    assert res.status_code == 200, res.text
    return len(seen)


def test_save_statement_count_is_identical_with_flag_on_and_off(client, setup, monkeypatch):
    auth, tid = setup
    monkeypatch.setattr(config, "MONITOR_ENABLED", False)
    _statements_for_save(client, auth, tid)  # warm-up: first save inserts the rows
    off = _statements_for_save(client, auth, tid)
    monkeypatch.setattr(config, "MONITOR_ENABLED", True)
    on = _statements_for_save(client, auth, tid)
    assert on == off
    assert _recs()  # and the hook really ran


def test_tracker_fault_does_not_break_the_save(client, monitor_on, setup, monkeypatch):
    auth, tid = setup

    def boom(*args, **kwargs):
        raise RuntimeError("tracker down")

    monkeypatch.setattr(ws, "_touch", boom)
    assert _save(client, auth, tid, time_spent_delta=30, annotations=_anns(1)).status_code == 200
    with SessionLocal() as db:
        assert db.query(models.Annotation).filter(models.Annotation.task_id == tid).count() == 1


def test_health_reports_monitoring_block(client):
    body = client.get("/health").json()
    assert set(body["monitoring"]) == {"enabled", "healthy"}

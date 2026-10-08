"""The two request hooks that feed the work-session tracker.

.devnotes/feature/team-monitoring/02_DESIGN.md § 3.2. What matters here is what
is *not* recorded (refused saves, seconds the system threw away) and that the
hooks add no database statement to the save path.
"""
import json

import pytest
from sqlalchemy import event

import config
from api import work_sessions as ws
from database import engine


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(config, "MONITOR_ENABLED", True)
    ws.reset_for_tests()
    yield
    ws.reset_for_tests()


def _shapes(n):
    return json.dumps([{"id": f"s{i}", "type": "box", "labelId": "l"} for i in range(n)])


@pytest.fixture
def env(client, alice, bob):
    uid = client.get("/api/auth/me", headers=alice).json()["id"]
    pid = client.post(
        "/api/projects", json={"name": "hk", "slug": "hk", "creator": "t"}, headers=alice
    ).json()["id"]

    def task(name="a.jpg"):
        r = client.post(
            f"/api/tasks?projectId={pid}",
            json={"description": name, "status": "New", "client_id": "c"},
            headers=alice,
        )
        return r.json()["id"]

    return {"client": client, "alice": alice, "bob": bob, "uid": uid, "task": task}


def _ping(env, who, task_id, seconds=30):
    return env["client"].post(
        "/api/time-logs/time",
        json={"name": "x", "time_logged": seconds, "task_id": task_id},
        headers=env[who],
    )


def _save(env, task_id, n, **extra):
    return env["client"].post(
        "/api/tasks",
        json={"id": task_id, "annotations": _shapes(n), "client_id": "c", **extra},
        headers=env["alice"],
    )


def test_a_banked_ping_is_recorded(env):
    tid = env["task"]()
    r = _ping(env, "alice", tid, 30)
    assert r.json()["status"] == "ok"
    assert ws._LIVE[(env["uid"], tid)]["seconds"] == 30


def test_a_ping_without_a_task_is_not_recorded(env):
    r = env["client"].post(
        "/api/time-logs/time", json={"name": "x", "time_logged": 30}, headers=env["alice"]
    )
    assert r.status_code == 200
    assert not ws._LIVE


def test_seconds_the_system_ignored_are_not_monitored(env):
    tid = env["task"]()
    r = _ping(env, "bob", tid, 30)  # bob has no role on alice's project
    assert r.json()["status"] == "ignored"
    assert not ws._LIVE


def test_an_accepted_save_records_before_and_after(env):
    tid = env["task"]()
    assert _save(env, tid, 3).status_code == 200
    assert _save(env, tid, 8).status_code == 200
    rec = ws._LIVE[(env["uid"], tid)]
    assert (rec["objects_start"], rec["objects_end"]) == (0, 8)


def test_a_refused_save_leaves_no_count_behind(env):
    tid = env["task"]()
    assert _save(env, tid, 5).status_code == 200
    res = env["client"].post(
        "/api/tasks",
        json={"id": tid, "annotations": "[]", "client_id": "c"},
        headers=env["alice"],
    )
    assert res.status_code == 422
    rec = ws._LIVE[(env["uid"], tid)]
    assert rec["objects_end"] == 5  # the refused empty set was not noted


def test_a_time_only_save_does_not_touch_the_tracker(env):
    tid = env["task"]()
    res = env["client"].post(
        "/api/tasks",
        json={"id": tid, "time_spent_delta": 30, "client_id": "c"},
        headers=env["alice"],
    )
    assert res.status_code == 200
    assert not ws._LIVE


def test_flag_off_records_nothing(env, monkeypatch):
    monkeypatch.setattr(config, "MONITOR_ENABLED", False)
    tid = env["task"]()
    _ping(env, "alice", tid)
    _save(env, tid, 2)
    assert not ws._LIVE


def _statements(fn):
    seen = []

    def spy(conn, cursor, statement, *a):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", spy)
    try:
        fn()
    finally:
        event.remove(engine, "before_cursor_execute", spy)
    return seen


def test_the_save_path_issues_the_same_statements_with_monitoring_on_or_off(env, monkeypatch):
    t_on, t_off = env["task"]("on.jpg"), env["task"]("off.jpg")
    _save(env, t_on, 2)   # warm both tasks identically
    _save(env, t_off, 2)

    on = _statements(lambda: _save(env, t_on, 4))
    monkeypatch.setattr(config, "MONITOR_ENABLED", False)
    off = _statements(lambda: _save(env, t_off, 4))
    assert len(on) == len(off)


def test_the_ping_path_issues_the_same_statements_with_monitoring_on_or_off(env, monkeypatch):
    tid = env["task"]()
    _ping(env, "alice", tid)  # warm
    on = _statements(lambda: _ping(env, "alice", tid))
    monkeypatch.setattr(config, "MONITOR_ENABLED", False)
    off = _statements(lambda: _ping(env, "alice", tid))
    assert len(on) == len(off)


def test_health_reports_the_monitor_block(client):
    body = client.get("/health").json()
    assert "monitor" in body and body["monitor"]["enabled"] is True
    assert body["monitor"]["healthy"] in (True, False)

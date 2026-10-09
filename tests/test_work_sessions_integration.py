"""One annotator's morning through the real endpoints, on a simulated clock.

Cross-checks the activity monitor against the components it must agree with:
  * login session history (api/presence.py, GET /api/team/{name}/sessions),
  * breaks (POST/PATCH /api/team/breaks),
  * the time the task save banks (tasks.time_spent).

Timeline (UTC, minutes):
  09:00  login
  09:00-09:10  task A, a save every 30 s carrying 30 s     (stretch A1)
  09:10-09:25  break; heartbeat continues, one status-only autosave
  09:25-09:35  task B, a save every 30 s carrying 30 s     (stretch B1)
  09:35-09:38  task A again, 5 saves                       (stretch A2: gap > 5 min)
  09:38  logout
"""
import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import config
import models
from api import work_sessions as ws
from database import SessionLocal

START = datetime(2026, 10, 9, 9, 0, 0, tzinfo=timezone.utc)
DAY = "2026-10-09"


class Clock:
    def __init__(self):
        self.t = START

    def advance(self, seconds):
        self.t += timedelta(seconds=seconds)

    def now(self, tz=None):
        return self.t if tz is not None else self.t.replace(tzinfo=None)


@pytest.fixture
def clock(monkeypatch):
    """Route the session/break/auth/tracker clocks through one controllable clock."""
    from api import auth as api_auth, presence
    from api.routers import auth as auth_router, team as team_router

    c = Clock()

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return c.now(tz)

    for module in (team_router, presence, auth_router, api_auth):
        monkeypatch.setattr(module, "datetime", FakeDatetime)
    monkeypatch.setattr(ws, "_now", lambda: c.t)
    return c


def _anns(n):
    return json.dumps([{"id": f"r{i}", "type": "rectangle", "x": 1, "y": 1, "width": 2, "height": 2} for i in range(n)])


def _setup(client):
    """An annotator (alice), a team creator (boss), two tasks (B pre-seeded with 4 shapes)."""
    suffix = uuid.uuid4().hex[:6]
    users = {}
    for key in ("alice", "boss"):
        name, password = f"{key}-{suffix}", "pw-12345678"
        res = client.post("/api/auth/register", json={"username": name, "password": password})
        assert res.status_code == 200, res.text
        client.cookies.clear()
        users[key] = {"name": name, "password": password}
    boss = {"Authorization": f"Bearer {client.post('/api/auth/token', data={'username': users['boss']['name'], 'password': users['boss']['password']}).json()['access_token']}"}
    client.cookies.clear()

    with SessionLocal() as db:
        team = models.Team(name=f"team-{suffix}", creator=users["boss"]["name"])
        db.add(team)
        db.commit()
        for n in (users["alice"]["name"], users["boss"]["name"]):
            if not db.get(models.TeamMember, n):
                db.add(models.TeamMember(name=n, time_logged=0))
            db.add(models.TeamMemberAssociation(member_name=n, team_id=team.id))
        db.commit()
        team_id = team.id

    # Alice owns the project and its tasks. Seeded before monitoring is on.
    login = client.post("/api/auth/token", data={"username": users["alice"]["name"], "password": users["alice"]["password"]})
    client.cookies.clear()
    alice = {"Authorization": f"Bearer {login.json()['access_token']}", "X-Annotator-Name": users["alice"]["name"]}
    pid = client.post("/api/projects", json={"name": f"p-{suffix}", "slug": f"p-{suffix}", "creator": "x"}, headers=alice).json()["id"]
    tasks = {}
    for key, desc in (("A", "task-a.png"), ("B", "task-b.png")):
        tasks[key] = client.post("/api/tasks", params={"projectId": pid}, json={"description": desc}, headers=alice).json()["id"]
    assert client.post("/api/tasks", json={"id": tasks["B"], "annotations": _anns(4)}, headers=alice).status_code == 200
    return users, boss, alice, team_id, tasks


def test_a_morning_reconciles_across_login_break_and_saves(client, clock, monkeypatch):
    users, boss, alice, team_id, tasks = _setup(client)
    name = users["alice"]["name"]
    monkeypatch.setattr(config, "MONITOR_ENABLED", True)
    ws.reset_for_tests()

    def save(task, seconds, n_shapes):
        body = {"id": tasks[task], "time_spent_delta": seconds, "annotations": _anns(n_shapes), "client_id": "tab-1"}
        res = client.post("/api/tasks", json=body, headers=alice)
        assert res.status_code == 200, res.text

    def beat():
        assert client.post("/api/team/ping", headers=alice).status_code == 200

    # 09:00 login: _setup's POST /api/auth/token already opened alice's login
    # session at the simulated 09:00:00 (a second login would supersede it).

    # 09:00-09:10 task A: 20 saves x 30 s, shapes grow 0 -> 10. Mid-way checkpoint.
    for step in range(20):
        clock.advance(30)
        save("A", 30, (step + 1) // 2)
        beat()
        if step == 9:
            assert ws.flush(now=clock.t) == 1
    assert clock.t == START + timedelta(minutes=10)

    # 09:10-09:25 break. The tab stays open (heartbeat continues); the timer is
    # paused, so the only save is a status-only autosave carrying no seconds.
    assert client.post("/api/team/breaks", headers=alice).status_code == 201
    for step in range(30):
        clock.advance(30)
        beat()
        if step == 9:
            res = client.post("/api/tasks", json={"id": tasks["A"], "status": "In Progress"}, headers=alice)
            assert res.status_code == 200
    assert client.patch("/api/team/breaks/current", headers=alice).json()["on_break"] is False
    assert clock.t == START + timedelta(minutes=25)

    # 09:25-09:35 task B: 20 saves x 30 s, shapes 4 -> 14.
    for step in range(20):
        clock.advance(30)
        save("B", 30, 4 + (step + 1) // 2)
        beat()

    # 09:35-09:38 back on A (its last save was 25 minutes ago): 5 saves, 10 -> 15 shapes.
    for step in range(5):
        clock.advance(30)
        save("A", 30, 10 + step + 1)
        beat()
    clock.advance(30)
    assert client.post("/api/auth/logout", headers=alice).status_code == 200  # 09:38
    assert ws.flush(now=clock.t) >= 1

    # --- the monitor, as the team creator sees it -----------------------------
    res = client.get(f"/api/teams/{team_id}/work-sessions/summary", params={"from": DAY, "to": DAY}, headers=boss)
    assert res.status_code == 200, res.text
    body = res.json()
    rows = {r["task_name"]: r for r in body["rows"] if r["member_name"] == name}
    a, b = rows["task-a.png"], rows["task-b.png"]

    assert (a["sessions"], a["active_seconds"]) == (2, 750)
    assert (a["objects_start"], a["objects_end"], a["objects_delta"]) == (0, 15, 15)
    assert (b["sessions"], b["active_seconds"]) == (1, 600)
    assert (b["objects_start"], b["objects_end"], b["objects_delta"]) == (4, 14, 10)
    assert a["first_start"].startswith("2026-10-09T09:00:00")
    assert a["last_end"].startswith("2026-10-09T09:37:30")

    # Saving time: what the monitor reports equals what the save banked.
    with SessionLocal() as db:
        banked = {k: db.get(models.Task, tid).time_spent for k, tid in tasks.items()}
        db_rows = db.query(models.WorkSession).filter(models.WorkSession.member_name == name).all()
    assert banked["A"] == a["active_seconds"] and banked["B"] == b["active_seconds"]
    assert len(db_rows) == 3  # the mid-way checkpoint updated its row, not duplicated it

    # --- login history and breaks, from their own endpoints --------------------
    hist = client.get(f"/api/team/{name}/sessions", params={"date": DAY, "tz_offset": 0}, headers=boss)
    assert hist.status_code == 200, hist.text
    hist = hist.json()
    (login,) = hist["sessions"]
    (brk,) = hist["breaks"]
    assert login["duration_seconds"] == 38 * 60 and login["ended_reason"] == "logout"
    assert brk["duration_seconds"] == 15 * 60 and brk["ended_reason"] == "resumed"
    assert hist["break_seconds"] == 15 * 60

    # --- reconciliation ---------------------------------------------------------
    total_active = a["active_seconds"] + b["active_seconds"]
    assert total_active <= hist["total_seconds"] - hist["break_seconds"]

    def utc(s):
        # The login-history endpoint returns naive timestamps on SQLite; both
        # components store UTC, so a bare value is UTC.
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    login_in, login_out = utc(login["login_at"]), utc(login["logout_at"])
    break_in, break_out = utc(brk["started_at"]), utc(brk["ended_at"])
    stretches = []
    for task in ("A", "B"):
        res = client.get(
            f"/api/teams/{team_id}/work-sessions",
            params={"member": name, "task_id": tasks[task], "from": DAY, "to": DAY}, headers=boss,
        )
        stretches += res.json()["rows"]
    assert len(stretches) == 3
    for s in stretches:
        start, end = utc(s["started_at"]), utc(s["last_at"])
        assert login_in <= start and end <= login_out, "work outside the login session"
        assert end <= break_in or start >= break_out, "work recorded during the break"


def test_restart_loses_at_most_one_flush_interval_and_splits_the_stretch(client, clock, monkeypatch):
    users, boss, alice, team_id, tasks = _setup(client)
    name = users["alice"]["name"]
    monkeypatch.setattr(config, "MONITOR_ENABLED", True)
    ws.reset_for_tests()

    def save(seconds, n):
        res = client.post("/api/tasks", json={"id": tasks["A"], "time_spent_delta": seconds, "annotations": _anns(n)}, headers=alice)
        assert res.status_code == 200, res.text

    for n in range(1, 5):          # four saves, then a checkpoint
        clock.advance(30)
        save(30, n)
    assert ws.flush(now=clock.t) == 1
    clock.advance(30)
    save(30, 5)                    # a fifth save that is never checkpointed
    ws.reset_for_tests()           # the process dies

    clock.advance(30)              # restarted; the annotator carries on
    save(30, 6)
    ws.flush(now=clock.t)

    with SessionLocal() as db:
        rows = db.query(models.WorkSession).filter(models.WorkSession.member_name == name).order_by(models.WorkSession.id).all()
        assert db.get(models.Task, tasks["A"]).time_spent == 180  # the save itself lost nothing
    assert [r.active_seconds for r in rows] == [120, 30]  # 30 s of monitor time lost, stretch split
    assert rows[1].objects_end == 6


def test_break_with_no_saves_leaves_no_phantom_time(client, clock, monkeypatch):
    users, boss, alice, team_id, tasks = _setup(client)
    name = users["alice"]["name"]
    monkeypatch.setattr(config, "MONITOR_ENABLED", True)
    ws.reset_for_tests()

    assert client.post("/api/team/breaks", headers=alice).status_code == 201
    for _ in range(40):            # 20 minutes of heartbeats and timer-less autosaves
        clock.advance(30)
        client.post("/api/team/ping", headers=alice)
        client.post("/api/tasks", json={"id": tasks["A"], "status": "In Progress"}, headers=alice)
    client.patch("/api/team/breaks/current", headers=alice)
    ws.flush(now=clock.t)

    body = client.get(f"/api/teams/{team_id}/work-sessions/summary", params={"from": DAY, "to": DAY}, headers=boss).json()
    assert body["rows"] == []
    assert name in {m["member_name"] for m in body["idle_members"]}

"""Team Activity read API - the owner's pivoted table.

.devnotes/feature/team-monitoring/02_DESIGN.md § 4 and 03_EDGE_CASES.md. Rows
are seeded straight into `work_sessions` so ranges and pivots are deterministic;
the capture side has its own tests.
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

import config
import models
from api.routers import team_activity
from database import SessionLocal, engine

DAY = datetime(2026, 10, 8, 4, 0, 0, tzinfo=timezone.utc)  # 09:45 Kathmandu
FROM = TO = "2026-10-08"


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(config, "MONITOR_ENABLED", True)


def _me(client, h):
    return client.get("/api/auth/me", headers=h).json()


@pytest.fixture
def team(client, alice, bob, carol):
    """alice owns the team; bob is a plain member; carol is outside it."""
    tid = client.post("/api/teams", json={"name": "Mon", "description": None}, headers=alice).json()["id"]
    r = client.post(
        f"/api/teams/{tid}/members",
        json={"username": _me(client, bob)["username"], "role": "member"},
        headers=alice,
    )
    assert r.status_code in (200, 201), r.text
    return {"id": tid, "alice": _me(client, alice)["id"], "bob": _me(client, bob)["id"],
            "carol": _me(client, carol)["id"]}


def _task(client, h, name="a.jpg"):
    pid = client.post("/api/projects", json={"name": name, "slug": name, "creator": "t"}, headers=h).json()["id"]
    return client.post(f"/api/tasks?projectId={pid}", json={"description": name, "status": "New"}, headers=h).json()["id"]


def _seed(user_id, task_id, start, minutes=10, seconds=300, o_start=None, o_end=None, name=None):
    db = SessionLocal()
    try:
        db.add(models.WorkSession(
            user_id=user_id, task_id=task_id, task_name=name,
            started_at=start, last_at=start + timedelta(minutes=minutes),
            active_seconds=seconds, objects_start=o_start, objects_end=o_end,
        ))
        db.commit()
    finally:
        db.close()


def _summary(client, h, team_id, **params):
    params.setdefault("from", FROM)
    params.setdefault("to", TO)
    return client.get(f"/api/teams/{team_id}/work-sessions/summary", params=params, headers=h)


# --- authorization ------------------------------------------------------------


def test_non_member_gets_404_and_plain_member_403(client, team, alice, bob, carol):
    assert _summary(client, carol, team["id"]).status_code == 404
    res = _summary(client, bob, team["id"])
    assert res.status_code == 403 and "manager" in res.json()["detail"].lower()
    assert _summary(client, alice, team["id"]).status_code == 200


def test_a_manager_may_view(client, team, alice, bob):
    client.patch(f"/api/teams/{team['id']}/members/{team['bob']}", json={"role": "manager"}, headers=alice)
    assert _summary(client, bob, team["id"]).status_code == 200


def test_an_instance_admin_who_is_not_a_member_may_view(client, team, carol):
    db = SessionLocal()
    try:
        db.get(models.User, team["carol"]).is_admin = True
        db.commit()
    finally:
        db.close()
    assert _summary(client, carol, team["id"]).status_code == 200
    assert client.get("/api/teams/999999/work-sessions/summary", headers=carol).status_code == 404


def test_permission_is_checked_before_the_range(client, team, carol):
    res = _summary(client, carol, team["id"], **{"from": "garbage"})
    assert res.status_code == 404  # not 400: a stranger learns nothing


# --- the pivot ----------------------------------------------------------------


def test_three_stretches_on_one_task_are_one_row(client, team, alice, bob):
    t = _task(client, alice, "pivot.jpg")
    _seed(team["bob"], t, DAY, seconds=600, o_start=12, o_end=20)
    _seed(team["bob"], t, DAY + timedelta(hours=1), seconds=300, o_start=20, o_end=24)
    _seed(team["bob"], t, DAY + timedelta(hours=2), seconds=120, o_start=24, o_end=31)
    body = _summary(client, alice, team["id"]).json()
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["username"] == _me(client, bob)["username"]
    assert row["task_name"] == "pivot.jpg"
    assert row["active_seconds"] == 1020 and row["sessions"] == 3
    assert (row["objects_start"], row["objects_end"], row["objects_delta"]) == (12, 31, 19)
    assert row["first_start"].startswith("2026-10-08T04:00") and row["last_end"].startswith("2026-10-08T06:10")


def test_two_tasks_or_two_people_are_separate_rows(client, team, alice, bob):
    t1, t2 = _task(client, alice, "one.jpg"), _task(client, alice, "two.jpg")
    _seed(team["bob"], t1, DAY)
    _seed(team["bob"], t2, DAY)
    _seed(team["alice"], t1, DAY)
    assert len(_summary(client, alice, team["id"]).json()["rows"]) == 3


def test_unmeasured_counts_are_null_not_zero(client, team, alice):
    t = _task(client, alice)
    _seed(team["bob"], t, DAY, o_start=None, o_end=None)
    row = _summary(client, alice, team["id"]).json()["rows"][0]
    assert row["objects_start"] is None and row["objects_delta"] is None


def test_the_first_and_last_stretch_decide_the_counts_whatever_the_insert_order(client, team, alice):
    t = _task(client, alice)
    _seed(team["bob"], t, DAY + timedelta(hours=2), o_start=24, o_end=31)  # latest, inserted first
    _seed(team["bob"], t, DAY, o_start=12, o_end=20)
    row = _summary(client, alice, team["id"]).json()["rows"][0]
    assert (row["objects_start"], row["objects_end"]) == (12, 31)


def test_filters_by_member_and_task_name(client, team, alice):
    t1, t2 = _task(client, alice, "cat.jpg"), _task(client, alice, "dog.jpg")
    _seed(team["bob"], t1, DAY)
    _seed(team["alice"], t2, DAY)
    assert len(_summary(client, alice, team["id"], user_id=team["bob"]).json()["rows"]) == 1
    rows = _summary(client, alice, team["id"], q="DOG").json()["rows"]
    assert [r["task_name"] for r in rows] == ["dog.jpg"]
    assert _summary(client, alice, team["id"], q="%").json()["rows"] == []  # escaped, not a wildcard
    assert _summary(client, alice, team["id"], user_id=team["carol"]).status_code == 404


def test_members_without_activity_are_listed_as_idle(client, team, alice, bob):
    t = _task(client, alice)
    _seed(team["alice"], t, DAY)
    body = _summary(client, alice, team["id"]).json()
    assert [m["user_id"] for m in body["idle_members"]] == [team["bob"]]


def test_a_deleted_task_keeps_its_snapshot_name(client, team, alice):
    t = _task(client, alice, "live-name.jpg")
    _seed(team["bob"], t, DAY, name="snapshot.jpg")
    client.delete(f"/api/tasks/{t}", headers=alice)
    row = _summary(client, alice, team["id"]).json()["rows"][0]
    assert row["task_name"] == "snapshot.jpg" and row["task_id"] is None


def test_people_outside_the_team_are_never_included(client, team, alice):
    t = _task(client, alice)
    _seed(team["carol"], t, DAY)
    assert _summary(client, alice, team["id"]).json()["rows"] == []


# --- ranges and time zones ------------------------------------------------------


def test_the_nepal_day_boundary(client, team, alice):
    t = _task(client, alice)
    # 18:30 UTC on 7 Oct is 00:15 on 8 Oct in Kathmandu (+05:45).
    _seed(team["bob"], t, datetime(2026, 10, 7, 18, 30, tzinfo=timezone.utc))
    assert len(_summary(client, alice, team["id"], **{"from": "2026-10-08", "to": "2026-10-08"}).json()["rows"]) == 1
    assert _summary(client, alice, team["id"], **{"from": "2026-10-07", "to": "2026-10-07"}).json()["rows"] == []


def test_range_validation(client, team, alice):
    assert _summary(client, alice, team["id"], **{"from": "nope"}).status_code == 400
    assert _summary(client, alice, team["id"], **{"from": "2026-10-09", "to": "2026-10-08"}).status_code == 400
    wide = _summary(client, alice, team["id"], **{"from": "2026-01-01", "to": "2026-12-31"})
    assert wide.status_code == 400 and "maximum" in wide.json()["detail"]


def test_a_stretch_belongs_to_the_day_it_started(client, team, alice):
    t = _task(client, alice)
    _seed(team["bob"], t, datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc), minutes=30)  # crosses local midnight
    assert len(_summary(client, alice, team["id"], **{"from": "2026-10-08", "to": "2026-10-08"}).json()["rows"]) == 1
    assert _summary(client, alice, team["id"], **{"from": "2026-10-09", "to": "2026-10-09"}).json()["rows"] == []


# --- off, caps, cost -------------------------------------------------------------


def test_disabled_says_so_instead_of_an_empty_table(client, team, alice, monkeypatch):
    monkeypatch.setattr(config, "MONITOR_ENABLED", False)
    body = _summary(client, alice, team["id"]).json()
    assert body["enabled"] is False and body["rows"] == []


def test_truncation_is_reported(client, team, alice, monkeypatch):
    monkeypatch.setattr(team_activity, "MAX_ROWS", 2)
    for i in range(3):
        _seed(team["bob"], _task(client, alice, f"t{i}.jpg"), DAY)
    body = _summary(client, alice, team["id"]).json()
    assert body["truncated"] is True and len(body["rows"]) == 2


def test_monitoring_since_explains_an_empty_range(client, team, alice):
    t = _task(client, alice)
    _seed(team["bob"], t, DAY)
    body = _summary(client, alice, team["id"], **{"from": "2026-01-01", "to": "2026-01-01"}).json()
    assert body["rows"] == [] and body["monitoring_since"].startswith("2026-10-08")


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


def test_a_get_never_writes_and_cost_does_not_grow_with_rows(client, team, alice):
    def run():
        assert _summary(client, alice, team["id"]).status_code == 200

    few = _statements(run)
    for i in range(15):
        _seed(team["bob"], _task(client, alice, f"g{i}.jpg"), DAY)
    many = _statements(run)
    for s in many:
        head = s.lstrip().split(None, 1)[0].upper()
        assert head not in ("INSERT", "UPDATE", "DELETE"), s
    assert len(many) == len(few)


# --- detail ----------------------------------------------------------------------


def test_detail_lists_the_stretches_behind_a_row_oldest_first(client, team, alice):
    t, other = _task(client, alice, "d.jpg"), _task(client, alice, "o.jpg")
    _seed(team["bob"], t, DAY + timedelta(hours=1), seconds=60, o_start=5, o_end=6)
    _seed(team["bob"], t, DAY, seconds=30, o_start=4, o_end=5)
    _seed(team["bob"], other, DAY, seconds=99)
    res = client.get(
        f"/api/teams/{team['id']}/work-sessions",
        params={"user_id": team["bob"], "task_id": t, "from": FROM, "to": TO}, headers=alice,
    )
    rows = res.json()["rows"]
    assert [r["active_seconds"] for r in rows] == [30, 60]


def test_detail_enforces_membership_and_role(client, team, alice, bob, carol):
    base = f"/api/teams/{team['id']}/work-sessions"
    assert client.get(base, params={"user_id": team["bob"]}, headers=bob).status_code == 403
    assert client.get(base, params={"user_id": team["bob"]}, headers=carol).status_code == 404
    assert client.get(base, params={"user_id": team["carol"]}, headers=alice).status_code == 404

"""GET /api/teams/{id}/work-sessions: authorization, pivoting, ranges, cost."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

import config
import models
from api.routers import team_activity
from database import SessionLocal, engine

DAY = datetime(2026, 10, 9, 8, 0, 0, tzinfo=timezone.utc)
D = "2026-10-09"


@pytest.fixture(autouse=True)
def _enabled(monitor_on):
    yield


def _username(client, auth):
    return client.get("/api/auth/me", headers=auth).json()["username"]


def _team(creator, members=(), name=None):
    with SessionLocal() as db:
        t = models.Team(name=name or f"team-{uuid.uuid4().hex[:6]}", creator=creator)
        db.add(t)
        db.commit()
        for m in {creator, *members}:
            if not db.get(models.TeamMember, m):
                db.add(models.TeamMember(name=m, time_logged=0))
            db.add(models.TeamMemberAssociation(member_name=m, team_id=t.id))
        db.commit()
        return t.id


def _task(name="a.png"):
    with SessionLocal() as db:
        t = models.Task(description=name)
        db.add(t)
        db.commit()
        return t.id


def _ws(member, task_id, start, seconds=60, o_start=None, o_end=None, task_name="a.png", minutes=5):
    with SessionLocal() as db:
        db.add(models.WorkSession(
            member_name=member, task_id=task_id, task_name=task_name, started_at=start,
            last_at=start + timedelta(minutes=minutes), active_seconds=seconds,
            objects_start=o_start, objects_end=o_end,
        ))
        db.commit()


def _get(client, auth, team_id, path="/summary", **params):
    return client.get(f"/api/teams/{team_id}/work-sessions{path}", params=params, headers=auth)


@pytest.fixture
def world(client, alice, bob):
    a, b = _username(client, alice), _username(client, bob)
    team_id = _team(a, [b])
    return {"alice": alice, "bob": bob, "a": a, "b": b, "team": team_id}


# --- authorization --------------------------------------------------------

def test_role_matrix(client, world, alice, bob):
    w = world
    assert _get(client, w["alice"], w["team"], **{"from": D}).status_code == 200
    res = _get(client, w["bob"], w["team"], **{"from": D})
    assert res.status_code == 403 and "creator" in res.json()["detail"]
    assert _get(client, w["alice"], 99999).status_code == 404
    other = _team(w["a"])  # a team bob has no standing in
    assert _get(client, w["bob"], other).status_code == 404


def test_permission_is_checked_before_the_range(client, world):
    w = world
    assert _get(client, w["bob"], w["team"], **{"from": "garbage"}).status_code == 403
    assert _get(client, w["alice"], w["team"], **{"from": "garbage"}).status_code == 400


def test_forged_annotator_header_grants_nothing(client, world):
    headers = {**world["bob"], "X-Annotator-Name": world["a"]}
    res = client.get(f"/api/teams/{world['team']}/work-sessions/summary", headers=headers)
    assert res.status_code == 403


def test_anonymous_is_rejected(client, world):
    assert client.get(f"/api/teams/{world['team']}/work-sessions/summary").status_code == 401


# --- pivot ------------------------------------------------------------------

def test_three_stretches_collapse_into_one_row(client, world):
    w = world
    t = _task()
    _ws(w["b"], t, DAY, 600, o_start=5, o_end=8)
    _ws(w["b"], t, DAY + timedelta(hours=1), 300, o_start=8, o_end=12)
    _ws(w["b"], t, DAY + timedelta(hours=2), 120, o_start=12, o_end=24)
    body = _get(client, w["alice"], w["team"], **{"from": D}).json()
    (row,) = body["rows"]
    assert row["member_name"] == w["b"] and row["task_id"] == t
    assert row["active_seconds"] == 1020 and row["sessions"] == 3
    assert (row["objects_start"], row["objects_end"], row["objects_delta"]) == (5, 24, 19)
    assert row["first_start"].startswith("2026-10-09T08:00:00")
    assert row["last_end"].startswith("2026-10-09T10:05:00")


def test_first_and_last_follow_time_not_insert_order(client, world):
    w = world
    t = _task()
    _ws(w["b"], t, DAY + timedelta(hours=2), 10, o_start=12, o_end=20)
    _ws(w["b"], t, DAY, 10, o_start=3, o_end=9)
    _ws(w["b"], t, DAY + timedelta(hours=1), 10, o_start=9, o_end=12)
    (row,) = _get(client, w["alice"], w["team"], **{"from": D}).json()["rows"]
    assert (row["objects_start"], row["objects_end"]) == (3, 20)


def test_separate_rows_per_member_and_task(client, world):
    w = world
    t1, t2 = _task("x.png"), _task("y.png")
    _ws(w["b"], t1, DAY, 10)
    _ws(w["b"], t2, DAY, 10)
    _ws(w["a"], t1, DAY, 10)
    rows = _get(client, w["alice"], w["team"], **{"from": D}).json()["rows"]
    assert len(rows) == 3


def test_unknown_counts_stay_null(client, world):
    w = world
    _ws(w["b"], _task(), DAY, 10)
    (row,) = _get(client, w["alice"], w["team"], **{"from": D}).json()["rows"]
    assert row["objects_start"] is None and row["objects_end"] is None and row["objects_delta"] is None


def test_outsiders_are_excluded(client, world):
    w = world
    _ws("stranger", _task(), DAY, 10)
    assert _get(client, w["alice"], w["team"], **{"from": D}).json()["rows"] == []


def test_deleted_tasks_keep_snapshot_names_and_stay_separate(client, world):
    w = world
    _ws(w["b"], None, DAY, 10, task_name="gone1.png")
    _ws(w["b"], None, DAY + timedelta(minutes=30), 10, task_name="gone2.png")
    rows = _get(client, w["alice"], w["team"], **{"from": D}).json()["rows"]
    assert sorted(r["task_name"] for r in rows) == ["gone1.png", "gone2.png"]


# --- filters ----------------------------------------------------------------

def test_member_and_task_filters(client, world):
    w = world
    _ws(w["b"], _task("dog_1.png"), DAY, 10, task_name="dog_1.png")
    _ws(w["b"], _task("cat%.png"), DAY, 10, task_name="cat%.png")
    _ws(w["a"], _task("dogx.png"), DAY, 10, task_name="dogx.png")
    base = {"from": D}
    assert len(_get(client, w["alice"], w["team"], **base, member=w["b"]).json()["rows"]) == 2
    # '_' and '%' are literals: "dog_" must not match "dogx".
    names = [r["task_name"] for r in _get(client, w["alice"], w["team"], **base, q="dog_").json()["rows"]]
    assert names == ["dog_1.png"]
    names = [r["task_name"] for r in _get(client, w["alice"], w["team"], **base, q="%").json()["rows"]]
    assert names == ["cat%.png"]
    assert _get(client, w["alice"], w["team"], **base, member="stranger").status_code == 404


def test_idle_members_and_monitoring_since(client, world):
    w = world
    _ws(w["b"], _task(), DAY - timedelta(days=30), 10)
    body = _get(client, w["alice"], w["team"], **{"from": D}).json()
    assert body["rows"] == []
    assert {m["member_name"] for m in body["idle_members"]} == {w["a"], w["b"]}
    assert body["monitoring_since"].startswith("2026-09-09")


# --- ranges -----------------------------------------------------------------

def test_day_boundary_uses_the_clients_offset(client, world):
    w = world
    # 18:30Z on the 9th is 00:15 on the 10th at UTC+05:45.
    _ws(w["b"], _task(), datetime(2026, 10, 9, 18, 30, tzinfo=timezone.utc), 10)
    on = lambda day, off: _get(client, w["alice"], w["team"], **{"from": day, "tz_offset": off}).json()["rows"]
    assert on("2026-10-09", 0) != []
    assert on("2026-10-09", 345) == []
    assert len(on("2026-10-10", 345)) == 1


def test_a_stretch_belongs_to_the_day_it_started(client, world):
    w = world
    _ws(w["b"], _task(), datetime(2026, 10, 9, 23, 50, tzinfo=timezone.utc), 10, minutes=30)
    assert len(_get(client, w["alice"], w["team"], **{"from": D}).json()["rows"]) == 1
    assert _get(client, w["alice"], w["team"], **{"from": "2026-10-10"}).json()["rows"] == []


def test_range_validation(client, world):
    w = world
    assert _get(client, w["alice"], w["team"], **{"from": "2026-10-09", "to": "2026-10-01"}).status_code == 400
    assert _get(client, w["alice"], w["team"], **{"from": "2026-01-01", "to": "2026-03-01"}).status_code == 400
    assert _get(client, w["alice"], w["team"], **{"from": "09/10/2026"}).status_code == 400
    assert _get(client, w["alice"], w["team"], **{"from": D, "tz_offset": 99999}).status_code == 422
    res = _get(client, w["alice"], w["team"], **{"from": "2026-10-01", "to": "2026-10-31"})
    assert res.status_code == 200 and res.json()["date_to"] == "2026-10-31"


def test_defaults_to_today(client, world):
    body = _get(client, world["alice"], world["team"]).json()
    assert body["date_from"] == body["date_to"]


# --- flag, truncation -------------------------------------------------------

def test_flag_off_says_so(client, world, monkeypatch):
    monkeypatch.setattr(config, "MONITOR_ENABLED", False)
    _ws(world["b"], _task(), DAY, 10)
    body = _get(client, world["alice"], world["team"], **{"from": D}).json()
    assert body["enabled"] is False and body["rows"] == []
    assert _get(client, world["alice"], world["team"], path="", member=world["b"]).json()["enabled"] is False


def test_truncation_is_reported(client, world, monkeypatch):
    monkeypatch.setattr(team_activity, "SUMMARY_ROW_LIMIT", 3)
    for i in range(5):
        _ws(world["b"], _task(f"t{i}.png"), DAY + timedelta(minutes=i), 10)
    body = _get(client, world["alice"], world["team"], **{"from": D}).json()
    assert body["truncated"] is True and len(body["rows"]) == 3


# --- cost -------------------------------------------------------------------

def _statements(client, auth, team):
    seen = []

    def before(conn, cursor, statement, *args):
        seen.append(statement.strip().split(None, 1)[0].upper())

    event.listen(engine, "before_cursor_execute", before)
    try:
        res = _get(client, auth, team, **{"from": D})
    finally:
        event.remove(engine, "before_cursor_execute", before)
    assert res.status_code == 200
    return seen


def test_get_writes_nothing_and_cost_does_not_grow_with_rows(client, world):
    w = world
    _ws(w["b"], _task("one.png"), DAY, 10)
    few = _statements(client, w["alice"], w["team"])
    for i in range(15):
        _ws(w["b"], _task(f"n{i}.png"), DAY + timedelta(minutes=i), 10)
    many = _statements(client, w["alice"], w["team"])
    assert len(few) == len(many)
    assert not {"INSERT", "UPDATE", "DELETE"} & set(many)


# --- detail -----------------------------------------------------------------

def test_detail_lists_stretches_in_time_order(client, world):
    w = world
    t = _task()
    _ws(w["b"], t, DAY + timedelta(hours=1), 20)
    _ws(w["b"], t, DAY, 10)
    _ws(w["b"], _task("other.png"), DAY, 99)
    body = _get(client, w["alice"], w["team"], path="", member=w["b"], task_id=t, **{"from": D}).json()
    assert [r["active_seconds"] for r in body["rows"]] == [10, 20]


def test_detail_for_a_deleted_task_matches_on_name(client, world):
    w = world
    _ws(w["b"], None, DAY, 7, task_name="gone.png")
    _ws(w["b"], None, DAY, 9, task_name="other.png")
    body = _get(client, w["alice"], w["team"], path="", member=w["b"], task_name="gone.png", **{"from": D}).json()
    assert [r["active_seconds"] for r in body["rows"]] == [7]


def test_detail_auth_and_membership(client, world):
    w = world
    assert _get(client, w["bob"], w["team"], path="", member=w["b"]).status_code == 403
    assert _get(client, w["alice"], w["team"], path="", member="stranger").status_code == 404
    assert _get(client, w["alice"], w["team"], path="").status_code == 422

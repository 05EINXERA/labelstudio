"""The work-session tracker and its write-behind flush.

.devnotes/feature/team-monitoring/02_DESIGN.md § 3 and 03_EDGE_CASES.md. Pure
tracker behaviour is tested with an explicit `now`; the flush is tested against
the real (SQLite) schema so FK behaviour is the database's, not a mock's.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event

import config
import models
from api import work_sessions as ws
from database import SessionLocal, engine

T0 = datetime(2026, 10, 8, 4, 0, 0, tzinfo=timezone.utc)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setattr(config, "MONITOR_ENABLED", True)
    monkeypatch.setattr(config, "MONITOR_SESSION_GAP_SECONDS", 300)
    ws.reset_for_tests()
    yield
    ws.reset_for_tests()


def live(user=1, task=1):
    return ws._LIVE.get((user, task))


# --- tracker (no database) ----------------------------------------------------


def test_a_ping_opens_a_stretch_and_a_second_extends_it():
    ws.note_time(1, 1, 30, now=at(0))
    ws.note_time(1, 1, 30, now=at(30))
    rec = live()
    assert rec["seconds"] == 60
    assert rec["started_at"] == at(0) and rec["last_at"] == at(30)
    assert len(ws._CLOSED) == 0


def test_gap_boundary_is_inclusive_so_exactly_the_threshold_splits():
    ws.note_time(1, 1, 30, now=at(0))
    ws.note_time(1, 1, 30, now=at(299.999))
    assert len(ws._CLOSED) == 0
    ws.note_time(1, 1, 30, now=at(299.999 + 300))  # gap == 300s exactly
    assert len(ws._CLOSED) == 1
    assert live()["seconds"] == 30


def test_zero_and_negative_pings_do_not_open_or_extend_a_stretch():
    ws.note_time(1, 1, 0, now=at(0))
    ws.note_time(1, 1, -5, now=at(1))
    assert live() is None
    ws.note_time(1, 1, 30, now=at(2))
    ws.note_time(1, 1, 0, now=at(100))
    assert live()["last_at"] == at(2)


def test_no_task_or_no_user_is_not_task_work():
    ws.note_time(1, None, 30, now=at(0))
    ws.note_time(None, 1, 30, now=at(0))
    ws.note_save(1, None, 1, 2, now=at(0))
    assert not ws._LIVE


def test_first_save_fixes_objects_start_and_each_save_moves_objects_end():
    ws.note_save(1, 1, 10, 12, now=at(0))
    ws.note_save(1, 1, 12, 20, now=at(5))
    rec = live()
    assert (rec["objects_start"], rec["objects_end"]) == (10, 20)


def test_unknown_counts_stay_unknown_not_zero():
    ws.note_save(1, 1, None, None, now=at(0))
    rec = live()
    assert rec["objects_start"] is None and rec["objects_end"] is None


def test_pairs_are_independent():
    ws.note_time(1, 1, 10, now=at(0))
    ws.note_time(1, 2, 20, now=at(0))
    ws.note_time(2, 1, 30, now=at(0))
    assert (live(1, 1)["seconds"], live(1, 2)["seconds"], live(2, 1)["seconds"]) == (10, 20, 30)


def test_disabled_records_nothing(monkeypatch):
    monkeypatch.setattr(config, "MONITOR_ENABLED", False)
    ws.note_time(1, 1, 30, now=at(0))
    ws.note_save(1, 1, 1, 2, now=at(0))
    assert not ws._LIVE and ws.flush() == 0


def test_never_raises_on_garbage():
    ws.note_time(1, 1, "not-a-number", now=at(0))
    ws.note_save(object(), 1, "x", "y", now="not-a-datetime")


def test_buffer_cap_drops_oldest_closed(monkeypatch):
    monkeypatch.setattr(config, "MONITOR_BUFFER_MAX", 3)
    for i in range(6):  # six stretches for one pair, each a new one
        ws.note_time(1, 1, 10, now=at(i * 1000))
    assert len(ws._LIVE) + len(ws._CLOSED) <= 3


# --- flush (real schema) ------------------------------------------------------


@pytest.fixture
def world(client, alice):
    """A real user, project and task, so the FKs are the database's own."""
    me = client.get("/api/auth/me", headers=alice)
    assert me.status_code == 200, me.text
    uid = me.json()["id"]
    proj = client.post("/api/projects", json={"name": "ws", "slug": "ws", "creator": "t"}, headers=alice)
    pid = proj.json()["id"]

    def make_task(name="img.jpg", n_shapes=0):
        r = client.post(f"/api/tasks?projectId={pid}", json={"description": name, "status": "New", "client_id": "c"}, headers=alice)
        tid = r.json()["id"]
        if n_shapes:
            shapes = [{"id": f"s{i}", "type": "box", "labelId": "l"} for i in range(n_shapes)]
            s = client.post(
                "/api/tasks",
                json={"id": tid, "annotations": json.dumps(shapes), "client_id": "c"},
                headers=alice,
            )
            assert s.status_code == 200, s.text
        # The setup save above went through the real hook; start from a clean tracker.
        ws.reset_for_tests()
        return tid

    return {"uid": uid, "make_task": make_task, "client": client, "auth": alice}


def rows(uid):
    db = SessionLocal()
    try:
        return db.query(models.WorkSession).filter_by(user_id=uid).order_by(models.WorkSession.id).all()
    finally:
        db.close()


def test_flush_inserts_then_updates_the_same_row(world):
    uid, tid = world["uid"], world["make_task"]("a.jpg")
    ws.note_time(uid, tid, 30, now=at(0))
    assert ws.flush(now=at(1)) == 1
    ws.note_time(uid, tid, 30, now=at(31))
    assert ws.flush(now=at(32)) == 1
    got = rows(uid)
    assert len(got) == 1
    assert got[0].active_seconds == 60 and got[0].task_name == "a.jpg"


def test_a_clean_flush_writes_nothing_twice(world):
    uid, tid = world["uid"], world["make_task"]()
    ws.note_time(uid, tid, 30, now=at(0))
    assert ws.flush(now=at(1)) == 1
    assert ws.flush(now=at(2)) == 0  # not dirty any more


def test_gap_closes_and_the_next_event_is_a_new_row(world):
    uid, tid = world["uid"], world["make_task"]()
    ws.note_time(uid, tid, 30, now=at(0))
    ws.flush(now=at(1))
    ws.note_time(uid, tid, 40, now=at(2000))
    ws.flush(now=at(2001))
    assert [r.active_seconds for r in rows(uid)] == [30, 40]


def test_look_only_stretch_gets_start_equal_end_from_one_count(world):
    uid, tid = world["uid"], world["make_task"](n_shapes=4)
    ws.note_time(uid, tid, 30, now=at(0))
    ws.flush(now=at(1))
    r = rows(uid)[0]
    assert (r.objects_start, r.objects_end) == (4, 4)


def test_a_save_after_the_checkpoint_keeps_the_first_start(world):
    uid, tid = world["uid"], world["make_task"](n_shapes=4)
    ws.note_time(uid, tid, 30, now=at(0))
    ws.flush(now=at(1))                       # counts 4 -> start=end=4
    ws.note_save(uid, tid, 4, 9, now=at(20))  # later edit
    ws.flush(now=at(21))
    r = rows(uid)[0]
    assert (r.objects_start, r.objects_end) == (4, 9)


def test_a_save_before_the_first_checkpoint_supplies_the_start(world):
    uid, tid = world["uid"], world["make_task"](n_shapes=4)
    ws.note_time(uid, tid, 30, now=at(0))
    ws.note_save(uid, tid, 4, 7, now=at(5))
    ws.flush(now=at(6))
    r = rows(uid)[0]
    assert (r.objects_start, r.objects_end) == (4, 7)


def test_task_deleted_while_buffered_still_keeps_the_row(world):
    uid, tid = world["uid"], world["make_task"]("gone.jpg")
    ws.note_time(uid, tid, 30, now=at(0))
    db = SessionLocal()
    try:
        db.query(models.Task).filter_by(id=tid).delete()
        db.commit()
    finally:
        db.close()
    assert ws.flush(now=at(1)) == 1
    got = rows(uid)
    assert len(got) == 1 and got[0].active_seconds == 30


def test_deleting_a_task_later_keeps_the_session_and_its_name(world):
    uid, tid = world["uid"], world["make_task"]("keep.jpg")
    ws.note_time(uid, tid, 30, now=at(0))
    ws.flush(now=at(1))
    r = world["client"].delete(f"/api/tasks/{tid}", headers=world["auth"])
    assert r.status_code == 200, r.text
    got = rows(uid)
    assert len(got) == 1 and got[0].task_name == "keep.jpg"


def test_flush_runs_one_count_not_one_per_stretch(world):
    uid = world["uid"]
    tids = [world["make_task"](f"t{i}.jpg") for i in range(8)]
    for tid in tids:
        ws.note_time(uid, tid, 30, now=at(0))
    seen = []

    def spy(conn, cursor, statement, *a):
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", spy)
    try:
        ws.flush(now=at(1))
    finally:
        event.remove(engine, "before_cursor_execute", spy)
    counts = [s for s in seen if "count(" in s.lower() and "annotations" in s.lower()]
    assert len(counts) == 1


def test_a_failed_flush_keeps_live_stretches_for_the_next_one(world, monkeypatch):
    uid, tid = world["uid"], world["make_task"]()
    ws.note_time(uid, tid, 30, now=at(0))
    real = ws._write

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(ws, "_write", boom)
    assert ws.flush(now=at(1)) == 0
    monkeypatch.setattr(ws, "_write", real)
    assert ws.flush(now=at(2)) == 1
    assert rows(uid)[0].active_seconds == 30


def test_drain_status_reports_enabled_and_buffer(world):
    ws.note_time(world["uid"], 1, 30, now=at(0))
    st = ws.drain_status()
    assert st["enabled"] is True and st["live"] == 1

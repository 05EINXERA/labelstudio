"""Checkpointing to the real schema."""
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import event

import models
from api import work_sessions as ws
from database import SessionLocal, engine

T0 = datetime(2026, 10, 9, 8, 0, 0, tzinfo=timezone.utc)


def _user(db, name=None):
    u = models.User(username=name or f"u-{uuid.uuid4().hex[:8]}", hashed_password="x")
    db.add(u)
    db.commit()
    return u


def _task(db, name="img.png", annotations=0, comments=0):
    t = models.Task(description=name, image_path=f"uploads/{name}")
    db.add(t)
    db.commit()
    for _ in range(annotations):
        db.add(models.Annotation(id=f"a-{uuid.uuid4().hex}", task_id=t.id, type="rectangle"))
    for _ in range(comments):
        db.add(models.Annotation(id=f"c-{uuid.uuid4().hex}", task_id=t.id, type="comment"))
    db.commit()
    return t


def _rows():
    with SessionLocal() as db:
        return db.query(models.WorkSession).order_by(models.WorkSession.id).all()


def _setup(annotations=0, name="img.png"):
    with SessionLocal() as db:
        u, t = _user(db), _task(db, name, annotations=annotations)
        return u.id, u.username, t.id


def test_insert_then_update_the_same_row(monitor_on):
    uid, name, tid = _setup()
    ws.note_save(uid, name, tid, 30, 1, 2, now=T0)
    assert ws.flush(now=T0) == 1
    ws.note_save(uid, name, tid, 30, 2, 5, now=T0 + timedelta(seconds=30))
    assert ws.flush(now=T0 + timedelta(seconds=30)) == 1
    (row,) = _rows()
    assert (row.active_seconds, row.objects_start, row.objects_end) == (60, 1, 5)
    assert row.task_name == "img.png" and row.member_name == name


def test_clean_flush_writes_nothing_twice(monitor_on):
    uid, name, tid = _setup()
    ws.note_save(uid, name, tid, 30, 1, 2, now=T0)
    assert ws.flush(now=T0) == 1
    assert ws.flush(now=T0 + timedelta(seconds=1)) == 0


def test_gap_makes_a_new_row(monitor_on):
    uid, name, tid = _setup()
    ws.note_save(uid, name, tid, 30, 1, 2, now=T0)
    ws.flush(now=T0)
    ws.note_save(uid, name, tid, 30, 2, 3, now=T0 + timedelta(seconds=600))
    ws.flush(now=T0 + timedelta(seconds=600))
    assert len(_rows()) == 2


def test_look_only_stretches_use_exactly_one_count_and_exclude_comments(monitor_on):
    with SessionLocal() as db:
        u = _user(db)
        tasks = [_task(db, f"t{i}.png", annotations=i + 1, comments=2) for i in range(4)]
        uid, name, tids = u.id, u.username, [t.id for t in tasks]
    for tid in tids:
        ws.note_save(uid, name, tid, 30, None, None, now=T0)
    seen = []

    def before(conn, cursor, statement, *args):
        s = statement.lower()
        if "count(" in s and "annotations" in s:
            seen.append(s)

    event.listen(engine, "before_cursor_execute", before)
    try:
        ws.flush(now=T0)
    finally:
        event.remove(engine, "before_cursor_execute", before)
    assert len(seen) == 1
    rows = {r.task_id: r for r in _rows()}
    for i, tid in enumerate(tids):
        assert (rows[tid].objects_start, rows[tid].objects_end) == (i + 1, i + 1)


def test_save_after_checkpoint_keeps_the_first_start(monitor_on):
    uid, name, tid = _setup(annotations=3)
    ws.note_save(uid, name, tid, 30, None, None, now=T0)  # look-only so far
    ws.flush(now=T0)                                      # fills start=end=3
    ws.note_save(uid, name, tid, 30, 3, 8, now=T0 + timedelta(seconds=30))
    ws.flush(now=T0 + timedelta(seconds=30))
    (row,) = _rows()
    assert (row.objects_start, row.objects_end) == (3, 8)


def test_save_before_checkpoint_supplies_the_start(monitor_on):
    uid, name, tid = _setup(annotations=3)
    ws.note_save(uid, name, tid, 30, 3, 8, now=T0)
    ws.flush(now=T0)
    (row,) = _rows()
    assert (row.objects_start, row.objects_end) == (3, 8)


def test_task_deleted_while_buffered_keeps_the_row(monitor_on):
    uid, name, tid = _setup()
    with SessionLocal() as db:
        db.query(models.Task).filter(models.Task.id == tid).delete()
        db.commit()
    ws.note_save(uid, name, tid, 30, None, None, now=T0)
    assert ws.flush(now=T0) == 1
    (row,) = _rows()
    assert row.task_id is None and row.active_seconds == 30
    assert row.objects_start is None  # unknown, never 0


def test_task_deleted_later_keeps_row_and_name(monitor_on):
    uid, name, tid = _setup(name="keepme.png")
    ws.note_save(uid, name, tid, 30, 1, 2, now=T0)
    ws.flush(now=T0)
    with SessionLocal() as db:
        db.query(models.Task).filter(models.Task.id == tid).delete()
        db.commit()
    (row,) = _rows()
    assert row.task_id is None and row.task_name == "keepme.png"


def test_failed_flush_keeps_live_stretches_and_drops_closed(monitor_on, monkeypatch):
    uid, name, tid = _setup()
    ws.note_save(uid, name, tid, 30, 1, 2, now=T0)
    ws.note_save(uid, name, tid, 30, 1, 2, now=T0 + timedelta(seconds=900))  # closes the first

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    with monkeypatch.context() as m:
        m.setattr(ws, "_write_batch", boom)
        assert ws.flush(now=T0 + timedelta(seconds=900)) == 0
    assert ws._CLOSED == [] and ws._LIVE[(uid, tid)]["dirty"] is True
    assert ws.flush(now=T0 + timedelta(seconds=901)) == 1

"""Tracker semantics (no database): sessions, gap boundary, counts, safety."""
from datetime import datetime, timedelta, timezone

import config
from api import work_sessions as ws

T0 = datetime(2026, 10, 9, 8, 0, 0, tzinfo=timezone.utc)


def _live():
    return list(ws._LIVE.values())


def test_opens_and_extends_one_stretch(monitor_on):
    ws.note_save(1, "alice", 10, 30, 5, 6, now=T0)
    ws.note_save(1, "alice", 10, 30, 6, 9, now=T0 + timedelta(seconds=30))
    (rec,) = _live()
    assert rec["seconds"] == 60
    assert rec["last_at"] == T0 + timedelta(seconds=30)
    assert not ws._CLOSED


def test_start_is_back_dated_by_the_seconds_reported(monitor_on):
    ws.note_save(1, "alice", 10, 30, 0, 1, now=T0)
    assert _live()[0]["started_at"] == T0 - timedelta(seconds=30)


def test_back_dating_is_capped_at_the_gap(monitor_on):
    ws.note_save(1, "alice", 10, 99999, 0, 1, now=T0)
    assert _live()[0]["started_at"] == T0 - timedelta(seconds=300)


def test_gap_boundary_is_inclusive(monitor_on):
    ws.note_save(1, "alice", 10, 10, 0, 1, now=T0)
    ws.note_save(1, "alice", 10, 10, 1, 2, now=T0 + timedelta(seconds=299))
    assert len(ws._CLOSED) == 0
    ws.note_save(1, "alice", 10, 10, 2, 3, now=T0 + timedelta(seconds=299 + 300))
    assert len(ws._CLOSED) == 1 and len(_live()) == 1


def test_zero_seconds_without_counts_is_ignored_and_does_not_extend(monitor_on):
    ws.note_save(1, "alice", 10, 10, 0, 1, now=T0)
    ws.note_save(1, "alice", 10, 0, None, None, now=T0 + timedelta(seconds=200))
    ws.note_save(1, "alice", 10, -5, None, None, now=T0 + timedelta(seconds=200))
    assert _live()[0]["last_at"] == T0


def test_missing_user_task_or_name_is_ignored(monitor_on):
    ws.note_save(None, "a", 10, 10, 0, 1, now=T0)
    ws.note_save(1, "a", None, 10, 0, 1, now=T0)
    ws.note_save(1, "", 10, 10, 0, 1, now=T0)
    assert not _live()


def test_first_save_fixes_start_and_each_save_moves_end(monitor_on):
    ws.note_save(1, "a", 10, 5, 7, 9, now=T0)
    ws.note_save(1, "a", 10, 5, 9, 12, now=T0 + timedelta(seconds=10))
    rec = _live()[0]
    assert (rec["objects_start"], rec["objects_end"]) == (7, 12)


def test_unknown_counts_stay_none(monitor_on):
    ws.note_save(1, "a", 10, 5, None, None, now=T0)
    rec = _live()[0]
    assert rec["objects_start"] is None and rec["objects_end"] is None


def test_pairs_are_independent(monitor_on):
    ws.note_save(1, "a", 10, 5, 0, 1, now=T0)
    ws.note_save(1, "a", 11, 5, 0, 1, now=T0)
    ws.note_save(2, "b", 10, 5, 0, 1, now=T0)
    assert len(_live()) == 3


def test_flag_off_records_nothing_and_flush_is_a_noop(monkeypatch):
    monkeypatch.setattr(config, "MONITOR_ENABLED", False)
    ws.reset_for_tests()
    ws.note_save(1, "a", 10, 5, 0, 1, now=T0)
    assert not _live() and ws.flush() == 0


def test_garbage_input_never_raises(monitor_on):
    ws.note_save("x", object(), "y", "z", "p", "q", now=T0)
    ws.note_save(1, "a", 10, "not-a-number", 0, 1, now=T0)


def test_buffer_cap_drops_oldest_closed_first(monitor_on, monkeypatch):
    monkeypatch.setattr(config, "MONITOR_BUFFER_MAX", 3)
    for i in range(3):
        ws.note_save(1, "a", 10, 5, 0, 1, now=T0 + timedelta(seconds=400 * i))
    ws.note_save(1, "a", 11, 5, 0, 1, now=T0 + timedelta(seconds=2000))
    assert len(ws._LIVE) + len(ws._CLOSED) <= 3


def test_drain_status_off_is_healthy(monkeypatch):
    monkeypatch.setattr(config, "MONITOR_ENABLED", False)
    st = ws.drain_status()
    assert st["enabled"] is False and st["healthy"] is True

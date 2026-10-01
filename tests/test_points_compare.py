"""Comparing a shape's `points` by value, and the JSON parser behind it.

Phase 1 of .devnotes/fix-performance-upgrade/. The live profile showed that a
save spent 38% of all server CPU re-serialising every polygon's vertices to
text, only to compare with the stored text and find nothing had moved. The diff
now compares the *parsed value* and serialises only a shape that really
changed. These tests pin that the change is invisible on the wire and in the
database, and that a doubt always resolves to "write it", never to "skip it".
"""
import json
import math
import random

import pytest
from sqlalchemy import event

import fastjson
import models
from database import SessionLocal
from formats.annotation_rows import (
    points_equal,
    rows_to_dicts,
    sync_task_annotations_for_project,
)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(params=["orjson", "stdlib"])
def parser(request, monkeypatch):
    """Run the parser tests against orjson (when installed) and the fallback."""
    if request.param == "stdlib":
        monkeypatch.setattr(fastjson, "orjson", None)
    elif not fastjson.HAVE_ORJSON:
        pytest.skip("orjson not installed")
    return request.param


# --- fastjson ---------------------------------------------------------------

CORPUS = [
    '[]', '{}', 'null', '3', '"text"', '[1, 2.5, -3e2]',
    '[{"x": 1.5, "y": 2.5}, {"x": 3, "y": 4}]',
    '[[0.1, 0.2], [123456.789012345, 9.87654321e-5]]',
    '{"a": {"b": [true, false, null]}}',
    '"\\u00e9\\u4e2d"',
    # things stdlib accepts and orjson refuses -- must still parse
    '[NaN]', '[Infinity, -Infinity]',
    '"\\ud800"',
]


@pytest.mark.parametrize("text", CORPUS)
def test_loads_matches_stdlib_on_everything_stdlib_accepts(parser, text):
    expected = json.loads(text)
    actual = fastjson.loads(text)
    # NaN != NaN, so compare through repr for that one case.
    assert repr(actual) == repr(expected)


def test_integers_wider_than_64_bits_are_the_same_double(parser):
    """orjson returns a float where the stdlib returns an exact int.

    Documented in fastjson.py: harmless for browser-originated documents, as
    the double is identical. What must hold is that the value is never *lost*.
    """
    text = "[123456789012345678901234567890, 100000000000000000000]"
    got = fastjson.loads(text)
    assert [float(v) for v in got] == [float(v) for v in json.loads(text)]


def test_loads_accepts_bytes(parser):
    assert fastjson.loads(b'{"a": [1, 2]}') == {"a": [1, 2]}


@pytest.mark.parametrize("bad", ['', '{', '[1,', 'nope', '{"a": }'])
def test_loads_raises_valueerror_on_invalid_input(parser, bad):
    with pytest.raises(ValueError):
        fastjson.loads(bad)


def test_loads_float_parsing_is_identical_to_stdlib(parser):
    """Coordinates must round-trip to the very same double either way."""
    rng = random.Random(7)
    numbers = [rng.uniform(-5000, 5000) for _ in range(2000)]
    numbers += [rng.random() * 10 ** rng.randint(-8, 8) for _ in range(2000)]
    text = json.dumps(numbers)
    assert fastjson.loads(text) == json.loads(text)


# --- points_equal -----------------------------------------------------------

POINTS = [{"x": 1.5, "y": 2.5}, {"x": 3.0, "y": 4.25}]


def test_equal_when_same_value(parser):
    assert points_equal(json.dumps(POINTS), [dict(p) for p in POINTS])


def test_equal_ignores_whitespace_and_key_order_in_stored_text(parser):
    compact = '[{"y":2.5,"x":1.5},{"y":4.25,"x":3.0}]'
    assert points_equal(compact, [dict(p) for p in POINTS])


def test_not_equal_when_one_vertex_moved(parser):
    moved = [dict(p) for p in POINTS]
    moved[1]["x"] += 0.001
    assert not points_equal(json.dumps(POINTS), moved)


def test_not_equal_when_a_vertex_is_added_or_removed(parser):
    stored = json.dumps(POINTS)
    assert not points_equal(stored, POINTS + [{"x": 0, "y": 0}])
    assert not points_equal(stored, POINTS[:1])


def test_list_style_points(parser):
    stored = json.dumps([[0.0, 0], [0.5, 1]])
    assert points_equal(stored, [[0.0, 0], [0.5, 1]])
    assert not points_equal(stored, [[0.0, 0], [0.5, 2]])


def test_none_handling(parser):
    assert points_equal(None, None)
    assert not points_equal(None, [])
    assert not points_equal("[]", None)
    assert points_equal("[]", [])


def test_unparseable_stored_text_is_never_equal(parser):
    assert not points_equal("{not json", POINTS)


def test_nan_is_never_value_equal_so_the_caller_re_checks_text(parser):
    stored = json.dumps([{"x": float("nan"), "y": 1.0}])
    assert not points_equal(stored, [{"x": float("nan"), "y": 1.0}])


# --- the diff itself, through the real tables ---------------------------------

def _task(client, alice, db):
    res = client.post("/api/projects", json={
        "name": "pc", "slug": "pc", "creator": "alice"}, headers=alice)
    assert res.status_code == 200, res.text
    res = client.post(
        f"/api/tasks?projectId={res.json()['id']}",
        json={"description": "i.jpg", "status": "New", "client_id": "pc-tab"},
        headers=alice,
    )
    assert res.status_code == 200, res.text
    return db.get(models.Task, res.json()["id"])


def _shape(i, rng, verts=12):
    return {
        "id": f"s{i}", "type": "polygon",
        "points": [{"x": rng.uniform(0, 3000), "y": rng.uniform(0, 3000)}
                   for _ in range(verts)],
    }


def _sync(db, task, payload):
    changed = sync_task_annotations_for_project(db, task, payload)
    db.commit()
    return changed


class _Updates:
    """Counts UPDATE statements against `annotations` while active."""

    def __init__(self, db):
        self.engine = db.get_bind()
        self.n = 0

    def _record(self, conn, cursor, statement, params, context, executemany):
        if statement.strip().upper().startswith("UPDATE ANNOTATIONS"):
            self.n += 1

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._record)


def test_resaving_an_unchanged_set_changes_and_writes_nothing(client, alice, db, parser):
    task = _task(client, alice, db)
    rng = random.Random(1)
    payload = [_shape(i, rng) for i in range(40)]
    assert _sync(db, task, payload) is True
    with _Updates(db) as updates:
        assert _sync(db, task, json.loads(json.dumps(payload))) is False
    assert updates.n == 0


def test_stored_text_in_a_different_format_is_not_rewritten(client, alice, db, parser):
    """Rows written before this change keep their old text; equal value, no write."""
    task = _task(client, alice, db)
    rng = random.Random(2)
    payload = [_shape(i, rng) for i in range(10)]
    _sync(db, task, payload)
    for row in task.annotation_rows:           # re-store compactly, as another writer might
        row.points = json.dumps(json.loads(row.points), separators=(",", ":"))
    db.commit()
    db.expire_all()
    task = db.get(models.Task, task.id)
    with _Updates(db) as updates:
        assert _sync(db, task, json.loads(json.dumps(payload))) is False
    assert updates.n == 0


def test_moving_one_vertex_updates_exactly_one_row(client, alice, db, parser):
    task = _task(client, alice, db)
    rng = random.Random(3)
    payload = [_shape(i, rng) for i in range(40)]
    _sync(db, task, payload)
    payload[17]["points"][4]["y"] += 1.0
    with _Updates(db) as updates:
        assert _sync(db, task, payload) is True
    assert updates.n == 1
    db.expire_all()
    stored = {r["id"]: r for r in rows_to_dicts(db.get(models.Task, task.id).annotation_rows)}
    assert stored["s17"]["points"] == payload[17]["points"]


def test_points_removed_and_added_back(client, alice, db, parser):
    task = _task(client, alice, db)
    rng = random.Random(4)
    shape = _shape(0, rng)
    _sync(db, task, [shape])
    no_points = {k: v for k, v in shape.items() if k != "points"}
    assert _sync(db, task, [no_points]) is True
    db.expire_all()
    assert "points" not in rows_to_dicts(db.get(models.Task, task.id).annotation_rows)[0]
    assert _sync(db, db.get(models.Task, task.id), [shape]) is True
    db.expire_all()
    assert rows_to_dicts(db.get(models.Task, task.id).annotation_rows)[0]["points"] == shape["points"]


def test_nan_coordinates_do_not_cause_a_rewrite_on_every_save(client, alice, db, parser):
    task = _task(client, alice, db)
    payload = json.loads('[{"id": "n1", "type": "polygon", "points": [{"x": NaN, "y": 1.0}]}]')
    assert _sync(db, task, payload) is True
    with _Updates(db) as updates:
        again = json.loads('[{"id": "n1", "type": "polygon", "points": [{"x": NaN, "y": 1.0}]}]')
        assert _sync(db, task, again) is False
    assert updates.n == 0


def test_random_edit_sequences_always_leave_exactly_what_was_sent(client, alice, db, parser):
    """Differential check: after every save the rows read back as the payload,
    and `changed` is True exactly when the payload differs from the last one."""
    task = _task(client, alice, db)
    rng = random.Random(11)
    current = [_shape(i, rng) for i in range(25)]
    _sync(db, task, current)
    next_id = 25
    for step in range(30):
        payload = json.loads(json.dumps(current))
        action = rng.choice(["noop", "move", "add", "delete", "reshape", "reorder"])
        if action == "move" and payload:
            s = rng.choice(payload)
            s["points"][rng.randrange(len(s["points"]))]["x"] += rng.uniform(0.5, 5)
        elif action == "add":
            payload.append(_shape(next_id, rng)); next_id += 1
        elif action == "delete" and payload:
            payload.pop(rng.randrange(len(payload)))
        elif action == "reshape" and payload:
            s = rng.choice(payload)
            s["points"].append({"x": rng.random(), "y": rng.random()})
        elif action == "reorder" and len(payload) > 1:
            rng.shuffle(payload)
        expect_change = payload != current
        changed = _sync(db, db.get(models.Task, task.id), payload)
        assert changed == expect_change, f"step {step} {action}"
        db.expire_all()
        got = rows_to_dicts(db.get(models.Task, task.id).annotation_rows)
        assert got == payload, f"step {step} {action}"
        current = payload

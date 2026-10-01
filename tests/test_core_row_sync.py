"""The Core-based row diff against the ORM one it replaced.

`sync_task_annotations` used to load every shape of a task as an ORM object and
let SQLAlchemy's dirty tracking decide what to write. It now reads the stored
rows as plain tuples and issues explicit DELETE / UPDATE / upsert statements --
about half the CPU of what was left of a save
(.devnotes/fix-performance-upgrade/01_PROFILE_ANALYSIS.md). That is the hot data
path, so correctness is established the strongest way available: the original
implementation is kept verbatim in `tests/_reference_sync.py`, and for any
sequence of payloads both must leave a task's rows identical and report the same
`changed` answer.
"""
import importlib.util
import json
import random
from pathlib import Path

import pytest
from sqlalchemy import event

import models
from database import SessionLocal
from formats.annotation_rows import (
    load_stored_rows,
    sync_task_annotations,
    sync_task_annotations_for_project,
)

# Loaded by path, not `from tests._reference_sync import ...`: a stray package
# named `tests` in the venv's site-packages shadows this directory, which is why
# a few other modules here fail to collect.
_spec = importlib.util.spec_from_file_location(
    "_reference_sync", Path(__file__).with_name("_reference_sync.py"))
_reference = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_reference)
reference_sync_task_annotations = _reference.reference_sync_task_annotations


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _two_tasks(client, alice, db, with_labels=False):
    res = client.post("/api/projects", json={
        "name": "cs", "slug": "cs", "creator": "alice"}, headers=alice)
    pid = res.json()["id"]
    labels = []
    if with_labels:
        # Real rows: annotations.label_id has a foreign key, and the diff must
        # behave the same against labels that exist.
        for n in (1, 2):
            lid = f"L{n}-{pid}"
            res = client.post("/api/labels", json={
                "id": lid, "name": f"cls{n}", "color": "#111", "projectId": pid},
                headers=alice)
            assert res.status_code == 200, res.text
            labels.append(lid)
    ids = []
    for name in ("new.jpg", "ref.jpg"):
        res = client.post(
            f"/api/tasks?projectId={pid}",
            json={"description": name, "status": "New", "client_id": "cs-tab"},
            headers=alice)
        assert res.status_code == 200, res.text
        ids.append(res.json()["id"])
    if with_labels:
        return pid, ids, labels
    return pid, ids


def _table(db, task_id):
    """Everything stored for a task, comparable across tasks."""
    db.expire_all()
    rows = (db.query(models.Annotation)
            .filter(models.Annotation.task_id == task_id)
            .order_by(models.Annotation.id).all())
    cols = ("id", "label_id", "type", "points", "x", "y", "width", "height",
            "text", "color", "order", "seq", "group_id", "extra")
    return [{c: getattr(r, c) for c in cols} for r in rows]


def _apply_new(db, project_id, task_id, payload, label_ids):
    task = db.get(models.Task, task_id)
    changed = sync_task_annotations(db, task, payload, label_ids)
    db.commit()
    return changed


def _apply_ref(db, task_id, payload, label_ids):
    task = db.get(models.Task, task_id)
    changed = reference_sync_task_annotations(db, task, payload, label_ids)
    db.commit()
    return changed


def _shape(i, rng, verts=8):
    return {
        "id": f"s{i}", "type": "polygon", "color": "#abc",
        "points": [{"x": rng.uniform(0, 3000), "y": rng.uniform(0, 3000)}
                   for _ in range(verts)],
        "x": rng.uniform(0, 100), "y": rng.uniform(0, 100),
        "width": rng.uniform(1, 50), "height": rng.uniform(1, 50),
    }


def _assert_same(db, new_id, ref_id, context=""):
    new, ref = _table(db, new_id), _table(db, ref_id)
    assert new == ref, f"tables diverged {context}"


# --- differential -------------------------------------------------------------

@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
def test_random_sequences_leave_identical_tables(client, alice, db, seed):
    pid, (new_id, ref_id), (l1, l2) = _two_tasks(client, alice, db, with_labels=True)
    rng = random.Random(seed)
    labels = {l1, l2}
    payload = [_shape(i, rng) for i in range(30)]
    next_id = 30

    assert _apply_new(db, pid, new_id, payload, labels) == _apply_ref(db, ref_id, payload, labels)
    _assert_same(db, new_id, ref_id, "initial")

    for step in range(60):
        payload = json.loads(json.dumps(payload))
        action = rng.choice(["noop", "move", "add", "delete", "delete_first",
                             "reshape", "reorder", "relabel", "text", "unpoint",
                             "dup", "orphan"])
        if action == "move" and payload:
            s = rng.choice(payload)
            if s.get("points"):
                s["points"][rng.randrange(len(s["points"]))]["x"] += rng.uniform(0.5, 9)
        elif action == "add":
            payload.insert(rng.randrange(len(payload) + 1), _shape(next_id, rng))
            next_id += 1
        elif action == "delete" and payload:
            payload.pop(rng.randrange(len(payload)))
        elif action == "delete_first" and payload:
            payload.pop(0)
        elif action == "reshape" and payload:
            s = rng.choice(payload)
            s.setdefault("points", []).append({"x": rng.random(), "y": rng.random()})
        elif action == "reorder" and len(payload) > 1:
            rng.shuffle(payload)
        elif action == "relabel" and payload:
            rng.choice(payload)["labelId"] = rng.choice([l1, l2])
        elif action == "text" and payload:
            rng.choice(payload)["text"] = rng.choice(["a", "b", None])
        elif action == "unpoint" and payload:
            payload[rng.randrange(len(payload))].pop("points", None)
        elif action == "dup" and payload:
            payload.append(dict(rng.choice(payload)))        # repeated id: later copy dropped
        elif action == "orphan" and payload:
            rng.choice(payload)["labelId"] = "GONE"          # not a known label

        got_new = _apply_new(db, pid, new_id, payload, labels)
        got_ref = _apply_ref(db, ref_id, payload, labels)
        assert got_new == got_ref, f"seed {seed} step {step} {action}: changed flag"
        _assert_same(db, new_id, ref_id, f"seed {seed} step {step} {action}")


def test_emptying_a_task_matches_the_reference(client, alice, db):
    pid, (new_id, ref_id) = _two_tasks(client, alice, db)
    rng = random.Random(9)
    payload = [_shape(i, rng) for i in range(12)]
    _apply_new(db, pid, new_id, payload, None)
    _apply_ref(db, ref_id, payload, None)
    assert _apply_new(db, pid, new_id, [], None) == _apply_ref(db, ref_id, [], None)
    _assert_same(db, new_id, ref_id)
    assert _table(db, new_id) == []


def test_deleting_more_rows_than_one_chunk(client, alice, db):
    """Deletes are issued in chunks; more than one chunk must all go."""
    pid, (new_id, ref_id) = _two_tasks(client, alice, db)
    rng = random.Random(10)
    payload = [_shape(i, rng, verts=3) for i in range(1300)]
    _apply_new(db, pid, new_id, payload, None)
    _apply_ref(db, ref_id, payload, None)
    keep = payload[:7]
    assert _apply_new(db, pid, new_id, keep, None) == _apply_ref(db, ref_id, keep, None)
    _assert_same(db, new_id, ref_id)
    assert len(_table(db, new_id)) == 7


def test_unknown_labels_are_preserved_in_extra_the_same_way(client, alice, db):
    pid, (new_id, ref_id), (l1, _) = _two_tasks(client, alice, db, with_labels=True)
    payload = [{"id": "a", "type": "box", "labelId": "DEAD", "x": 1.0},
               {"id": "b", "type": "box", "labelId": l1, "x": 2.0}]
    _apply_new(db, pid, new_id, payload, {l1})
    _apply_ref(db, ref_id, payload, {l1})
    _assert_same(db, new_id, ref_id)
    stored = {r["id"]: r for r in _table(db, new_id)}
    assert stored["a"]["label_id"] is None and "DEAD" in stored["a"]["extra"]


# --- what is written ----------------------------------------------------------

class _Statements:
    def __init__(self, db):
        self.engine = db.get_bind()
        self.updates, self.deletes, self.inserts = [], [], []

    def _record(self, conn, cursor, statement, params, context, executemany):
        head = statement.strip().upper()
        if head.startswith("UPDATE ANNOTATIONS"):
            self.updates.append(statement)
        elif head.startswith("DELETE FROM ANNOTATIONS"):
            self.deletes.append(statement)
        elif head.startswith("INSERT INTO ANNOTATIONS"):
            self.inserts.append(statement)

    def __enter__(self):
        event.listen(self.engine, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine, "before_cursor_execute", self._record)


def test_deleting_an_early_shape_renumbers_seq_without_rewriting_points(client, alice, db):
    """The ORM wrote only dirty columns; so must this. Deleting shape 0 shifts
    `seq` on every shape after it, and that must not drag `points` along."""
    pid, (new_id, _) = _two_tasks(client, alice, db)
    rng = random.Random(11)
    payload = [_shape(i, rng) for i in range(40)]
    _apply_new(db, pid, new_id, payload, None)
    with _Statements(db) as statements:
        _apply_new(db, pid, new_id, payload[1:], None)
    assert len(statements.deletes) == 1
    assert statements.updates, "later shapes must be renumbered"
    for sql in statements.updates:
        set_clause = sql.upper().split(" SET ", 1)[1].split(" WHERE ", 1)[0]
        assert "POINTS" not in set_clause, sql
        assert "SEQ" in set_clause, sql


def test_an_unchanged_set_issues_no_writes_at_all(client, alice, db):
    pid, (new_id, _) = _two_tasks(client, alice, db)
    rng = random.Random(12)
    payload = [_shape(i, rng) for i in range(40)]
    _apply_new(db, pid, new_id, payload, None)
    with _Statements(db) as statements:
        assert _apply_new(db, pid, new_id, json.loads(json.dumps(payload)), None) is False
    assert not (statements.updates or statements.deletes or statements.inserts)


def test_a_moved_vertex_is_one_update_setting_only_points(client, alice, db):
    pid, (new_id, _) = _two_tasks(client, alice, db)
    rng = random.Random(13)
    payload = [_shape(i, rng) for i in range(40)]
    _apply_new(db, pid, new_id, payload, None)
    payload[5]["points"][2]["x"] += 3.0
    with _Statements(db) as statements:
        assert _apply_new(db, pid, new_id, payload, None) is True
    assert len(statements.updates) == 1
    set_clause = statements.updates[0].upper().split(" SET ", 1)[1].split(" WHERE ", 1)[0]
    assert "POINTS" in set_clause and "SEQ" not in set_clause


# --- the shared read ------------------------------------------------------------

def test_load_stored_rows_returns_every_row_keyed_by_id(client, alice, db):
    pid, (new_id, _) = _two_tasks(client, alice, db)
    rng = random.Random(14)
    payload = [_shape(i, rng) for i in range(15)]
    _apply_new(db, pid, new_id, payload, None)
    stored = load_stored_rows(db, new_id)
    assert set(stored) == {s["id"] for s in payload}
    assert all(row[0] == ident for ident, row in stored.items())


def test_a_preloaded_read_gives_the_same_result_as_loading_inside(client, alice, db):
    pid, (new_id, ref_id) = _two_tasks(client, alice, db)
    rng = random.Random(15)
    payload = [_shape(i, rng) for i in range(20)]
    _apply_new(db, pid, new_id, payload, None)
    _apply_new(db, pid, ref_id, payload, None)
    payload[3]["points"][0]["y"] += 1.0
    del payload[10]

    task_a = db.get(models.Task, new_id)
    stored = load_stored_rows(db, new_id)
    assert sync_task_annotations_for_project(db, task_a, payload, stored=stored) is True
    db.commit()
    task_b = db.get(models.Task, ref_id)
    assert sync_task_annotations_for_project(db, task_b, payload) is True
    db.commit()
    assert [{k: v for k, v in r.items()} for r in _table(db, new_id)] == _table(db, ref_id)


def test_the_task_relationship_is_fresh_after_a_sync(client, alice, db):
    """Callers that read `task.annotation_rows` after a save must see the write."""
    pid, (new_id, _) = _two_tasks(client, alice, db)
    rng = random.Random(16)
    payload = [_shape(i, rng) for i in range(5)]
    task = db.get(models.Task, new_id)
    sync_task_annotations(db, task, payload, None)
    db.commit()
    assert len(task.annotation_rows) == 5            # loads now
    sync_task_annotations(db, task, payload[:2], None)
    db.commit()
    assert [r.id for r in task.annotation_rows] == ["s0", "s1"]

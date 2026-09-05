"""The save path stores annotations as rows.

Since the Phase C cutover the `annotations` table is authoritative and the
legacy blob column is no longer written. These tests pin what the save path
must do with a payload, and above all the property the whole normalisation
exists for -- that an edit writes only the rows it touched, not the entire
annotation set.
"""
import json

import pytest
from sqlalchemy import event

import models
from database import SessionLocal


@pytest.fixture
def db():
    """A session onto the same database the app under test writes to."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _project(client, auth, name="dual-write"):
    res = client.post("/api/projects", json={
        "name": name, "slug": name, "creator": "alice",
    }, headers=auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _create_task(client, auth, project_id):
    res = client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": "img.jpg", "status": "New", "client_id": CLIENT},
        headers=auth,
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


# One client id throughout: a client never conflicts with its own earlier save
# (CLAUDE.md rule 11), so these tests exercise the annotation write rather than
# conflict detection, which has its own suite.
CLIENT = "tab-dual-write"


def _save(client, auth, task_id, annotations, **extra):
    payload = {
        "id": task_id,
        "annotations": json.dumps(annotations),
        "client_id": CLIENT,
    }
    payload.update(extra)
    return client.post("/api/tasks", json=payload, headers=auth)


def _rows(db, task_id):
    return (
        db.query(models.Annotation)
        .filter(models.Annotation.task_id == task_id)
        .order_by(models.Annotation.id)
        .all()
    )


def test_save_writes_rows(client, alice, db):
    pid = _project(client, alice)
    tid = _create_task(client, alice, pid)

    anns = [
        {"id": "a1", "type": "polygon", "points": [{"x": 1, "y": 2}]},
        {"id": "a2", "type": "box", "x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0},
    ]
    assert _save(client, alice, tid, anns).status_code == 200

    rows = _rows(db, tid)
    assert [r.id for r in rows] == ["a1", "a2"]

    # And they read back as exactly what was sent: the wire format is unchanged
    # by the move to row storage.
    from formats.annotation_rows import rows_to_dicts
    assert rows_to_dicts(rows) == anns


def test_successive_edits_leave_exactly_what_was_sent(client, alice, db):
    """Add, edit and delete in sequence; the stored set tracks the payload."""
    from formats.annotation_rows import rows_to_dicts

    pid = _project(client, alice)
    tid = _create_task(client, alice, pid)

    for anns in (
        [{"id": "a1", "type": "polygon"}, {"id": "a2", "type": "box"}],
        [{"id": "a1", "type": "polygon", "color": "#fff"}],           # edit + delete
        [{"id": "a1", "type": "polygon", "color": "#fff"},
         {"id": "a3", "type": "box", "text": "new"}],                 # add
    ):
        assert _save(client, alice, tid, anns).status_code == 200
        db.expire_all()

        stored = {a["id"]: a for a in rows_to_dicts(_rows(db, tid))}
        assert stored == {a["id"]: a for a in anns}


def test_deleting_an_annotation_removes_its_row(client, alice, db):
    pid = _project(client, alice)
    tid = _create_task(client, alice, pid)

    _save(client, alice, tid, [{"id": "a1"}, {"id": "a2"}, {"id": "a3"}])
    _save(client, alice, tid, [{"id": "a2"}])
    db.expire_all()

    assert [r.id for r in _rows(db, tid)] == ["a2"]


def test_editing_one_shape_updates_only_that_row(client, alice, db):
    """The point of the whole change.

    The blob path rewrote every annotation on every save. If this regresses to
    assigning all columns on all rows, the UPDATE count grows with the size of
    the task instead of the size of the edit — which is the original bug.
    """
    pid = _project(client, alice)
    tid = _create_task(client, alice, pid)

    anns = [{"id": f"a{i}", "type": "box", "x": float(i)} for i in range(50)]
    assert _save(client, alice, tid, anns).status_code == 200

    statements = []

    def record(conn, cursor, statement, params, context, executemany):
        # `UPDATE annotations`, not `UPDATE tasks SET ... annotations=?` --
        # the blob write names the column too, and matching it would hide the
        # very thing under test.
        if statement.strip().upper().startswith("UPDATE ANNOTATIONS"):
            statements.append(statement)

    anns[7]["x"] = 999.0
    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert _save(client, alice, tid, anns).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", record)

    # One row changed, so exactly one UPDATE against `annotations` -- not 50.
    assert len(statements) == 1, f"expected a single-row update, got {statements}"

    db.expire_all()
    changed = [r for r in _rows(db, tid) if r.id == "a7"]
    assert changed[0].x == pytest.approx(999.0)


def test_unmodelled_fields_survive_the_round_trip(client, alice, db):
    from formats.annotation_rows import rows_to_dicts

    pid = _project(client, alice)
    tid = _create_task(client, alice, pid)

    anns = [{"id": "a1", "type": "rect", "label": "car", "visible": True,
             "source": "sam", "w": 5, "h": 5}]
    assert _save(client, alice, tid, anns).status_code == 200
    db.expire_all()

    assert rows_to_dicts(_rows(db, tid)) == anns


def test_annotation_naming_a_missing_label_does_not_fail_the_save(client, alice, db):
    """656 real annotations name labels that no longer exist.

    The FK would reject them; the save must still succeed, with the original id
    preserved rather than discarded (06_PROGRESS.md D5).
    """
    from formats.annotation_rows import rows_to_dicts

    pid = _project(client, alice)
    tid = _create_task(client, alice, pid)

    anns = [{"id": "a1", "type": "polygon", "labelId": "deleted-long-ago"}]
    assert _save(client, alice, tid, anns).status_code == 200
    db.expire_all()

    rows = _rows(db, tid)
    assert rows[0].label_id is None
    assert rows_to_dicts(rows) == anns


def test_deleting_a_task_deletes_its_annotation_rows(client, alice, db):
    pid = _project(client, alice)
    tid = _create_task(client, alice, pid)
    _save(client, alice, tid, [{"id": "a1"}, {"id": "a2"}])

    assert client.delete(f"/api/tasks/{tid}", headers=alice).status_code == 200
    db.expire_all()
    assert _rows(db, tid) == []


def test_duplicate_ids_in_one_payload_do_not_collide(client, alice, db):
    """(id, task_id) is the primary key, so a repeated id would violate it."""
    pid = _project(client, alice)
    tid = _create_task(client, alice, pid)

    res = _save(client, alice, tid,
                [{"id": "same", "type": "box"}, {"id": "same", "type": "polygon"}])
    assert res.status_code == 200
    db.expire_all()
    assert len(_rows(db, tid)) == 2


def test_same_id_on_two_tasks_is_allowed(client, alice, db):
    """678 real annotation ids appear on more than one task (copy-paste)."""
    pid = _project(client, alice)
    t1 = _create_task(client, alice, pid)
    t2 = _create_task(client, alice, pid)

    assert _save(client, alice, t1, [{"id": "shared", "type": "box"}]).status_code == 200
    assert _save(client, alice, t2, [{"id": "shared", "type": "box"}]).status_code == 200
    db.expire_all()

    assert len(_rows(db, t1)) == 1
    assert len(_rows(db, t2)) == 1

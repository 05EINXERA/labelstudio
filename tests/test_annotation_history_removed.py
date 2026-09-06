"""The annotation-history feature is gone, and the restore path writes rows.

`task_annotation_history` recorded the annotation set each save superseded. It
was removed because, after the normalisation, it was the last place still
serialising a task's entire annotation set on every save -- ~1,325 ms of
GIL-held CPU and a 22 MB row write on the largest production task, in 95% of
cases to preserve one changed shape. See .devnotes/remove-annotation-history/.

Two things need pinning after a removal like this:

  * that saves which *would* have written history -- edits and deletes -- still
    work. A half-removal leaving a dangling call would fail exactly there and
    nowhere else, so the ordinary-save tests would not catch it.
  * that the surviving recovery path actually recovers. It did not: the restore
    script wrote only `tasks.annotations`, the dead legacy column, so it
    reported success and changed nothing the application reads (01_ANALYSIS.md
    § 4). With history gone this script is the only restore path there is.
"""
import json

import pytest

import models
from database import SessionLocal


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


CLIENT = "tab-history-removed"


def _project(client, auth, name):
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


def _save(client, auth, task_id, annotations, **extra):
    payload = {
        "id": task_id,
        "annotations": json.dumps(annotations),
        "client_id": CLIENT,
    }
    payload.update(extra)
    return client.post("/api/tasks", json=payload, headers=auth)


def _annotations(n, prefix="obj"):
    return [
        {"id": f"{prefix}-{i}", "type": "box",
         "x": float(i), "y": 2.0, "width": 3.0, "height": 4.0}
        for i in range(n)
    ]


def test_the_history_model_is_gone():
    """The removal is complete, not just unwired.

    A leftover model would keep `Base.metadata.create_all` recreating the table
    on every fresh database, which is how a "removed" feature quietly comes
    back.
    """
    assert not hasattr(models, "TaskAnnotationHistory")
    assert "task_annotation_history" not in models.Base.metadata.tables


def test_an_overwriting_save_succeeds(client, alice, db):
    """The path that used to record history still works.

    An edit that changes an existing shape is what `_write_may_destroy` called
    destructive and what triggered the history write. It is the exact line a
    partial removal would break.
    """
    pid = _project(client, alice, "hist-removed-overwrite")
    tid = _create_task(client, alice, pid)

    assert _save(client, alice, tid, _annotations(3)).status_code == 200

    moved = _annotations(3)
    moved[1]["x"] = 999.0
    assert _save(client, alice, tid, moved).status_code == 200

    res = client.get(f"/api/tasks/{tid}", headers=alice)
    assert res.status_code == 200
    stored = {a["id"]: a for a in res.json()["annotations"]}
    assert stored["obj-1"]["x"] == 999.0


def test_a_deleting_save_succeeds(client, alice, db):
    """Deletes were the other history trigger, and are the wipe case."""
    pid = _project(client, alice, "hist-removed-delete")
    tid = _create_task(client, alice, pid)

    assert _save(client, alice, tid, _annotations(4)).status_code == 200
    assert _save(client, alice, tid, _annotations(1)).status_code == 200

    rows = db.query(models.Annotation).filter(
        models.Annotation.task_id == tid
    ).all()
    assert [r.id for r in rows] == ["obj-0"]


def test_restore_from_file_writes_annotation_rows(client, alice, db, tmp_path):
    """The surviving recovery path must reach the rows the application serves.

    This is the regression test for the bug in 01_ANALYSIS.md § 4: the script
    assigned `task.annotations` only, so a restore was a no-op against row
    storage while still printing RESTORED.
    """
    pid = _project(client, alice, "hist-removed-restore")
    tid = _create_task(client, alice, pid)

    # Work exists, then is wiped.
    original = _annotations(5)
    assert _save(client, alice, tid, original).status_code == 200
    assert _save(client, alice, tid, [], allow_clear=True).status_code == 200
    assert db.query(models.Annotation).filter(
        models.Annotation.task_id == tid
    ).count() == 0

    # Recovered payload, as extracted from a backup dump.
    payload = tmp_path / "recovered.json"
    payload.write_text(json.dumps(original), encoding="utf-8")

    import subprocess
    import sys
    import os

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # DATABASE_URL must be pinned to the test database explicitly.
    #
    # `config.py` loads the repo `.env` for every entry point (rule 12), and
    # this checkout's `.env` sets DATABASE_URL to the *dev Postgres* database.
    # DATABASE_URL is authoritative over DATA_DIR, so passing DATA_DIR alone is
    # not enough -- the subprocess would connect to annotation_dev and this test
    # would be writing to a real database instead of the temp one. Passing the
    # engine URL the tests are actually using closes that off.
    from database import SQLALCHEMY_DATABASE_URL

    env = dict(os.environ)
    env["DATABASE_URL"] = SQLALCHEMY_DATABASE_URL
    env["DATA_DIR"] = os.environ["DATA_DIR"]
    proc = subprocess.run(
        [sys.executable, "scripts/restore_task_annotations.py",
         "--task", str(tid), "--file", str(payload), "--commit",
         "--rollback-dir", str(tmp_path / "rollback")],
        cwd=repo, capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 0, f"{proc.stdout}\n{proc.stderr}"

    # The assertion that matters: the ROWS came back, not just the blob.
    db.expire_all()
    rows = (
        db.query(models.Annotation)
        .filter(models.Annotation.task_id == tid)
        .all()
    )
    assert sorted(r.id for r in rows) == sorted(a["id"] for a in original)

    # And the API serves them, which is what a restore is actually for.
    res = client.get(f"/api/tasks/{tid}", headers=alice)
    assert res.status_code == 200
    assert len(res.json()["annotations"]) == len(original)

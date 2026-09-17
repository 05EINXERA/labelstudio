"""The project status rollup runs only when it can actually change.

`_update_or_create_task_impl` derives `projects.status` from the spread of its
tasks' statuses. That aggregate used to run on *every* write to /api/tasks,
including plain annotation autosaves, which put a project-wide COUNT/SUM over
`tasks` on the hottest path in the app (~2,140 POST /api/tasks in one day's
access log, with tasks carrying up to 8,520 shapes each).

The rollup is now gated on a task status actually having changed, or on the
write being a create. These tests pin both halves: that the gating did not
break the rollup's real behaviour, and that an annotation-only save leaves the
project status alone.
"""
import json

from database import SessionLocal
import models


def _new_project(client, auth, name="rollup-proj"):
    res = client.post(
        "/api/projects",
        json={"name": name, "slug": name, "creator": "tester"},
        headers=auth,
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _new_task(client, auth, project_id, description="img.png", status="New"):
    res = client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": description, "status": status},
        headers=auth,
    )
    assert res.status_code == 200, res.text
    return res.json()


def _project_status(project_id):
    with SessionLocal() as db:
        return db.query(models.Project.status).filter(
            models.Project.id == project_id
        ).scalar()


def _set_project_status(project_id, status):
    with SessionLocal() as db:
        db.query(models.Project).filter(models.Project.id == project_id).update(
            {"status": status}
        )
        db.commit()


def test_completing_every_task_completes_the_project(client, alice):
    """The rollup still fires on a real status change."""
    pid = _new_project(client, alice)
    t1 = _new_task(client, alice, pid)
    t2 = _new_task(client, alice, pid)

    for task in (t1, t2):
        res = client.post(
            "/api/tasks",
            json={"id": task["id"], "status": "Completed",
                  "updated_at": task["updated_at"]},
            headers=alice,
        )
        assert res.status_code == 200, res.text

    assert _project_status(pid) == "Completed"


def test_partial_completion_moves_project_to_in_progress(client, alice):
    pid = _new_project(client, alice)
    t1 = _new_task(client, alice, pid)
    _new_task(client, alice, pid)

    res = client.post(
        "/api/tasks",
        json={"id": t1["id"], "status": "Completed",
              "updated_at": t1["updated_at"]},
        headers=alice,
    )
    assert res.status_code == 200, res.text

    assert _project_status(pid) == "In Progress"


def test_annotation_only_save_does_not_run_the_rollup(client, alice):
    """An autosave that changes no status must not touch the project.

    The project needs a *mix* of statuses for this to test anything: the rollup
    writes nothing at all when no task is 'Completed' (`new_status` stays None),
    so a project whose only task is 'New' would pass whether or not the gating
    works. With one of two tasks completed the rollup would compute
    'In Progress' and overwrite the forced 'Completed' below — so this fails if
    the rollup runs on an annotation-only save.
    """
    pid = _new_project(client, alice)
    task = _new_task(client, alice, pid)
    done = _new_task(client, alice, pid, description="done.png")

    res = client.post(
        "/api/tasks",
        json={"id": done["id"], "status": "Completed",
              "updated_at": done["updated_at"]},
        headers=alice,
    )
    assert res.status_code == 200, res.text

    _set_project_status(pid, "Completed")

    annotations = json.dumps([
        {"id": "ann-rollup-1", "type": "box", "x": 1, "y": 2,
         "width": 3, "height": 4},
    ])
    res = client.post(
        "/api/tasks",
        json={"id": task["id"], "annotations": annotations,
              "updated_at": task["updated_at"]},
        headers=alice,
    )
    assert res.status_code == 200, res.text

    assert _project_status(pid) == "Completed"


def test_creating_a_task_reopens_a_completed_project(client, alice):
    """A create shifts the status distribution even with no status change."""
    pid = _new_project(client, alice)
    task = _new_task(client, alice, pid)

    res = client.post(
        "/api/tasks",
        json={"id": task["id"], "status": "Completed",
              "updated_at": task["updated_at"]},
        headers=alice,
    )
    assert res.status_code == 200, res.text
    assert _project_status(pid) == "Completed"

    # The new task arrives as 'New', so the project is no longer fully done.
    _new_task(client, alice, pid, description="img2.png")

    assert _project_status(pid) == "In Progress"


def test_resending_the_same_status_does_not_run_the_rollup(client, alice):
    """Every autosave echoes the current status; that is not a change.

    Mirrors the `status_changed_to` guard already used for notifications.

    As above, the project carries a mix of statuses so the rollup has something
    to write; otherwise the test would pass vacuously.
    """
    pid = _new_project(client, alice)
    task = _new_task(client, alice, pid)
    done = _new_task(client, alice, pid, description="done.png")

    res = client.post(
        "/api/tasks",
        json={"id": done["id"], "status": "Completed",
              "updated_at": done["updated_at"]},
        headers=alice,
    )
    assert res.status_code == 200, res.text

    _set_project_status(pid, "Completed")

    refreshed = client.get(f"/api/tasks/{task['id']}", headers=alice).json()
    res = client.post(
        "/api/tasks",
        json={"id": task["id"], "status": "New",
              "updated_at": refreshed["updated_at"]},
        headers=alice,
    )
    assert res.status_code == 200, res.text

    assert _project_status(pid) == "Completed"

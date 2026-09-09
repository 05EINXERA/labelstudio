"""Moving tasks between projects (POST /api/tasks/move).

The contract under test is "all the data stays the same": the task row and its
annotations survive intact, and each annotation still means the class it meant
before — which, because labels are per-project rows, requires remapping
label_id onto the destination project's own class of the same name.
"""
import datetime

from conftest import unique_label_id
from database import SessionLocal
import models


def _new_project(client, auth, name="proj"):
    res = client.post("/api/projects", json={"name": name, "slug": name, "creator": "ignored"}, headers=auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _new_label(client, auth, pid, name, color="#ff0000"):
    lid = unique_label_id()
    res = client.post("/api/labels", json={"id": lid, "name": name, "color": color, "projectId": pid}, headers=auth)
    assert res.status_code == 200, res.text
    return lid


def _new_task(client, auth, pid, description="a.png"):
    res = client.post("/api/tasks", params={"projectId": pid}, json={"description": description}, headers=auth)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _save_annotations(client, auth, pid, tid, anns):
    """Write annotations through the real save path, as the canvas does."""
    import json
    res = client.patch(
        f"/api/tasks/{tid}?projectId={pid}",
        json={"annotations": json.dumps(anns)},
        headers=auth,
    )
    assert res.status_code == 200, res.text


def _box(ann_id, label_id, x=1.0, y=2.0, w=3.0, h=4.0):
    return {
        "id": ann_id, "labelId": label_id, "type": "rect",
        "x": x, "y": y, "width": w, "height": h,
    }


def _move(client, auth, task_ids, target_pid):
    return client.post(
        "/api/tasks/move",
        json={"taskIds": task_ids, "targetProjectId": target_pid},
        headers=auth,
    )


def test_move_preserves_task_fields_and_annotation_geometry(client, alice):
    src = _new_project(client, alice, "src")
    dst = _new_project(client, alice, "dst")
    lid = _new_label(client, alice, src, "Car")
    tid = _new_task(client, alice, src, "photo.png")

    client.patch(f"/api/tasks/{tid}?projectId={src}", json={"assignee": "Alice", "status": "In Progress"}, headers=alice)
    _save_annotations(client, alice, src, tid, [_box("a1", lid, 10.5, 20.5, 30.0, 40.0)])

    res = _move(client, alice, [tid], dst)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["moved"] == 1
    assert body["skipped"] == []

    detail = client.get(f"/api/tasks/{tid}", headers=alice).json()
    assert detail["assignee"] == "Alice"
    assert detail["status"] == "In Progress"
    assert detail["description"] == "photo.png"

    with SessionLocal() as db:
        # TaskDetail does not expose project_id, so the move itself is checked
        # against the row.
        assert db.query(models.Task).filter(models.Task.id == tid).one().project_id == dst
        anns = db.query(models.Annotation).filter(models.Annotation.task_id == tid).all()
        assert len(anns) == 1
        a = anns[0]
        assert (a.x, a.y, a.width, a.height) == (10.5, 20.5, 30.0, 40.0)
        assert a.type == "rect"


def test_move_reuses_destination_label_of_same_name(client, alice):
    src = _new_project(client, alice, "src")
    dst = _new_project(client, alice, "dst")
    src_lid = _new_label(client, alice, src, "Car", "#ff0000")
    dst_lid = _new_label(client, alice, dst, "Car", "#00ff00")
    tid = _new_task(client, alice, src)
    _save_annotations(client, alice, src, tid, [_box("a1", src_lid)])

    body = _move(client, alice, [tid], dst).json()
    assert body["moved"] == 1
    assert body["labelsCreated"] == 0
    assert body["labelsRemapped"] == 1

    with SessionLocal() as db:
        a = db.query(models.Annotation).filter(models.Annotation.task_id == tid).one()
        assert a.label_id == dst_lid
        # The destination gained no duplicate "Car".
        names = [l.name for l in db.query(models.Label).filter(models.Label.project_id == dst).all()]
        assert names == ["Car"]


def test_move_creates_missing_label_in_destination(client, alice):
    src = _new_project(client, alice, "src")
    dst = _new_project(client, alice, "dst")
    src_lid = _new_label(client, alice, src, "Pedestrian", "#123456")
    tid = _new_task(client, alice, src)
    _save_annotations(client, alice, src, tid, [_box("a1", src_lid)])

    body = _move(client, alice, [tid], dst).json()
    assert body["labelsCreated"] == 1
    assert body["labelsRemapped"] == 1

    with SessionLocal() as db:
        dest_labels = db.query(models.Label).filter(models.Label.project_id == dst).all()
        assert len(dest_labels) == 1
        assert dest_labels[0].name == "Pedestrian"
        assert dest_labels[0].color == "#123456"
        # The new row is distinct from the source's, which is untouched.
        assert dest_labels[0].id != src_lid
        a = db.query(models.Annotation).filter(models.Annotation.task_id == tid).one()
        assert a.label_id == dest_labels[0].id
        src_label = db.query(models.Label).filter(models.Label.id == src_lid).one()
        assert src_label.project_id == src


def test_moved_annotations_survive_deleting_the_source_project(client, alice):
    """The regression the remap exists to prevent.

    labels.id is a FK with ON DELETE SET NULL, so a moved annotation still
    pointing at a source label loses its class the moment the source project
    is deleted.
    """
    src = _new_project(client, alice, "src")
    dst = _new_project(client, alice, "dst")
    src_lid = _new_label(client, alice, src, "Car")
    tid = _new_task(client, alice, src)
    _save_annotations(client, alice, src, tid, [_box("a1", src_lid)])

    assert _move(client, alice, [tid], dst).json()["moved"] == 1
    assert client.delete(f"/api/projects/{src}", headers=alice).status_code == 200

    with SessionLocal() as db:
        a = db.query(models.Annotation).filter(models.Annotation.task_id == tid).one()
        assert a.label_id is not None
        label = db.query(models.Label).filter(models.Label.id == a.label_id).one()
        assert label.name == "Car"
        assert label.project_id == dst


def test_move_is_case_insensitive_on_class_name(client, alice):
    src = _new_project(client, alice, "src")
    dst = _new_project(client, alice, "dst")
    src_lid = _new_label(client, alice, src, "car")
    dst_lid = _new_label(client, alice, dst, "Car")
    tid = _new_task(client, alice, src)
    _save_annotations(client, alice, src, tid, [_box("a1", src_lid)])

    body = _move(client, alice, [tid], dst).json()
    assert body["labelsCreated"] == 0
    with SessionLocal() as db:
        a = db.query(models.Annotation).filter(models.Annotation.task_id == tid).one()
        assert a.label_id == dst_lid


def test_move_preserves_annotation_z_order(client, alice):
    src = _new_project(client, alice, "src")
    dst = _new_project(client, alice, "dst")
    lid = _new_label(client, alice, src, "Car")
    tid = _new_task(client, alice, src)
    _save_annotations(client, alice, src, tid, [
        _box("bottom", lid), _box("middle", lid), _box("top", lid),
    ])

    assert _move(client, alice, [tid], dst).json()["moved"] == 1

    detail = client.get(f"/api/tasks/{tid}", headers=alice).json()
    import json
    anns = detail["annotations"]
    if isinstance(anns, str):
        anns = json.loads(anns)
    assert [a["id"] for a in anns] == ["bottom", "middle", "top"]


def test_move_rejects_unowned_source_tasks(client, alice, bob):
    alice_src = _new_project(client, alice, "asrc")
    bob_src = _new_project(client, bob, "bsrc")
    alice_dst = _new_project(client, alice, "adst")
    a_tid = _new_task(client, alice, alice_src)
    b_tid = _new_task(client, bob, bob_src)

    body = _move(client, alice, [a_tid, b_tid], alice_dst).json()
    assert body["moved"] == 1
    assert body["skipped"] == [{"taskId": b_tid, "reason": "not_owned"}]

    # Bob's task never left his project.
    with SessionLocal() as db:
        assert db.query(models.Task).filter(models.Task.id == b_tid).one().project_id == bob_src


def test_move_to_unowned_destination_is_forbidden(client, alice, bob):
    src = _new_project(client, alice, "src")
    bob_dst = _new_project(client, bob, "bdst")
    tid = _new_task(client, alice, src)

    res = _move(client, alice, [tid], bob_dst)
    assert res.status_code in (403, 404)
    with SessionLocal() as db:
        assert db.query(models.Task).filter(models.Task.id == tid).one().project_id == src


def test_move_skips_a_locked_task(client, alice):
    src = _new_project(client, alice, "src")
    dst = _new_project(client, alice, "dst")
    tid = _new_task(client, alice, src)
    assert client.post(f"/api/tasks/{tid}/claim", params={"client_id": "tab-1"}, headers=alice).status_code == 200

    body = _move(client, alice, [tid], dst).json()
    assert body["moved"] == 0
    assert body["skipped"] == [{"taskId": tid, "reason": "locked"}]
    with SessionLocal() as db:
        assert db.query(models.Task).filter(models.Task.id == tid).one().project_id == src


def test_move_skips_task_already_in_target(client, alice):
    dst = _new_project(client, alice, "dst")
    tid = _new_task(client, alice, dst)

    body = _move(client, alice, [tid], dst).json()
    assert body["moved"] == 0
    assert body["skipped"] == [{"taskId": tid, "reason": "already_in_target"}]


def test_move_leaves_unlabelled_annotations_alone(client, alice):
    src = _new_project(client, alice, "src")
    dst = _new_project(client, alice, "dst")
    tid = _new_task(client, alice, src)
    _save_annotations(client, alice, src, tid, [_box("a1", None)])

    body = _move(client, alice, [tid], dst).json()
    assert body["moved"] == 1
    assert body["labelsCreated"] == 0
    assert body["labelsRemapped"] == 0
    with SessionLocal() as db:
        assert db.query(models.Annotation).filter(models.Annotation.task_id == tid).one().label_id is None

"""Annotation import (tracker P4.2, G5)."""
import io
import json

import models
from database import SessionLocal


def _new_project(client, auth, name="impp"):
    return client.post("/api/projects", json={"name": name, "slug": name, "creator": "ignored"}, headers=auth).json()["id"]


def _new_task(client, auth, pid, description):
    return client.post("/api/tasks", json={"description": description}, params={"projectId": pid}, headers=auth).json()["id"]


COCO_PAYLOAD = json.dumps({
    "images": [{"id": 1, "file_name": "cat.png", "width": 100, "height": 100}],
    "categories": [{"id": 1, "name": "cat"}],
    "annotations": [{"id": 1, "image_id": 1, "category_id": 1, "bbox": [10, 10, 20, 30], "segmentation": []}],
}).encode()

NATIVE_PAYLOAD = json.dumps([
    {
        "name": "dog.png",
        "annotations": [
            {"title": "dog", "points": [10, 10, 30, 10, 30, 40, 10, 40]},
        ],
    }
]).encode()


# --- preview -----------------------------------------------------------

def test_preview_reports_match_without_writing(client, alice):
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "cat.png")

    res = client.post(
        f"/api/imports/annotations/preview?projectId={pid}",
        files={"file": ("coco.json", COCO_PAYLOAD, "application/json")},
        headers=alice,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["matched"]) == 1
    assert body["matched"][0]["filename"] == "cat.png"
    assert body["new_labels"] == ["cat"]
    assert body["total_annotations"] == 1

    # nothing written
    tasks = client.get(f"/api/tasks?projectId={pid}", headers=alice).json()
    assert tasks[0]["annotations"] == []


def test_preview_reports_unmatched_filenames(client, alice):
    pid = _new_project(client, alice)
    # no task named cat.png exists
    res = client.post(
        f"/api/imports/annotations/preview?projectId={pid}",
        files={"file": ("coco.json", COCO_PAYLOAD, "application/json")},
        headers=alice,
    )
    body = res.json()
    assert body["matched"] == []
    assert body["unmatched"][0]["filename"] == "cat.png"


def test_preview_requires_ownership(client, alice, bob):
    pid = _new_project(client, alice)
    res = client.post(
        f"/api/imports/annotations/preview?projectId={pid}",
        files={"file": ("coco.json", COCO_PAYLOAD, "application/json")},
        headers=bob,
    )
    assert res.status_code == 404


# --- apply: COCO ---------------------------------------------------------

def test_import_coco_matches_by_filename_and_creates_label(client, alice):
    pid = _new_project(client, alice)
    tid = _new_task(client, alice, pid, "cat.png")

    res = client.post(
        f"/api/imports/annotations?projectId={pid}",
        files={"file": ("coco.json", COCO_PAYLOAD, "application/json")},
        headers=alice,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["tasks_updated"] == 1
    assert body["annotations_imported"] == 1
    assert body["unmatched"] == []

    task = next(t for t in client.get(f"/api/tasks?projectId={pid}", headers=alice).json() if t["id"] == tid)
    assert len(task["annotations"]) == 1
    assert task["annotations"][0]["x"] == 10
    assert task["annotations"][0]["width"] == 20

    labels = client.get(f"/api/labels?projectId={pid}", headers=alice).json()
    assert labels[0]["name"] == "cat"


def test_import_native_format_with_flat_point_list(client, alice):
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "dog.png")

    res = client.post(
        f"/api/imports/annotations?projectId={pid}",
        files={"file": ("export.json", NATIVE_PAYLOAD, "application/json")},
        headers=alice,
    )
    body = res.json()
    assert body["tasks_updated"] == 1
    labels = client.get(f"/api/labels?projectId={pid}", headers=alice).json()
    assert labels[0]["name"] == "dog"


def test_import_merge_appends_to_existing_annotations(client, alice):
    pid = _new_project(client, alice)
    tid = _new_task(client, alice, pid, "cat.png")
    client.patch(f"/api/tasks/{tid}", json={"annotations": json.dumps([{"id": "pre-existing", "labelId": "x"}])}, headers=alice)

    client.post(
        f"/api/imports/annotations?projectId={pid}&mode=merge",
        files={"file": ("coco.json", COCO_PAYLOAD, "application/json")},
        headers=alice,
    )
    task = next(t for t in client.get(f"/api/tasks?projectId={pid}", headers=alice).json() if t["id"] == tid)
    assert len(task["annotations"]) == 2
    assert any(a.get("id") == "pre-existing" for a in task["annotations"])


def test_import_replace_overwrites_existing_annotations(client, alice):
    pid = _new_project(client, alice)
    tid = _new_task(client, alice, pid, "cat.png")
    client.patch(f"/api/tasks/{tid}", json={"annotations": json.dumps([{"id": "pre-existing", "labelId": "x"}])}, headers=alice)

    client.post(
        f"/api/imports/annotations?projectId={pid}&mode=replace",
        files={"file": ("coco.json", COCO_PAYLOAD, "application/json")},
        headers=alice,
    )
    task = next(t for t in client.get(f"/api/tasks?projectId={pid}", headers=alice).json() if t["id"] == tid)
    assert len(task["annotations"]) == 1
    assert not any(a.get("id") == "pre-existing" for a in task["annotations"])


def test_import_reuses_existing_label_case_insensitively(client, alice):
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "cat.png")
    client.post("/api/labels", json={"id": "existing", "name": "Cat", "color": "#000", "projectId": pid}, headers=alice)

    client.post(
        f"/api/imports/annotations?projectId={pid}",
        files={"file": ("coco.json", COCO_PAYLOAD, "application/json")},
        headers=alice,
    )
    labels = client.get(f"/api/labels?projectId={pid}", headers=alice).json()
    assert len(labels) == 1  # no duplicate "cat" created


def test_import_does_not_create_new_tasks(client, alice):
    """Import matches existing tasks only; it cannot add new images."""
    pid = _new_project(client, alice)
    client.post(
        f"/api/imports/annotations?projectId={pid}",
        files={"file": ("coco.json", COCO_PAYLOAD, "application/json")},
        headers=alice,
    )
    tasks = client.get(f"/api/tasks?projectId={pid}", headers=alice).json()
    assert tasks == []


def test_import_invalid_json_returns_422(client, alice):
    pid = _new_project(client, alice)
    res = client.post(
        f"/api/imports/annotations?projectId={pid}",
        files={"file": ("bad.json", b"{not json", "application/json")},
        headers=alice,
    )
    assert res.status_code == 422


def test_import_requires_ownership(client, alice, bob):
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "cat.png")
    res = client.post(
        f"/api/imports/annotations?projectId={pid}",
        files={"file": ("coco.json", COCO_PAYLOAD, "application/json")},
        headers=bob,
    )
    assert res.status_code == 404

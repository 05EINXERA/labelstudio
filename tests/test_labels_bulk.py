"""Label bulk / import / export (tracker P3.3, G4).

Label.id is a global primary key (not scoped to project_id), so every test
below mints unique ids rather than reusing short literals like "a"/"b" across
projects in the same session.
"""
import io
import itertools
import json

import pytest

_id_seq = itertools.count()


def _lid():
    return f"lbl-{next(_id_seq)}"


def _new_project(client, auth, name="labp"):
    res = client.post("/api/projects", json={"name": name, "slug": name, "creator": "ignored"}, headers=auth)
    return res.json()["id"]


def _label(id_, name, color="#fff", pid=None):
    return {"id": id_, "name": name, "color": color, "projectId": pid}


# --- bulk upsert / delete ---------------------------------------------------

def test_bulk_upsert_creates_and_updates(client, alice):
    pid = _new_project(client, alice)
    id_a, id_b, id_c = _lid(), _lid(), _lid()
    res = client.post("/api/labels/bulk", json={
        "projectId": pid,
        "labels": [_label(id_a, "cat", "#111", pid), _label(id_b, "dog", "#222", pid)],
    }, headers=alice)
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "ok", "created": 2, "updated": 0}

    res = client.post("/api/labels/bulk", json={
        "projectId": pid,
        "labels": [_label(id_a, "cat", "#999", pid), _label(id_c, "bird", "#333", pid)],
    }, headers=alice)
    assert res.json() == {"status": "ok", "created": 1, "updated": 1}

    labels = {l["id"]: l for l in client.get(f"/api/labels?projectId={pid}", headers=alice).json()}
    assert labels[id_a]["color"] == "#999"
    assert len(labels) == 3


def test_bulk_upsert_rejects_mismatched_project_id(client, alice):
    pid = _new_project(client, alice)
    other_pid = _new_project(client, alice, "other")
    res = client.post("/api/labels/bulk", json={
        "projectId": pid,
        "labels": [_label(_lid(), "cat", "#111", other_pid)],
    }, headers=alice)
    assert res.status_code == 422


def test_bulk_upsert_requires_ownership(client, alice, bob):
    pid = _new_project(client, alice)
    res = client.post("/api/labels/bulk", json={
        "projectId": pid,
        "labels": [_label(_lid(), "cat", "#111", pid)],
    }, headers=bob)
    assert res.status_code == 404


def test_bulk_delete(client, alice):
    pid = _new_project(client, alice)
    id_a, id_b = _lid(), _lid()
    client.post("/api/labels/bulk", json={
        "projectId": pid,
        "labels": [_label(id_a, "cat", "#111", pid), _label(id_b, "dog", "#222", pid)],
    }, headers=alice)
    res = client.post("/api/labels/bulk-delete", json={"projectId": pid, "ids": [id_a]}, headers=alice)
    assert res.json() == {"status": "ok", "deleted": 1, "annotationsDeleted": 0}
    remaining = client.get(f"/api/labels?projectId={pid}", headers=alice).json()
    assert [l["id"] for l in remaining] == [id_b]


def test_bulk_delete_requires_ownership(client, alice, bob):
    pid = _new_project(client, alice)
    res = client.post("/api/labels/bulk-delete", json={"projectId": pid, "ids": [_lid()]}, headers=bob)
    assert res.status_code == 404


# --- deleting a class cascades to its annotations -----------------------------

def _new_task(client, auth, pid, description, annotations):
    tid = client.post("/api/tasks", json={"description": description}, params={"projectId": pid}, headers=auth).json()["id"]
    client.patch(f"/api/tasks/{tid}", json={"annotations": json.dumps(annotations)}, headers=auth)
    return tid


def _annotations_of(client, auth, pid, tid):
    tasks = client.get("/api/tasks", params={"projectId": pid}, headers=auth).json()
    task = next(t for t in tasks if t["id"] == tid)
    anns = task["annotations"]
    return json.loads(anns) if isinstance(anns, str) else (anns or [])


def test_delete_label_deletes_its_annotations(client, alice):
    pid = _new_project(client, alice)
    id_a, id_b = _lid(), _lid()
    client.post("/api/labels", json=_label(id_a, "cat", "#111", pid), headers=alice)
    client.post("/api/labels", json=_label(id_b, "dog", "#222", pid), headers=alice)
    tid = _new_task(client, alice, pid, "a.png", [
        {"id": "1", "type": "bbox", "labelId": id_a},
        {"id": "2", "type": "bbox", "labelId": id_b},
        {"id": "3", "type": "comment", "text": "keep me"},
    ])

    res = client.delete(f"/api/labels/{id_a}?projectId={pid}", headers=alice)
    assert res.json() == {"status": "ok", "annotationsDeleted": 1}

    remaining = _annotations_of(client, alice, pid, tid)
    assert [a["id"] for a in remaining] == ["2", "3"]


def test_bulk_delete_cascades_across_tasks(client, alice):
    pid = _new_project(client, alice)
    id_a, id_b, id_c = _lid(), _lid(), _lid()
    for lid, name in ((id_a, "cat"), (id_b, "dog"), (id_c, "bird")):
        client.post("/api/labels", json=_label(lid, name, "#111", pid), headers=alice)
    t1 = _new_task(client, alice, pid, "a.png", [
        {"id": "1", "type": "bbox", "labelId": id_a},
        {"id": "2", "type": "polygon", "labelId": id_c},
    ])
    t2 = _new_task(client, alice, pid, "b.png", [
        {"id": "3", "type": "bbox", "labelId": id_b},
    ])

    res = client.post("/api/labels/bulk-delete", json={"projectId": pid, "ids": [id_a, id_b]}, headers=alice)
    assert res.json() == {"status": "ok", "deleted": 2, "annotationsDeleted": 2}

    assert [a["id"] for a in _annotations_of(client, alice, pid, t1)] == ["2"]
    assert _annotations_of(client, alice, pid, t2) == []


def test_delete_label_leaves_other_projects_untouched(client, alice):
    pid = _new_project(client, alice)
    other = _new_project(client, alice, "other")
    lid = _lid()
    client.post("/api/labels", json=_label(lid, "cat", "#111", pid), headers=alice)
    # Label ids are globally unique, but an annotation in another project could
    # still reference one; the purge must stay scoped to the owning project.
    other_tid = _new_task(client, alice, other, "z.png", [{"id": "9", "type": "bbox", "labelId": lid}])

    client.delete(f"/api/labels/{lid}?projectId={pid}", headers=alice)

    assert [a["id"] for a in _annotations_of(client, alice, other, other_tid)] == ["9"]


def test_import_replace_deletes_orphaned_annotations(client, alice):
    pid = _new_project(client, alice)
    lid = _lid()
    client.post("/api/labels", json=_label(lid, "obsolete", "#000", pid), headers=alice)
    tid = _new_task(client, alice, pid, "a.png", [{"id": "1", "type": "bbox", "labelId": lid}])

    client.post(
        f"/api/labels/import?projectId={pid}&mode=replace",
        files={"file": ("classes.txt", io.BytesIO(b"cat\n"), "text/plain")},
        headers=alice,
    )

    assert _annotations_of(client, alice, pid, tid) == []


# --- export ------------------------------------------------------------------

def test_export_json(client, alice):
    pid = _new_project(client, alice)
    lid = _lid()
    client.post("/api/labels", json=_label(lid, "cat", "#111", pid), headers=alice)
    res = client.get(f"/api/labels/export?projectId={pid}&format=json", headers=alice)
    assert res.status_code == 200
    assert res.json() == [{"id": lid, "name": "cat", "color": "#111"}]


def test_export_csv(client, alice):
    pid = _new_project(client, alice)
    lid = _lid()
    client.post("/api/labels", json=_label(lid, "cat", "#111", pid), headers=alice)
    res = client.get(f"/api/labels/export?projectId={pid}&format=csv", headers=alice)
    assert "id,name,color" in res.text
    assert f"{lid},cat,#111" in res.text


def test_export_txt(client, alice):
    pid = _new_project(client, alice)
    client.post("/api/labels", json=_label(_lid(), "cat", "#111", pid), headers=alice)
    client.post("/api/labels", json=_label(_lid(), "dog", "#222", pid), headers=alice)
    res = client.get(f"/api/labels/export?projectId={pid}&format=txt", headers=alice)
    assert res.text.splitlines() == ["cat", "dog"]


def test_export_requires_ownership(client, alice, bob):
    pid = _new_project(client, alice)
    res = client.get(f"/api/labels/export?projectId={pid}&format=json", headers=bob)
    assert res.status_code == 404


# --- import ------------------------------------------------------------------

def test_import_txt(client, alice):
    pid = _new_project(client, alice)
    f = io.BytesIO(b"cat\ndog\n\nbird\n")
    res = client.post(
        f"/api/labels/import?projectId={pid}",
        files={"file": ("classes.txt", f, "text/plain")},
        headers=alice,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["created"] == 3
    assert {l["name"] for l in body["labels"]} == {"cat", "dog", "bird"}


def test_import_json_array_of_strings(client, alice):
    pid = _new_project(client, alice)
    f = io.BytesIO(b'["cat", "dog"]')
    res = client.post(
        f"/api/labels/import?projectId={pid}",
        files={"file": ("classes.json", f, "application/json")},
        headers=alice,
    )
    assert res.json()["created"] == 2


def test_import_json_object_with_labels_key(client, alice):
    pid = _new_project(client, alice)
    f = io.BytesIO(b'{"labels": [{"name": "cat", "color": "#abc"}]}')
    res = client.post(
        f"/api/labels/import?projectId={pid}",
        files={"file": ("classes.json", f, "application/json")},
        headers=alice,
    )
    body = res.json()
    assert body["created"] == 1
    assert body["labels"][0]["color"] == "#abc"


def test_import_csv_with_header(client, alice):
    pid = _new_project(client, alice)
    f = io.BytesIO(b"name,color\ncat,#111\ndog,#222\n")
    res = client.post(
        f"/api/labels/import?projectId={pid}",
        files={"file": ("classes.csv", f, "text/csv")},
        headers=alice,
    )
    body = res.json()
    assert body["created"] == 2
    colors = {l["name"]: l["color"] for l in body["labels"]}
    assert colors == {"cat": "#111", "dog": "#222"}


def test_import_merge_is_case_insensitive_and_idempotent(client, alice):
    pid = _new_project(client, alice)
    existing_id = _lid()
    client.post("/api/labels", json=_label(existing_id, "Cat", "#000", pid), headers=alice)

    f = io.BytesIO(b"cat\ndog\n")  # lowercase "cat" should match existing "Cat"
    res = client.post(
        f"/api/labels/import?projectId={pid}&mode=merge",
        files={"file": ("classes.txt", f, "text/plain")},
        headers=alice,
    )
    body = res.json()
    assert body["updated"] == 1  # matched existing "Cat"
    assert body["created"] == 1  # new "dog"
    assert len(body["labels"]) == 2
    # the pre-existing row's id survived the merge rather than being duplicated
    assert any(l["id"] == existing_id for l in body["labels"])


def test_import_duplicate_names_within_file_are_deduped(client, alice):
    pid = _new_project(client, alice)
    f = io.BytesIO(b"cat\nCat\nCAT\n")
    res = client.post(
        f"/api/labels/import?projectId={pid}",
        files={"file": ("classes.txt", f, "text/plain")},
        headers=alice,
    )
    body = res.json()
    assert body["created"] == 1
    assert body["skipped"] == 2


def test_import_replace_deletes_existing_first(client, alice):
    pid = _new_project(client, alice)
    client.post("/api/labels", json=_label(_lid(), "obsolete", "#000", pid), headers=alice)

    f = io.BytesIO(b"cat\n")
    res = client.post(
        f"/api/labels/import?projectId={pid}&mode=replace",
        files={"file": ("classes.txt", f, "text/plain")},
        headers=alice,
    )
    body = res.json()
    assert [l["name"] for l in body["labels"]] == ["cat"]

    remaining = client.get(f"/api/labels?projectId={pid}", headers=alice).json()
    assert len(remaining) == 1


def test_import_invalid_json_returns_422_not_500(client, alice):
    pid = _new_project(client, alice)
    f = io.BytesIO(b"{not valid json")
    res = client.post(
        f"/api/labels/import?projectId={pid}",
        files={"file": ("classes.json", f, "application/json")},
        headers=alice,
    )
    assert res.status_code == 422


def test_import_empty_file_returns_422(client, alice):
    pid = _new_project(client, alice)
    f = io.BytesIO(b"   \n\n  ")
    res = client.post(
        f"/api/labels/import?projectId={pid}",
        files={"file": ("classes.txt", f, "text/plain")},
        headers=alice,
    )
    assert res.status_code == 422


def test_import_requires_ownership(client, alice, bob):
    pid = _new_project(client, alice)
    f = io.BytesIO(b"cat\n")
    res = client.post(
        f"/api/labels/import?projectId={pid}",
        files={"file": ("classes.txt", f, "text/plain")},
        headers=bob,
    )
    assert res.status_code == 404


def test_export_then_reimport_round_trips(client, alice):
    """The story's requirement: export a class set, import it elsewhere."""
    pid1 = _new_project(client, alice, "source")
    pid2 = _new_project(client, alice, "dest")
    client.post("/api/labels", json=_label(_lid(), "cat", "#111", pid1), headers=alice)
    client.post("/api/labels", json=_label(_lid(), "dog", "#222", pid1), headers=alice)

    exported = client.get(f"/api/labels/export?projectId={pid1}&format=json", headers=alice).content

    res = client.post(
        f"/api/labels/import?projectId={pid2}",
        files={"file": ("classes.json", io.BytesIO(exported), "application/json")},
        headers=alice,
    )
    body = res.json()
    assert body["created"] == 2
    assert {l["name"] for l in body["labels"]} == {"cat", "dog"}
    # colors carried over, not re-randomized
    assert {l["color"] for l in body["labels"]} == {"#111", "#222"}

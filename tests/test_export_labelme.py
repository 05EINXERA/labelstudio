"""LabelMe annotation export, end to end through the export job.

Unit-level build coverage is in test_labelme_format.py; this file covers the
wiring — that the format is accepted, lands under its own folder, and always
ZIPs (it is multi-file, so the single-file download carve-out must not claim it).
"""
import io
import json
import zipfile

from formats import labelme


def _new_project(client, auth, name="lmexp"):
    return client.post("/api/projects", json={"name": name, "slug": name, "creator": "x"},
                       headers=auth).json()["id"]


def _new_label(client, auth, pid, lid, name, color="#ef4444"):
    client.post("/api/labels", json={"id": lid, "name": name, "color": color,
                                     "projectId": pid}, headers=auth)
    return lid


def _new_task(client, auth, pid, description, annotations=None):
    # Annotations go in the create payload, as in test_coco_format.py. Setting
    # them with a follow-up PATCH does not persist here — see the two
    # pre-existing failures in test_exports.py that use that pattern.
    payload = {"description": description, "status": "New"}
    if annotations is not None:
        payload["annotations"] = json.dumps(annotations)
    res = client.post("/api/tasks", json=payload, params={"projectId": pid}, headers=auth)
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


def _run_export(client, auth, **kwargs):
    res = client.post("/api/exports", json=kwargs, headers=auth)
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    status = client.get(f"/api/exports/{job_id}", headers=auth).json()
    assert status["status"] == "completed", status
    return job_id, status


def _download_zip(client, auth, job_id):
    res = client.get(f"/api/exports/{job_id}/download", headers=auth)
    assert res.status_code == 200
    return zipfile.ZipFile(io.BytesIO(res.content))


_POLY = [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 6, "y": 9}]
_BOX = [{"x": 1, "y": 2}, {"x": 11, "y": 2}, {"x": 11, "y": 8}, {"x": 1, "y": 8}]


def test_export_writes_one_json_per_task_under_its_own_folder(client, alice):
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "lm-exp-1", "Rust Area")
    _new_task(client, alice, pid, "a.png",
              [{"id": "ann1", "labelId": lid, "type": "polygon", "points": _POLY}])
    _new_task(client, alice, pid, "b.png",
              [{"id": "ann2", "labelId": lid, "type": "polygon", "points": _POLY}])

    job_id, status = _run_export(client, alice, projectId=pid, format="labelme")
    assert status["task_count"] == 2

    with _download_zip(client, alice, job_id) as zf:
        assert sorted(zf.namelist()) == ["labelme/a.json", "labelme/b.json"]


def test_exported_document_reimports(client, alice):
    """The round trip is the point of the format: what we write must satisfy
    our own detection and parse back to the same geometry."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "lm-exp-2", "Rust Area")
    _new_task(client, alice, pid, "a.png",
              [{"id": "ann1", "labelId": lid, "type": "polygon", "points": _POLY}])

    job_id, _ = _run_export(client, alice, projectId=pid, format="labelme")
    with _download_zip(client, alice, job_id) as zf:
        doc = json.loads(zf.read("labelme/a.json").decode("utf-8"))

    assert labelme.looks_like(doc) is True
    assert doc["imagePath"] == "a.png"
    parsed, notes = labelme.parse(doc)
    assert notes == []
    ann = parsed["a.png"][0]
    assert ann["labelName"] == "Rust Area"
    assert ann["points"] == _POLY


def test_box_exports_as_a_rectangle(client, alice):
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "lm-exp-3", "Rust Area")
    _new_task(client, alice, pid, "a.png",
              [{"id": "ann1", "labelId": lid, "type": "bbox", "points": _BOX}])

    job_id, _ = _run_export(client, alice, projectId=pid, format="labelme")
    with _download_zip(client, alice, job_id) as zf:
        doc = json.loads(zf.read("labelme/a.json").decode("utf-8"))

    shape = doc["shapes"][0]
    assert shape["shape_type"] == "rectangle"
    assert shape["points"] == [[1, 2], [11, 8]]


def test_non_ascii_labels_are_written_literally(client, alice):
    """Escaping to \\uXXXX is valid JSON no human can read, and diverges from
    what LabelMe itself writes."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "lm-exp-4", "健全箇所")
    _new_task(client, alice, pid, "a.png",
              [{"id": "ann1", "labelId": lid, "type": "polygon", "points": _POLY}])

    job_id, _ = _run_export(client, alice, projectId=pid, format="labelme")
    with _download_zip(client, alice, job_id) as zf:
        raw = zf.read("labelme/a.json")

    assert "健全箇所".encode("utf-8") in raw
    assert b"\\u" not in raw


def test_task_without_image_dimensions_is_not_skipped(client, alice):
    """Unlike YOLO — absolute coordinates need no image size, so the task
    exports with null dimensions rather than being dropped."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "lm-exp-5", "Rust Area")
    _new_task(client, alice, pid, "a.png",
              [{"id": "ann1", "labelId": lid, "type": "polygon", "points": _POLY}])

    job_id, status = _run_export(client, alice, projectId=pid, format="labelme")
    assert not status.get("skipped")

    with _download_zip(client, alice, job_id) as zf:
        doc = json.loads(zf.read("labelme/a.json").decode("utf-8"))
    assert doc["imageHeight"] is None
    assert doc["imageWidth"] is None


def test_export_is_always_a_zip_even_with_no_images(client, alice):
    """Multi-file, so the single-file bare-download carve-out (coco and
    annotations_json) must not claim it."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "a.png")

    job_id, _ = _run_export(client, alice, projectId=pid, format="labelme",
                            imageOutput="none")
    res = client.get(f"/api/exports/{job_id}/download", headers=alice)
    assert res.content[:4] == b"PK\x03\x04"


def test_labelme_pairs_with_the_original_image_output(client, alice):
    """The combination that produces a dataset LabelMe opens directly: each
    axis writes into its own folder, so the two never collide."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "a.png")

    job_id, _ = _run_export(client, alice, projectId=pid, format="labelme",
                            imageOutput="original")
    with _download_zip(client, alice, job_id) as zf:
        assert any(n.startswith("labelme/") for n in zf.namelist())

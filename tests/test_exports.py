"""Annotation export (tracker P4.4, G6)."""
import json


def _new_project(client, auth, name="expp"):
    return client.post("/api/projects", json={"name": name, "slug": name, "creator": "ignored"}, headers=auth).json()["id"]


def _new_task(client, auth, pid, description, status=None, annotations=None):
    payload = {"description": description}
    if status:
        payload["status"] = status
    tid = client.post("/api/tasks", json=payload, params={"projectId": pid}, headers=auth).json()["id"]
    if annotations is not None:
        client.patch(f"/api/tasks/{tid}", json={"annotations": json.dumps(annotations)}, headers=auth)
    return tid


def _run_export(client, auth, **kwargs):
    """Create a job and poll it to completion. TestClient runs BackgroundTasks
    synchronously before the response returns, so a single status check after
    create_export suffices; this still goes through the real polling contract.
    """
    res = client.post("/api/exports", json=kwargs, headers=auth)
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    status = client.get(f"/api/exports/{job_id}", headers=auth).json()
    assert status["status"] == "completed", status
    return job_id, status


def test_export_json_contains_task_and_label(client, alice):
    pid = _new_project(client, alice)
    lid = "lbl-exp-1"
    client.post("/api/labels", json={"id": lid, "name": "cat", "color": "#111", "projectId": pid}, headers=alice)
    _new_task(client, alice, pid, "a.png", annotations=[
        {"id": "ann1", "labelId": lid, "x": 10, "y": 10, "width": 20, "height": 30,
         "points": [{"x": 10, "y": 10}, {"x": 30, "y": 10}, {"x": 30, "y": 40}, {"x": 10, "y": 40}]}
    ])

    job_id, status = _run_export(client, alice, projectId=pid, format="json")
    assert status["task_count"] == 1

    res = client.get(f"/api/exports/{job_id}/download", headers=alice)
    assert res.status_code == 200
    body = res.json()
    assert body["images"][0]["file_name"] == "a.png"
    assert body["categories"][0]["name"] == "cat"
    assert body["annotations"][0]["bbox"] == [10, 10, 20, 30]


def test_export_csv(client, alice):
    pid = _new_project(client, alice)
    lid = "lbl-exp-2"
    client.post("/api/labels", json={"id": lid, "name": "dog", "color": "#111", "projectId": pid}, headers=alice)
    _new_task(client, alice, pid, "b.png", annotations=[
        {"id": "ann1", "labelId": lid, "x": 5, "y": 5, "width": 10, "height": 10}
    ])

    job_id, status = _run_export(client, alice, projectId=pid, format="csv")
    res = client.get(f"/api/exports/{job_id}/download", headers=alice)
    assert "image,label,x,y,width,height,status" in res.text
    assert "b.png,dog,5,5,10,10,New" in res.text


def test_export_status_filter(client, alice):
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "new.png", status="New")
    _new_task(client, alice, pid, "done.png", status="Completed")

    job_id, status = _run_export(client, alice, projectId=pid, format="json", statusFilter=["Completed"])
    assert status["task_count"] == 1
    res = client.get(f"/api/exports/{job_id}/download", headers=alice)
    assert res.json()["images"][0]["file_name"] == "done.png"


def test_export_no_filter_includes_all_statuses(client, alice):
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "new.png", status="New")
    _new_task(client, alice, pid, "done.png", status="Completed")

    job_id, status = _run_export(client, alice, projectId=pid, format="json")
    assert status["task_count"] == 2


def test_export_approved_status_filter(client, alice):
    pid = _new_project(client, alice)
    tid = _new_task(client, alice, pid, "a.png")
    client.patch(f"/api/tasks/{tid}", json={"status": "Approved"}, headers=alice)
    _new_task(client, alice, pid, "b.png", status="New")

    job_id, status = _run_export(client, alice, projectId=pid, format="json", statusFilter=["Approved"])
    assert status["task_count"] == 1


def test_export_rejects_unknown_status_value(client, alice):
    pid = _new_project(client, alice)
    res = client.post("/api/exports", json={"projectId": pid, "format": "json", "statusFilter": ["Bogus"]}, headers=alice)
    assert res.status_code == 422


def test_export_rejects_unimplemented_format(client, alice):
    pid = _new_project(client, alice)
    res = client.post("/api/exports", json={"projectId": pid, "format": "yolo"}, headers=alice)
    assert res.status_code == 422


def test_export_rejects_unimplemented_include_option(client, alice):
    """Masks are an explicit TODO — must reject, not silently ignore."""
    pid = _new_project(client, alice)
    res = client.post("/api/exports", json={"projectId": pid, "format": "json", "include": "with_mask_binary"}, headers=alice)
    assert res.status_code == 422
    assert "not implemented" in res.json()["detail"]


def test_export_requires_ownership(client, alice, bob):
    pid = _new_project(client, alice)
    res = client.post("/api/exports", json={"projectId": pid, "format": "json"}, headers=bob)
    assert res.status_code == 404


def test_download_is_one_shot(client, alice):
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "a.png")
    job_id, _ = _run_export(client, alice, projectId=pid, format="json")

    first = client.get(f"/api/exports/{job_id}/download", headers=alice)
    assert first.status_code == 200
    second = client.get(f"/api/exports/{job_id}/download", headers=alice)
    assert second.status_code == 404


def test_download_before_ready_returns_404(client, alice):
    res = client.get("/api/exports/nonexistent-job-id/download", headers=alice)
    assert res.status_code == 404


def test_status_of_unknown_job_returns_404(client, alice):
    res = client.get("/api/exports/nonexistent-job-id", headers=alice)
    assert res.status_code == 404


def test_export_skips_annotations_for_deleted_labels(client, alice):
    """An annotation whose labelId no longer resolves must not crash the export."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "a.png", annotations=[
        {"id": "ann1", "labelId": "does-not-exist", "x": 1, "y": 1, "width": 1, "height": 1}
    ])
    job_id, status = _run_export(client, alice, projectId=pid, format="json")
    res = client.get(f"/api/exports/{job_id}/download", headers=alice)
    assert res.json()["annotations"] == []

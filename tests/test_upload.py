"""Bulk image upload (tracker P3.1).

The upload endpoint used to abort the whole batch on the first bad file,
leaving earlier files on disk with no task row. It now reports each file
individually and never leaves partial/oversized files behind.
"""
import os

import models
from database import SessionLocal

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def _new_project(client, auth):
    res = client.post("/api/projects", json={"name": "up", "slug": "up", "creator": "ignored"}, headers=auth)
    return res.json()["id"]


def test_mixed_batch_reports_per_file_and_keeps_valid_ones(client, alice):
    pid = _new_project(client, alice)
    res = client.post(
        f"/api/projects/{pid}/upload",
        files=[
            ("file", ("good.png", PNG_BYTES, "image/png")),
            ("file", ("bad.exe", b"MZ...", "application/octet-stream")),
            ("file", ("good2.jpg", PNG_BYTES, "image/jpeg")),
        ],
        headers=alice,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["uploaded"]) == 2
    assert len(body["failed"]) == 1
    assert body["failed"][0]["filename"] == "bad.exe"

    tasks = client.get(f"/api/tasks?projectId={pid}", headers=alice).json()
    assert len(tasks) == 2


def test_uploaded_path_uses_forward_slashes(client, alice):
    """os.path.join would emit a backslash on Windows, breaking <img src>."""
    pid = _new_project(client, alice)
    res = client.post(
        f"/api/projects/{pid}/upload",
        files=[("file", ("good.png", PNG_BYTES, "image/png"))],
        headers=alice,
    )
    path = res.json()["uploaded"][0]["path"]
    assert "\\" not in path
    assert path.startswith("uploads/")


def test_empty_file_is_rejected_and_not_left_on_disk(client, alice):
    pid = _new_project(client, alice)
    res = client.post(
        f"/api/projects/{pid}/upload",
        files=[("file", ("empty.png", b"", "image/png"))],
        headers=alice,
    )
    body = res.json()
    assert body["uploaded"] == []
    assert body["failed"][0]["filename"] == "empty.png"


def test_oversized_file_is_rejected_and_not_left_on_disk(client, alice, monkeypatch):
    import api.routers.projects as projects_router
    monkeypatch.setattr(projects_router, "MAX_UPLOAD_BYTES", 10)

    pid = _new_project(client, alice)
    res = client.post(
        f"/api/projects/{pid}/upload",
        files=[("file", ("big.png", PNG_BYTES, "image/png"))],  # 40 bytes > 10 byte cap
        headers=alice,
    )
    body = res.json()
    assert body["uploaded"] == []
    assert "exceeds" in body["failed"][0]["error"]

    db = SessionLocal()
    try:
        assert db.query(models.Task).filter(models.Task.project_id == pid).count() == 0
    finally:
        db.close()


def test_upload_requires_ownership(client, alice, bob):
    pid = _new_project(client, alice)
    res = client.post(
        f"/api/projects/{pid}/upload",
        files=[("file", ("x.png", PNG_BYTES, "image/png"))],
        headers=bob,
    )
    assert res.status_code == 404

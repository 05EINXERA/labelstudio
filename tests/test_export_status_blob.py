"""Polling an export's status must not read the archive out of the database.

A finished export is stored in `export_jobs.content` and can be hundreds of
MB. The page polls the status every second; loading the blob on each poll
exhausted the Postgres container's memory and restarted the database
(2026-10-12, a 245 MB YOLO + original-image export).
"""
import json
import uuid

from sqlalchemy import event

import models
from database import SessionLocal, engine


def _make_job(content: bytes) -> str:
    job_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(models.ExportJob(
            id=job_id, status="completed", media_type="application/zip", filename="x.zip",
            content=content, meta_info=json.dumps({"task_count": 3, "format": "yolo"}),
        ))
        db.commit()
    finally:
        db.close()
    return job_id


def test_status_poll_does_not_select_the_archive(client, alice):
    job_id = _make_job(b"z" * 1024)
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        res = client.get(f"/api/exports/{job_id}", headers=alice)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert res.status_code == 200, res.text
    assert res.json()["status"] == "completed"
    assert res.json()["task_count"] == 3
    job_selects = [s for s in statements if s.lstrip().upper().startswith("SELECT") and "export_jobs" in s]
    assert job_selects, "expected the status poll to query export_jobs"
    assert not any("export_jobs.content" in s for s in job_selects), job_selects


def test_download_still_returns_the_archive(client, alice):
    job_id = _make_job(b"archive-bytes")
    res = client.get(f"/api/exports/{job_id}/download", headers=alice)
    assert res.status_code == 200, res.text
    assert res.content == b"archive-bytes"

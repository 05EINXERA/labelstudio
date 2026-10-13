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


def _job_exists(job_id: str) -> bool:
    db = SessionLocal()
    try:
        return db.query(models.ExportJob.id).filter(models.ExportJob.id == job_id).first() is not None
    finally:
        db.close()


def test_download_streams_in_chunks_and_keeps_the_job(client, alice, monkeypatch):
    """An archive larger than one chunk arrives intact, and downloading does
    not consume the job: it stays until it expires."""
    from api.routers import exports

    monkeypatch.setattr(exports, "DOWNLOAD_CHUNK_BYTES", 1000)
    payload = bytes(range(256)) * 13  # 3328 bytes: three full chunks and a remainder
    job_id = _make_job(payload)

    res = client.get(f"/api/exports/{job_id}/download", headers=alice)
    assert res.status_code == 200, res.text
    assert res.content == payload
    assert 'filename="x.zip"' in res.headers["content-disposition"]
    assert _job_exists(job_id)

    # A second click on Download gets the same archive, not a 404.
    again = client.get(f"/api/exports/{job_id}/download", headers=alice)
    assert again.status_code == 200, again.text
    assert again.content == payload


def test_a_finished_stream_does_not_break_one_still_running():
    """Repeat clicks on Download run parallel streams of one job. The first to
    finish used to delete the row, so the others died mid-archive
    ("vanished during download") and the next click got a 404."""
    from api.routers import exports

    size = exports.DOWNLOAD_CHUNK_BYTES + 10
    job_id = _make_job(b"p" * size)
    slow = exports._stream_archive(job_id, size)
    first_chunk = next(slow)

    assert b"".join(exports._stream_archive(job_id, size)) == b"p" * size

    assert first_chunk + b"".join(slow) == b"p" * size


def test_download_is_not_gzipped_and_keeps_its_length(client, alice):
    """The archive is already compressed. Letting GZipMiddleware re-compress it
    costs event-loop time for every other request and drops Content-Length, so
    the browser cannot show download progress."""
    payload = b"z" * 5000  # over the middleware's 1000-byte minimum
    job_id = _make_job(payload)

    res = client.get(
        f"/api/exports/{job_id}/download",
        headers={**alice, "Accept-Encoding": "gzip"},
    )
    assert res.status_code == 200, res.text
    assert res.headers.get("content-encoding") != "gzip"
    assert res.headers["content-length"] == str(len(payload))
    assert res.content == payload


def test_download_without_the_bearer_header_is_a_json_401(client, alice):
    """The session cookie does not outlive the browser session, but the bearer
    token the page keeps in localStorage does. A browser that has been reopened
    since login therefore authenticates by header only, and a plain link to the
    download (which can carry no header) gets this 401 — which the browser
    saves as "download.json". The page must download through apiFetch."""
    job_id = _make_job(b"archive-bytes")

    bare = client.get(f"/api/exports/{job_id}/download")
    assert bare.status_code == 401
    assert bare.headers["content-type"].startswith("application/json")

    res = client.get(f"/api/exports/{job_id}/download", headers=alice)
    assert res.status_code == 200, res.text
    assert res.content == b"archive-bytes"


def test_download_never_selects_the_whole_archive(client, alice, monkeypatch):
    from api.routers import exports

    monkeypatch.setattr(exports, "DOWNLOAD_CHUNK_BYTES", 1000)
    job_id = _make_job(b"q" * 2500)
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        res = client.get(f"/api/exports/{job_id}/download", headers=alice)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert res.content == b"q" * 2500
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT") and "export_jobs" in s]
    assert selects
    assert not any("export_jobs.content AS" in s or "SELECT export_jobs.content" in s for s in selects), selects


def test_abandoned_download_keeps_the_job_for_a_retry():
    """Closing the stream early (a browser that gave up) must not delete it."""
    from api.routers import exports

    job_id = _make_job(b"r" * (exports.DOWNLOAD_CHUNK_BYTES + 10))
    stream = exports._stream_archive(job_id, exports.DOWNLOAD_CHUNK_BYTES + 10)
    assert len(next(stream)) == exports.DOWNLOAD_CHUNK_BYTES
    stream.close()
    assert _job_exists(job_id)


def test_download_of_a_pending_job_is_refused(client, alice):
    job_id = str(uuid.uuid4())
    db = SessionLocal()
    try:
        db.add(models.ExportJob(id=job_id, status="pending"))
        db.commit()
    finally:
        db.close()
    assert client.get(f"/api/exports/{job_id}/download", headers=alice).status_code == 404


def test_job_vanishing_mid_download_breaks_the_stream():
    """A truncated archive must not look like a finished one."""
    import pytest
    from api.routers import exports

    job_id = _make_job(b"s" * 10)
    stream = exports._stream_archive(job_id, exports.DOWNLOAD_CHUNK_BYTES + 10)  # claims more than exists
    assert next(stream) == b"s" * 10
    with pytest.raises(RuntimeError):
        next(stream)

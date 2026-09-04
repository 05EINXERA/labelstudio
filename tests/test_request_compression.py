"""Tests for gzipped *request* bodies (network-lag F1 / audit finding C1).

Responses were already compressed; requests were not, and requests are the
direction that hurt — every save uploads the task's whole annotation set, so a
~1.8 MB body crossed the LAN raw and took 25-30 s, long enough that weak links
dropped it mid-upload and uvicorn answered 400. See
.devnotes/network-lag/01_AUDIT.md.

The bar these tests exist to hold is **no data loss**. Compression is only worth
having if a gzipped save stores byte-for-byte what the same save would have
stored uncompressed, so the central tests compare against that baseline rather
than merely asserting 200. Silent truncation — at a chunk boundary, or from a
stale Content-Length — is the failure mode that would quietly destroy
annotations, and it is what most of this file is aimed at.
"""
import gzip
import json

import pytest


def _gz(payload: dict) -> bytes:
    return gzip.compress(json.dumps(payload).encode())


def _annotations(count: int) -> str:
    """A realistic annotation set: the shape the canvas actually sends.

    Polygons with many vertices, because that is what makes real payloads large
    and what a truncation bug would cut short.
    """
    return json.dumps([
        {
            "id": f"obj-{i}",
            "type": "polygon",
            "label": f"defect_{i % 7}",
            "color": "#ff8800",
            "visible": True,
            "points": [[i + j * 0.5, i * 2 + j] for j in range(18)],
        }
        for i in range(count)
    ])


@pytest.fixture
def project(client, alice):
    res = client.post(
        "/api/projects",
        json={"name": "compression", "slug": "compression", "creator": "alice"},
        headers=alice,
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _make_task(client, headers, project_id):
    """Create a task and return the whole record.

    The record, not just the id, because every save needs the task's
    `updated_at` as its concurrency token — see _save_body.
    """
    res = client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": "img.jpg", "status": "New"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res.json()


def _save_body(task, annotations):
    """A save payload carrying the concurrency token.

    `updated_at` is not optional in practice: without it the server treats the
    write as having read nothing and answers 409 (CLAUDE.md rule 11). Every test
    here is about the transport, so each one has to be a *valid* save first —
    otherwise a 409 would mask whatever the compression path actually did.
    """
    return {
        "id": task["id"],
        "annotations": annotations,
        "time_spent_delta": 0,
        "updated_at": task.get("updated_at"),
        "client_id": "tab-compression",
    }


def _read_annotations(client, headers, task_id):
    """The stored annotation set, always as a list.

    `TaskDetail.annotations` is a parsed list, not the raw blob string, so
    callers compare structures rather than re-parsing (CLAUDE.md rule 6).
    """
    res = client.get(f"/api/tasks/{task_id}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["annotations"]


def test_gzipped_body_is_accepted(client, alice, project):
    task = _make_task(client, alice, project)
    res = client.post(
        "/api/tasks",
        content=_gz(_save_body(task, _annotations(5))),
        headers={**alice, "Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    assert res.status_code == 200, res.text


def test_gzipped_and_plaintext_store_identical_annotations(client, alice, project):
    """The no-loss bar: compression must not change what is stored.

    Two tasks, the same annotation set, one sent each way. Comparing the stored
    values against each other (not just against 200) is what would catch a body
    that arrived subtly short.
    """
    payload = _annotations(50)

    plain = _make_task(client, alice, project)
    res = client.post("/api/tasks", json=_save_body(plain, payload), headers=alice)
    assert res.status_code == 200, res.text

    gz = _make_task(client, alice, project)
    res = client.post(
        "/api/tasks",
        content=_gz(_save_body(gz, payload)),
        headers={**alice, "Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    assert res.status_code == 200, res.text

    assert (
        _read_annotations(client, alice, gz["id"])
        == _read_annotations(client, alice, plain["id"])
    )


def test_large_annotation_set_survives_intact(client, alice, project):
    """~3000 objects — the size that provoked the incident.

    A small fixture fits in one chunk and would pass even with the multi-chunk
    handling broken, so the count here is load-bearing: it has to be big enough
    that the body genuinely spans several reads.
    """
    task = _make_task(client, alice, project)
    payload = _annotations(3000)
    assert len(payload) > 1_000_000, "fixture too small to exercise chunking"

    res = client.post(
        "/api/tasks",
        content=_gz(_save_body(task, payload)),
        headers={**alice, "Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    assert res.status_code == 200, res.text

    stored = _read_annotations(client, alice, task["id"])
    assert len(stored) == 3000
    # Spot-check both ends: a truncation would take the tail, and an off-by-one
    # in the replay would take the head.
    assert stored[0]["id"] == "obj-0"
    assert stored[-1]["id"] == "obj-2999"
    assert len(stored[-1]["points"]) == 18


def test_plaintext_body_still_accepted(client, alice, project):
    """The permanent dual path.

    Clients without CompressionStream, and every sendBeacon unload flush, keep
    sending plaintext forever. This is normal traffic, not a legacy case.
    """
    task = _make_task(client, alice, project)
    res = client.post("/api/tasks", json=_save_body(task, _annotations(3)), headers=alice)
    assert res.status_code == 200, res.text
    assert len(_read_annotations(client, alice, task["id"])) == 3


def test_malformed_gzip_is_rejected_with_400(client, alice, project):
    """Not a 500, and not a partial write.

    400 tells the client the request was at fault and it may retry
    uncompressed; a 500 would suggest the server is broken and invite a retry
    loop against a request that can never succeed.
    """
    task = _make_task(client, alice, project)
    res = client.post(
        "/api/tasks",
        content=b"this is not gzip at all",
        headers={**alice, "Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    assert res.status_code == 400, res.text

    # And nothing was written on the way to failing.
    assert _read_annotations(client, alice, task["id"]) in (None, [])


def test_oversized_inflated_body_is_rejected_with_413(client, alice, project, monkeypatch):
    """A gzip bomb must be refused while inflating, not after."""
    import api.compression as compression

    monkeypatch.setattr(compression, "MAX_DECOMPRESSED_BODY", 2048)

    task = _make_task(client, alice, project)
    res = client.post(
        "/api/tasks",
        content=_gz(_save_body(task, _annotations(200))),
        headers={**alice, "Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    assert res.status_code == 413, res.text


def test_gzip_with_query_param_csrf_still_passes(client, project):
    """The beacon-shaped request: CSRF in the query string, not a header.

    Beacons themselves never compress (they cannot await a compression stream
    without risking the unload flush being dropped), but CSRF resolution must be
    provably independent of body encoding — otherwise a later change that did
    compress them would fail in the one place work is least recoverable.
    """
    username = f"csrf-gzip-{project}"
    res = client.post(
        "/api/auth/register", json={"username": username, "password": "pw-12345"}
    )
    assert res.status_code == 200, res.text
    csrf = client.cookies.get("csrf_token")
    assert csrf, "registration should set the CSRF cookie"

    # Cookie-authenticated, so CSRF applies (a Bearer client is exempt — see
    # require_csrf). Setup calls carry the token in the header, the way the app
    # normally does; only the save under test uses the query-param fallback.
    hdr = {"X-CSRF-Token": csrf}
    res = client.post(
        "/api/projects",
        json={"name": "beacon", "slug": f"beacon-{project}", "creator": username},
        headers=hdr,
    )
    assert res.status_code == 200, res.text
    proj = res.json()["id"]
    task = _make_task(client, hdr, proj)

    res = client.post(
        f"/api/tasks?csrf_token={csrf}",
        content=_gz(_save_body(task, _annotations(4))),
        headers={"Content-Type": "application/json", "Content-Encoding": "gzip"},
    )
    assert res.status_code == 200, res.text
    assert len(_read_annotations(client, hdr, task["id"])) == 4


def test_non_gzip_content_encoding_is_left_alone(client, alice, project):
    """Only gzip is claimed. An unknown encoding must not be silently inflated."""
    task = _make_task(client, alice, project)
    res = client.post(
        "/api/tasks",
        json=_save_body(task, _annotations(2)),
        headers={**alice, "Content-Encoding": "identity"},
    )
    assert res.status_code == 200, res.text

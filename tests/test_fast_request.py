"""Request bodies on the tasks router are parsed by `fastjson`.

The route class (`api/fast_request.py`) swaps the parser FastAPI runs on the
event loop. It must be invisible: same saves, same 422 for a malformed body, and
nothing that the stdlib parser accepted may start failing.
"""
import json

import pytest

import fastjson
from api.fast_request import FastJSONRequest

CLIENT = "tab-fast-request"


def _task(client, alice):
    res = client.post("/api/projects", json={
        "name": "fr", "slug": "fr", "creator": "alice"}, headers=alice)
    pid = res.json()["id"]
    res = client.post(
        f"/api/tasks?projectId={pid}",
        json={"description": "i.jpg", "status": "New", "client_id": CLIENT},
        headers=alice,
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _post(client, alice, body: bytes):
    return client.post(
        "/api/tasks", content=body,
        headers={**alice, "Content-Type": "application/json"},
    )


def _saved(client, alice, tid):
    res = client.get(f"/api/tasks/{tid}", headers=alice)
    assert res.status_code == 200, res.text
    return res.json()["annotations"]


def test_a_save_round_trips_through_the_fast_parser(client, alice):
    tid = _task(client, alice)
    shapes = [{"id": f"a{i}", "type": "polygon",
               "points": [{"x": i + 0.5, "y": 2.25}, {"x": 9.0, "y": 1.0}]}
              for i in range(20)]
    body = json.dumps({"id": tid, "client_id": CLIENT, "status": "In Progress",
                       "annotations": json.dumps(shapes)}).encode()
    assert _post(client, alice, body).status_code == 200
    assert [a["id"] for a in _saved(client, alice, tid)] == [s["id"] for s in shapes]
    assert _saved(client, alice, tid)[3]["points"] == shapes[3]["points"]


def test_malformed_json_is_still_a_422(client, alice):
    res = _post(client, alice, b'{"id": 1, "annotations": ')
    assert res.status_code == 422, res.text


def test_a_body_the_stdlib_accepts_but_orjson_refuses_still_saves(client, alice):
    """`NaN` is invalid JSON to orjson and valid to the stdlib parser.

    An unknown top-level field is ignored by the schema, so the only thing that
    could fail this request is the parse itself.
    """
    tid = _task(client, alice)
    body = ('{"id": %d, "client_id": "%s", "status": "In Progress", '
            '"scratch": NaN}' % (tid, CLIENT)).encode()
    assert _post(client, alice, body).status_code == 200


def test_json_is_cached_per_request():
    import asyncio

    async def go():
        body = b'{"a": [1, 2, 3]}'
        sent = False

        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        request = FastJSONRequest({"type": "http", "headers": []}, receive)
        first = await request.json()
        second = await request.json()
        assert first == {"a": [1, 2, 3]} and first is second

    asyncio.run(go())

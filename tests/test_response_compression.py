"""Tests for GZip response compression (server-optimization T1 / finding F3).

Nothing was compressed before this: the JS module graph (~562 KB across 50
files), styles.css (~64 KB) and every JSON body crossed the LAN raw. See
.devnotes/server-optimization/06_CACHING.md.

The properties worth pinning down are that compression actually engages on the
payloads that matter, that it does not engage where it would be pointless or
harmful, and — the subtle one — that adding an outer middleware did not
silently drop the security and cache headers set by the inner one.
"""
import json

import pytest

from tests.conftest import _register


def _get(client, url, **kwargs):
    """GET announcing gzip support, with redirects followed off by default.

    TestClient sends `Accept-Encoding: gzip` by default *and* transparently
    decodes the body, so `response.content` is always the plaintext. The
    `content-encoding` header is what reveals whether the wire bytes were
    compressed.
    """
    headers = {"Accept-Encoding": "gzip", **kwargs.pop("headers", {})}
    return client.get(url, headers=headers, **kwargs)


def test_large_json_response_is_compressed(client):
    """The payload shape this was added for: a repetitive JSON body."""
    headers = _register(client, "gzip-json")

    # Workspace data takes an arbitrary value, which makes it the cheapest way
    # to get a comfortably-over-threshold body through the real middleware
    # stack without depending on project/task fixtures.
    client.post(
        "/api/data",
        json={"key": "layout", "value": "x" * 4000},
        headers=headers,
    )

    res = _get(client, "/api/data", headers=headers)
    assert res.status_code == 200
    assert res.headers.get("content-encoding") == "gzip"


def test_static_css_is_compressed(client):
    """styles.css is ~64 KB and is fetched on every navigation."""
    res = _get(client, "/styles.css")
    assert res.status_code == 200
    assert res.headers.get("content-encoding") == "gzip"


def test_small_response_is_still_compressed_documenting_minimum_size_bypass(client):
    """`minimum_size` does not bite in this app, and this pins down why.

    GZipMiddleware only honours `minimum_size` when it can see the body length
    up front. `add_security_and_cache_headers` is a `BaseHTTPMiddleware`, and
    that class turns every response into a streaming one with no
    `content-length` — so GZip takes the streaming path and compresses
    regardless of size.

    The cost is small (a few hundred wasted bytes and a little CPU on tiny
    acknowledgements) and it is strictly better than the pre-T1 state, so it is
    documented rather than worked around: the fix would be rewriting the header
    middleware as pure ASGI, which is a bigger change than T1 and belongs with
    T2 if it is made at all.

    This test therefore asserts current reality. If a future change makes
    `minimum_size` effective, this test failing is the intended signal to flip
    it to `is None` — not a regression.
    """
    res = _get(client, "/health")
    assert res.status_code == 200
    assert res.headers.get("content-encoding") == "gzip"
    # The body really is small; it is the missing content-length, not the size,
    # that routes it down the streaming path.
    assert len(res.content) < 500


def test_client_without_gzip_support_gets_plaintext(client):
    """A client that does not advertise gzip must still get a usable body."""
    headers = _register(client, "gzip-optout")
    client.post(
        "/api/data",
        json={"key": "layout", "value": "y" * 4000},
        headers=headers,
    )

    res = client.get(
        "/api/data",
        headers={**headers, "Accept-Encoding": "identity"},
    )
    assert res.status_code == 200
    assert res.headers.get("content-encoding") is None
    assert json.loads(res.content)["layout"] == "y" * 4000


def test_compressed_body_round_trips_intact(client):
    """Compression must be transparent: same bytes out as went in.

    Guards the case where a body is truncated or double-encoded — which would
    show up as valid-looking JSON that is subtly short.
    """
    headers = _register(client, "gzip-roundtrip")
    payload = "".join(str(i % 10) for i in range(6000))
    client.post(
        "/api/data",
        json={"key": "layout", "value": payload},
        headers=headers,
    )

    res = _get(client, "/api/data", headers=headers)
    assert res.headers.get("content-encoding") == "gzip"
    assert res.json()["layout"] == payload


@pytest.mark.parametrize(
    "header, expected",
    [
        ("X-Content-Type-Options", "nosniff"),
        ("X-Frame-Options", "DENY"),
        ("Referrer-Policy", "same-origin"),
    ],
)
def test_security_headers_survive_compression(client, header, expected):
    """GZip is registered outermost; the inner header pass must still win.

    Starlette runs the last-added middleware outermost, so a mistake here
    (registering GZip before the header middleware) would not raise — it would
    quietly stop serving these headers on compressed responses only. That is
    exactly the kind of regression that survives a manual smoke test.
    """
    res = _get(client, "/styles.css")
    assert res.headers.get("content-encoding") == "gzip"
    assert res.headers.get(header) == expected


def test_cache_control_survives_compression(client):
    """The static-asset cache directive must not be lost on compressed assets.

    Pinned because T2 changed this value: if compression were swallowing the
    header, that change would have appeared to work while doing nothing.
    """
    res = _get(client, "/styles.css")
    assert res.headers.get("content-encoding") == "gzip"
    assert res.headers.get("Cache-Control") == "no-cache"

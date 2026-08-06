"""Tests for static-asset cache headers (server-optimization T2 / finding F4).

The header used to be `no-store`, which forbids caching outright and made every
navigation re-download ~626 KB of JS and CSS. It is now `no-cache`: storage is
allowed, revalidation is mandatory, so an unchanged asset costs a 304 instead of
its full body.

The distinction these tests protect is that `no-cache` must NOT drift back to
`no-store` (which would silently restore the cost) and must NOT drift forward to
an unconditional `max-age` (which would serve stale bundles without asking the
server). See .devnotes/server-optimization/06_CACHING.md.
"""
import pytest

STATIC_PATHS = ["/styles.css", "/js/utils.js", "/app.html"]


@pytest.mark.parametrize("path", STATIC_PATHS)
def test_static_assets_are_revalidated_not_forbidden(path, client):
    """`no-cache` exactly: storable, but revalidated on every use."""
    res = client.get(path)
    assert res.status_code == 200, path
    assert res.headers.get("Cache-Control") == "no-cache", path


@pytest.mark.parametrize("path", STATIC_PATHS)
def test_static_assets_are_not_no_store(path, client):
    """The regression this change exists to prevent.

    Spelled out separately from the equality assertion above because
    `no-store` is the specific value that costs a full re-download, and a
    future edit reaching for "make caching safer" is most likely to reach for
    exactly that word.
    """
    cache_control = client.get(path).headers.get("Cache-Control", "")
    assert cache_control, f"{path} has no Cache-Control header at all"
    assert "no-store" not in cache_control, path


@pytest.mark.parametrize("path", STATIC_PATHS)
def test_legacy_http10_no_cache_headers_are_gone(path, client):
    """`Pragma`/`Expires` only ever meant "don't cache".

    Leaving them would contradict `Cache-Control: no-cache` for any
    intermediary that still honours them.
    """
    res = client.get(path)
    assert "Pragma" not in res.headers, path
    assert "Expires" not in res.headers, path


@pytest.mark.parametrize("path", STATIC_PATHS)
def test_static_assets_carry_a_validator(path, client):
    """`no-cache` is only cheap if the asset can be revalidated.

    Without `ETag`/`Last-Modified` the browser has nothing to send in a
    conditional request, so every revalidation returns a full body and the
    change achieves nothing. StaticFiles supplies these; this pins that
    dependency so it cannot be lost silently.
    """
    res = client.get(path)
    assert res.headers.get("etag") or res.headers.get("last-modified"), path


@pytest.mark.parametrize("path", STATIC_PATHS)
def test_conditional_request_returns_304_with_empty_body(path, client):
    """The actual payoff: an unchanged asset costs no bytes.

    This is the whole point of T2 — it is what turns a reload from a ~626 KB
    re-download into a handful of empty 304s.
    """
    first = client.get(path)
    etag = first.headers.get("etag")
    assert etag, f"{path} has no ETag to revalidate with"

    second = client.get(path, headers={"If-None-Match": etag})
    assert second.status_code == 304, path
    assert second.content == b"", path


def test_api_responses_are_not_given_the_static_cache_header(client):
    """The cache branch is path-suffix based; API JSON must not fall into it.

    An API response marked cacheable — even as `no-cache` — would be a
    correctness problem, not just a performance one.
    """
    res = client.get("/health")
    assert res.status_code == 200
    assert res.headers.get("Cache-Control") is None

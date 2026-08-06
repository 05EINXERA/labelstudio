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


# --- /uploads (T5, finding F5) ---------------------------------------------
#
# Uploaded images are genuinely immutable — _save_upload (api/routers/
# projects.py) names every file with a fresh uuid4().hex and nothing ever
# writes into an existing upload path afterward — so they get the strongest
# cache directive instead of the revalidate-always one above. This does not
# touch the image bytes or quality in any way; only the response header
# changes. See .devnotes/server-optimization/06_CACHING.md (R-CACHE-3).

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def _upload_one(client, auth):
    pid = client.post(
        "/api/projects", json={"name": "cache-t5", "slug": "cache-t5", "creator": "x"},
        headers=auth,
    ).json()["id"]
    res = client.post(
        f"/api/projects/{pid}/upload",
        files=[("file", ("photo.png", PNG_BYTES, "image/png"))],
        headers=auth,
    )
    assert res.status_code == 200, res.text
    return res.json()["uploaded"][0]["path"]


def test_uploaded_image_is_cached_as_immutable(client, alice):
    path = _upload_one(client, alice)
    res = client.get(f"/{path}")
    assert res.status_code == 200
    cache_control = res.headers.get("Cache-Control", "")
    assert "immutable" in cache_control
    assert "max-age=31536000" in cache_control


def test_uploaded_image_is_not_marked_no_cache_or_no_store(client, alice):
    """Uploads must not fall into the JS/CSS/HTML branch above.

    Both directives are wrong for an immutable file: `no-cache` would force a
    pointless revalidation round trip on every view, and `no-store` would
    reintroduce the exact re-download cost T5 exists to remove.
    """
    path = _upload_one(client, alice)
    cache_control = client.get(f"/{path}").headers.get("Cache-Control", "")
    assert "no-cache" not in cache_control
    assert "no-store" not in cache_control


def test_uploaded_image_bytes_are_unchanged_by_the_header_change(client, alice):
    """T5 only adds a header; the served bytes must be byte-for-byte identical.

    This is the direct check that image quality/content was not touched: the
    same request before and after this change must return the same file.
    """
    path = _upload_one(client, alice)
    res = client.get(f"/{path}")
    assert res.content == PNG_BYTES


def test_non_upload_static_paths_are_unaffected_by_the_uploads_branch(client):
    """/uploads/ is a path-prefix check; it must not swallow other paths."""
    res = client.get("/styles.css")
    cache_control = res.headers.get("Cache-Control", "")
    assert "immutable" not in cache_control

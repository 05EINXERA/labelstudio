"""Task images should be fetched once per open, not three times.

Opening a task used to issue three requests for the same image URL:

1. `view.imageElement.src` — the Image() the canvas draws from.
2. `backgroundImage.src` — the backdrop <img>, set inside the canvas image's
   `onload`, so its fetch started only after the first had fully completed
   rather than coalescing with it.
3. `preloadAdjacentImages()` — index +/- 1, which re-requests the image you
   just navigated away from.

The fix pairs an ordering change in gallery.js (backdrop src set *before* the
canvas Image, so both resolve from one fetch) with `immutable` caching on
/uploads, which lets 2 and 3 hit cache with no revalidation round-trip.
"""
import os

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"0" * 32

GALLERY_JS = os.path.join(
    os.path.dirname(os.path.dirname(__file__)),
    "frontend", "js", "components", "gallery.js",
)


def _new_project(client, auth):
    res = client.post(
        "/api/projects",
        json={"name": "imgcache", "slug": "imgcache", "creator": "ignored"},
        headers=auth,
    )
    return res.json()["id"]


def test_uploaded_image_is_served_immutable(client, alice):
    """An uploaded image carries `immutable`, so a re-open needs no revalidation."""
    pid = _new_project(client, alice)
    res = client.post(
        f"/api/projects/{pid}/upload",
        files=[("file", ("shot.png", PNG_BYTES, "image/png"))],
        headers=alice,
    )
    assert res.status_code == 200, res.text

    tasks = client.get(f"/api/tasks/sequence/{pid}", headers=alice).json()
    url = "/" + tasks[0]["image_path"].replace("\\", "/")

    got = client.get(url, headers=alice)
    assert got.status_code == 200
    cache_control = got.headers.get("cache-control", "")
    assert "immutable" in cache_control, cache_control
    assert "max-age=31536000" in cache_control, cache_control


def test_non_upload_static_assets_keep_their_own_caching(client):
    """The uploads rule must not swallow JS, which needs to stay revalidating."""
    res = client.get("/js/components/gallery.js")
    assert res.status_code == 200
    # JS is version-pinned by query string, not content-hashed (CLAUDE.md #13),
    # so it must never be served as immutable or clients would pin forever.
    assert "immutable" not in res.headers.get("cache-control", "")


def test_backdrop_src_is_set_before_the_canvas_image_loads():
    """The backdrop <img> src must not be assigned inside the Image onload.

    Assigning it there starts a second fetch after the first has completed,
    which is the duplicate-request bug this guards against.
    """
    with open(GALLERY_JS, "r", encoding="utf-8") as f:
        content = f.read()

    assign = "backgroundImage.src = src"
    assert content.count(assign) == 1, "backdrop src should be assigned exactly once"

    onload_start = content.index("view.imageElement.onload")
    assert content.index(assign) < onload_start, (
        "backgroundImage.src must be set before view.imageElement.onload is "
        "defined, so the backdrop and canvas image share one fetch"
    )

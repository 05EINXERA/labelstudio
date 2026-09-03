"""The vendored annotation manual: routing, self-containment and the nav link.

The manual (`frontend/manual/`) is served by this app rather than a second
service on its own port, so that every link to it can stay root-relative and no
LAN address is baked into the frontend bundle. That decision only holds as long
as three things stay true, and each fails quietly:

  * the bare `/manual/` URL resolves (a `StaticFiles` mount does not imply an
    index for subdirectories, so it needs its own route);
  * the page stays self-contained — it was built to run from a USB stick with
    no network, and a CDN reference added later would break it on the LAN
    deployment, which has no internet access;
  * nothing reintroduces an absolute `http://<ip>:<port>/` link between the app
    and the manual, in either direction.

The nav half of the guard runs under node (tests/js/app_nav_spec.mjs) and is
invoked from here so a plain `pytest tests/` covers it.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).parent.parent / "frontend"
MANUAL = FRONTEND / "manual"
INDEX = MANUAL / "index.html"
SPEC = Path(__file__).parent / "js" / "app_nav_spec.mjs"


# --- routing ----------------------------------------------------------------

def test_bare_manual_url_serves_the_page(client):
    """`/manual/` is what the nav links to, so it is the path that must work."""
    res = client.get("/manual/")
    assert res.status_code == 200, res.text
    assert "text/html" in res.headers["content-type"]
    assert "Annotation Manual" in res.text


def test_manual_index_is_also_reachable_directly(client):
    """Served by the catch-all frontend mount, for anyone holding an old link."""
    res = client.get("/manual/index.html")
    assert res.status_code == 200
    assert "Annotation Manual" in res.text


def test_manual_assets_are_served(client):
    """The images are the bulk of the page; a broken asset path makes the
    manual useless without making it look broken in a test that only fetches
    the HTML."""
    images = sorted((MANUAL / "assets" / "img").glob("*"))
    assert images, "no manual images found"
    for img in images[:3]:
        res = client.get(f"/manual/assets/img/{img.name}")
        assert res.status_code == 200, f"{img.name}: {res.status_code}"
        assert res.content, f"{img.name} served empty"
        # The case for serving the manual from this process rather than putting
        # nginx in front of it rests on the app's own ETag/304 layer, so pin
        # that the manual's assets actually get it.
        assert res.headers.get("etag"), f"{img.name} served without an ETag"


def test_manual_assets_revalidate_with_304(client):
    """A cached image must cost a 304, not a re-download — 3.8 MB of JPEGs
    across ~25 annotators is the whole reason the caching layer matters here."""
    name = sorted((MANUAL / "assets" / "img").glob("*"))[0].name
    first = client.get(f"/manual/assets/img/{name}")
    again = client.get(
        f"/manual/assets/img/{name}",
        headers={"If-None-Match": first.headers["etag"]},
    )
    assert again.status_code == 304
    assert not again.content


def test_manual_does_not_shadow_the_api(client):
    """The route is registered before the catch-all mount, so this pins that it
    did not widen into anything else."""
    assert client.get("/manual/nonexistent.html").status_code == 404


# --- self-containment -------------------------------------------------------

def test_manual_has_no_external_resource_references():
    """The deployment has no internet access and the page must work from a
    file:// URL, so every script, style and image has to be local."""
    html = INDEX.read_text(encoding="utf-8")
    external = re.findall(r'(?:src|href)="(https?://[^"]*|//[^"]*)"', html)
    assert not external, f"external references in the manual: {external}"


def test_manual_links_to_the_app_relatively():
    """The manual's back-link to the app must not hardcode a host or port; the
    two are served from the same origin precisely so it does not have to."""
    html = INDEX.read_text(encoding="utf-8")
    assert not re.search(r'href="[^"]*\d+\.\d+\.\d+\.\d+', html), \
        "the manual hardcodes an IP address in a link"


def test_manual_asset_references_all_resolve():
    """Catches a renamed or missing image without opening a browser."""
    html = INDEX.read_text(encoding="utf-8")
    refs = set(re.findall(r'(?:src|href)="(assets/[^"]+)"', html))
    assert refs, "the manual references no local assets — has it been replaced?"
    missing = sorted(r for r in refs if not (MANUAL / r).is_file())
    assert not missing, f"manual references missing files: {missing}"


# --- the nav link -----------------------------------------------------------

def test_nav_link_target_exists():
    """`app-nav.js` points at `/manual/`; this asserts the other end of that
    link is actually in the tree, which the JS spec cannot see."""
    assert INDEX.is_file(), f"missing manual page: {INDEX}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_client_app_nav_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"app-nav spec failed:\n{result.stdout}\n{result.stderr}"
    )

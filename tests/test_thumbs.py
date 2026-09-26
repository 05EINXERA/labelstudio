"""List-view thumbnails: `GET /thumbs/{filename}` and api/thumbnails.py.

The Tasks and Move tables drew 40 px thumbnails from 10 MB originals, ~99 MB
per page (.devnotes/frontend-telemetry/07_TASKS_PAGE_THUMBNAILS.md). These
tests pin the replacement: small, correct, cached, safe against arbitrary
paths, and never a way back to the original.
"""
import io
import threading

import pytest
from PIL import Image

import api.thumbnails as thumbs

NAME = "0123456789abcdef0123456789abcdef"


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(thumbs, "DATA_DIR", str(tmp_path))
    (tmp_path / "uploads").mkdir()
    return tmp_path


def _jpeg(path, size=(2000, 1500), exif_orientation=None):
    img = Image.new("RGB", size, (200, 30, 30))
    kwargs = {}
    if exif_orientation:
        exif = Image.Exif()
        exif[0x0112] = exif_orientation
        kwargs["exif"] = exif.tobytes()
    img.save(path, "JPEG", quality=90, **kwargs)


def _webp_size(content):
    with Image.open(io.BytesIO(content)) as img:
        assert img.format == "WEBP"
        return img.size, img.mode


def test_thumbnail_is_small_webp_with_immutable_cache(client, data_dir):
    _jpeg(data_dir / "uploads" / f"{NAME}.jpg")
    res = client.get(f"/thumbs/{NAME}.jpg")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/webp"
    assert res.headers["cache-control"] == "public, max-age=31536000, immutable"
    size, mode = _webp_size(res.content)
    assert size == (160, 120)
    assert mode == "RGB"
    assert len(res.content) < 20_000
    assert (data_dir / "thumbs" / f"{NAME}.webp").is_file()


def test_second_request_is_served_from_the_cache(client, data_dir, monkeypatch):
    _jpeg(data_dir / "uploads" / f"{NAME}.jpg")
    assert client.get(f"/thumbs/{NAME}.jpg").status_code == 200

    def must_not_render(source, target):
        raise AssertionError("regenerated a cached thumbnail")

    monkeypatch.setattr(thumbs, "render_thumbnail", must_not_render)
    assert client.get(f"/thumbs/{NAME}.jpg").status_code == 200


def test_exif_rotated_photo_comes_out_upright(client, data_dir):
    # Stored landscape 300x200, orientation 6 = displayed rotated 90°: portrait.
    _jpeg(data_dir / "uploads" / f"{NAME}.jpg", size=(300, 200), exif_orientation=6)
    size, _ = _webp_size(client.get(f"/thumbs/{NAME}.jpg").content)
    assert size[1] > size[0]


def test_png_transparency_is_kept(client, data_dir):
    Image.new("RGBA", (400, 400), (0, 0, 0, 0)).save(data_dir / "uploads" / f"{NAME}.png")
    size, mode = _webp_size(client.get(f"/thumbs/{NAME}.png").content)
    assert size == (160, 160) and mode == "RGBA"


@pytest.mark.parametrize("name", [
    "../secret.jpg", "..%2Fsecret.jpg", "a.jpg", f"{NAME}.exe", f"{NAME.upper()}.jpg",
    f"{NAME}.jpg.webp", f"x{NAME}.jpg",
])
def test_non_upload_names_are_404(client, data_dir, name):
    assert client.get(f"/thumbs/{name}").status_code == 404


def test_missing_source_is_404(client, data_dir):
    assert client.get(f"/thumbs/{NAME}.jpg").status_code == 404


def test_corrupt_source_is_404_logged_and_leaves_nothing(client, data_dir, caplog, monkeypatch):
    # alembic/env.py's fileConfig disables every existing logger, so a test
    # that ran migrations earlier in the session would silence this one.
    monkeypatch.setattr(thumbs.logger, "disabled", False)
    (data_dir / "uploads" / f"{NAME}.jpg").write_bytes(b"not an image at all")
    with caplog.at_level("WARNING"):
        assert client.get(f"/thumbs/{NAME}.jpg").status_code == 404
    assert "Could not make a thumbnail" in caplog.text
    leftovers = list((data_dir / "thumbs").glob("*")) if (data_dir / "thumbs").exists() else []
    assert leftovers == []


def test_busy_generator_answers_503_with_retry_after(client, data_dir, monkeypatch):
    _jpeg(data_dir / "uploads" / f"{NAME}.jpg")
    monkeypatch.setattr(thumbs, "_GENERATE_SLOTS", threading.BoundedSemaphore(1))
    monkeypatch.setattr(thumbs, "GENERATE_WAIT_SECONDS", 0.01)
    thumbs._GENERATE_SLOTS.acquire()
    try:
        res = client.get(f"/thumbs/{NAME}.jpg")
    finally:
        thumbs._GENERATE_SLOTS.release()
    assert res.status_code == 503
    assert res.headers["retry-after"] == "2"


def test_thumbs_route_is_not_shadowed_by_the_static_mount(client, data_dir):
    """The catch-all StaticFiles mount at "/" would 404 this path if the
    router were included after it."""
    _jpeg(data_dir / "uploads" / f"{NAME}.jpg")
    assert client.get(f"/thumbs/{NAME}.jpg").headers["content-type"] == "image/webp"


def test_thumbs_need_no_login_like_uploads(client, data_dir):
    client.cookies.clear()
    _jpeg(data_dir / "uploads" / f"{NAME}.jpg")
    assert client.get(f"/thumbs/{NAME}.jpg").status_code == 200


# --- backfill script -------------------------------------------------------------

import io as _io  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import backfill_thumbnails  # noqa: E402


def test_backfill_makes_skips_and_reports(data_dir, monkeypatch):
    monkeypatch.setattr(thumbs.logger, "disabled", False)
    uploads = data_dir / "uploads"
    good = [f"{i:032x}.jpg" for i in range(3)]
    for name in good:
        _jpeg(uploads / name, size=(400, 300))
    (uploads / f"{9:032x}.jpg").write_bytes(b"corrupt")
    (uploads / "notes.txt").write_text("not an upload")

    dry = backfill_thumbnails.backfill(dry_run=True, out=_io.StringIO())
    assert dry["made"] == 4 and not (data_dir / "thumbs").exists()

    first = backfill_thumbnails.backfill(out=_io.StringIO())
    assert first == {"made": 3, "skipped": 0, "failed": 1, "ignored": 1}
    assert sorted(p.name for p in (data_dir / "thumbs").iterdir()) == \
        sorted(n.replace(".jpg", ".webp") for n in good)

    again = backfill_thumbnails.backfill(out=_io.StringIO())
    assert again["made"] == 0 and again["skipped"] == 3 and again["failed"] == 1


def test_backfill_exit_code_reflects_failures(data_dir, monkeypatch):
    monkeypatch.setattr(thumbs.logger, "disabled", False)
    _jpeg(data_dir / "uploads" / f"{NAME}.jpg", size=(200, 100))
    assert backfill_thumbnails.main([]) == 0
    (data_dir / "uploads" / f"{1:032x}.png").write_bytes(b"bad")
    assert backfill_thumbnails.main([]) == 1


def test_backfill_without_uploads_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(thumbs, "DATA_DIR", str(tmp_path / "empty"))
    assert backfill_thumbnails.backfill(out=_io.StringIO())["made"] == 0

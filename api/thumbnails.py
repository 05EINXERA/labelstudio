"""Small list-view thumbnails for task images.

The Tasks and Move tables used to draw each row's 40 px thumbnail from the
full original upload — p50 9.9 MB, 20 MP — so one 10-row page cost ~99 MB on
the wire and ~800 MB of decoded bitmaps, per annotator, per page, sort,
filter or search (.devnotes/frontend-telemetry/01_TRAFFIC_INVENTORY.md §3,
07_TASKS_PAGE_THUMBNAILS.md). A 160 px WebP of the same image is 1-4 KB.

This module is the one place a thumbnail is made; the `/thumbs/` router and
`scripts/backfill_thumbnails.py` both call `ensure_thumbnail`, so the cached
files are identical whichever produced them.

Thumbnails are a disposable cache under `DATA_DIR/thumbs/`: deleting the
folder only costs regeneration, so it is not backed up. An upload is never
rewritten in place (every upload gets a fresh uuid name, main.py), so a
thumbnail can never go stale.

Imports only config and Pillow — never a router.
"""
import logging
import os
import re
import threading

from PIL import Image, ImageOps, UnidentifiedImageError

from config import DATA_DIR

logger = logging.getLogger(__name__)

# Long edge in pixels. The table draws 40 px tall; 160 covers a 2x-3x display
# and portrait images without looking soft.
THUMB_SIZE = 160
THUMB_QUALITY = 70

# `_save_upload`'s naming: uuid4().hex plus an ALLOWED_UPLOAD_EXTENSIONS
# suffix (api/routers/projects.py). Anything else is not an upload, which is
# also what makes path traversal impossible here.
UPLOAD_NAME = re.compile(r"^[0-9a-f]{32}\.(png|jpg|jpeg|gif|webp)$")

# At most this many thumbnails decode at once. Pillow releases the GIL while
# decoding, but a room of cold Tasks pages must not take every core from the
# API. After the backfill the cold path is rare (new uploads only).
_GENERATE_SLOTS = threading.BoundedSemaphore(2)
# How long a request waits for a slot before answering 503 (Retry-After).
GENERATE_WAIT_SECONDS = 5.0


class ThumbnailBusy(Exception):
    """No generation slot came free in time."""


class ThumbnailUnavailable(Exception):
    """The source is missing or cannot be decoded."""


def uploads_dir() -> str:
    return os.path.join(DATA_DIR, "uploads")


def thumbs_dir() -> str:
    return os.path.join(DATA_DIR, "thumbs")


def thumb_path(filename: str) -> str:
    stem = os.path.splitext(filename)[0]
    return os.path.join(thumbs_dir(), f"{stem}.webp")


def render_thumbnail(source: str, target: str) -> None:
    """Decode `source` and write a WebP thumbnail to `target`, atomically.

    JPEG `draft` asks the decoder for a DCT-scaled image close to the target
    size — the reason a 20 MP photo takes ~120 ms rather than seconds.
    `exif_transpose` keeps phone photos upright; a sideways thumbnail would be
    a visible regression against the original it stands for.
    """
    with Image.open(source) as img:
        if img.format == "JPEG":
            img.draft("RGB", (THUMB_SIZE * 2, THUMB_SIZE * 2))
        img = ImageOps.exif_transpose(img)
        has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
        img = img.convert("RGBA" if has_alpha else "RGB")
        img.thumbnail((THUMB_SIZE, THUMB_SIZE))
        os.makedirs(os.path.dirname(target), exist_ok=True)
        tmp = f"{target}.{threading.get_ident()}.tmp"
        try:
            img.save(tmp, "WEBP", quality=THUMB_QUALITY)
            # Atomic on the same volume: a concurrent reader sees the old
            # state or the whole file, never half of one.
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


def ensure_thumbnail(filename: str, wait_seconds: float = None) -> str:
    """Path to the cached thumbnail for an upload, generating it if needed.

    Raises ValueError for a name that is not an upload, ThumbnailUnavailable
    for a missing or undecodable source, ThumbnailBusy when every generation
    slot stayed taken for `wait_seconds` (default GENERATE_WAIT_SECONDS).
    """
    if not UPLOAD_NAME.match(filename):
        raise ValueError(f"not an upload name: {filename!r}")
    target = thumb_path(filename)
    if os.path.isfile(target):
        return target

    source = os.path.join(uploads_dir(), filename)
    if not os.path.isfile(source):
        raise ThumbnailUnavailable(f"no such upload: {filename}")

    wait = GENERATE_WAIT_SECONDS if wait_seconds is None else wait_seconds
    if not _GENERATE_SLOTS.acquire(timeout=wait):
        raise ThumbnailBusy(filename)
    try:
        # Another request may have made it while this one waited for a slot.
        if os.path.isfile(target):
            return target
        try:
            render_thumbnail(source, target)
        except (OSError, UnidentifiedImageError, Image.DecompressionBombError, ValueError) as exc:
            logger.warning("Could not make a thumbnail for %s: %s", filename, exc)
            raise ThumbnailUnavailable(filename) from exc
        return target
    finally:
        _GENERATE_SLOTS.release()

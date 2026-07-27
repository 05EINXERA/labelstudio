"""Image outputs for an export — the second axis alongside the annotation format.

Three builders live here; the two colour-mask outputs are delegated to
`formats.masks` so there is one rasterizer, not two:

    original     the uploaded image, copied verbatim into the archive
    annotated    the image with the committed annotations drawn on it, the way
                 an annotator sees them (translucent fill + solid outline)
    mask_binary  an 8-bit grayscale PNG per image, annotated pixel = 255,
                 background = 0, every class collapsed to foreground

Every builder shares the export contract: `build(...) -> (entries, skipped)`
where an entry is an `(arcname, bytes)` pair and `skipped` reports tasks the
output could not represent. Arcnames here are *bare* (`<stem>.png`); the export
assembler prefixes each output's folder (`annotated_image/`, `mask_binary_color/`,
…). See api/routers/exports.py.

The "annotated" look is copied from the canvas renderer (frontend/js/canvas/
draw.js): a polygon fill at 50% alpha of the label colour, outlined 2px in the
solid label colour. It is a preview, not a pixel-exact reproduction of the
browser canvas — line joins and anti-aliasing differ — but it reads the same.
"""
import io
import logging
import os
from typing import List, Sequence, Tuple

from PIL import Image, ImageDraw

import models
from config import DATA_DIR
from formats.common import (
    hex_to_rgb,
    image_size,
    ordered_annotations,
    polygon_points,
    safe_stem,
)

logger = logging.getLogger(__name__)

# Matches the canvas fill alpha in draw.js (hexToRgba(color, 0.5)); the outline
# is the solid label colour at 2px, also from draw.js.
_FILL_ALPHA = 128  # 0.5 * 255
_OUTLINE_WIDTH = 2

# A binary mask paints every annotated pixel white regardless of class.
_FOREGROUND = 255


def _open_image(task: models.Task) -> Tuple[Image.Image, str]:
    """Open a task's source image, or raise. Returns (image, on-disk path)."""
    # image_path is stored relative to DATA_DIR as "uploads/<name>" with a
    # forward slash (see projects._save_upload).
    path = os.path.join(DATA_DIR, *task.image_path.split("/"))
    return Image.open(path), path


def _skip(name: str, reason: str, task_id) -> dict:
    logger.warning("Image export skipping task %s (%s): %s", task_id, name, reason)
    return {"filename": name, "reason": reason}


def build_original(tasks: Sequence[models.Task], labels: Sequence[models.Label],
                   db=None) -> Tuple[List[Tuple[str, bytes]], List[dict]]:
    """The uploaded image itself, copied byte-for-byte under its archive stem.

    Copied verbatim rather than re-encoded so the original stays exactly what
    was uploaded — re-encoding a JPEG would silently degrade it. The extension
    is preserved from the source path.
    """
    entries: List[Tuple[str, bytes]] = []
    skipped: List[dict] = []
    for task in tasks:
        name = task.description or f"task-{task.id}"
        if not task.image_path:
            skipped.append(_skip(name, "task has no image on file", task.id))
            continue
        path = os.path.join(DATA_DIR, *task.image_path.split("/"))
        ext = os.path.splitext(task.image_path)[1] or ".png"
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            skipped.append(_skip(name, "image file could not be read", task.id))
            logger.warning("Original export could not read %s: %s", path, exc)
            continue
        entries.append((f"{safe_stem(task)}{ext}", data))
    return entries, skipped


def build_annotated(tasks: Sequence[models.Task], labels: Sequence[models.Label],
                    db=None) -> Tuple[List[Tuple[str, bytes]], List[dict]]:
    """The image with its committed annotations drawn on, as PNG.

    PNG regardless of the source format: the overlay adds hard-edged lines that
    JPEG would ring around. A task whose image cannot be opened is skipped and
    reported rather than dropped.
    """
    labels_by_id = {l.id: l for l in labels}
    entries: List[Tuple[str, bytes]] = []
    skipped: List[dict] = []

    for task in tasks:
        name = task.description or f"task-{task.id}"
        if not task.image_path:
            skipped.append(_skip(name, "task has no image on file", task.id))
            continue
        try:
            source, _ = _open_image(task)
        except OSError as exc:
            skipped.append(_skip(name, "image file could not be read", task.id))
            logger.warning("Annotated export could not open %s: %s", task.image_path, exc)
            continue

        with source:
            base = source.convert("RGBA")
        # Draw onto a transparent overlay so the translucent fills composite
        # against the photo the way they do on the canvas, rather than blending
        # into each other where shapes overlap.
        overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        for ann in ordered_annotations(task):
            label = labels_by_id.get(ann.get("labelId"))
            polygon = polygon_points(ann)
            if polygon is None:
                continue
            r, g, b = hex_to_rgb(label.color if label else None)
            draw.polygon(polygon, fill=(r, g, b, _FILL_ALPHA),
                         outline=(r, g, b, 255), width=_OUTLINE_WIDTH)

        composed = Image.alpha_composite(base, overlay).convert("RGB")
        buf = io.BytesIO()
        composed.save(buf, format="PNG", optimize=True)
        entries.append((f"{safe_stem(task)}.png", buf.getvalue()))

    return entries, skipped


def build_binary(tasks: Sequence[models.Task], labels: Sequence[models.Label],
                 db=None) -> Tuple[List[Tuple[str, bytes]], List[dict]]:
    """One 8-bit grayscale mask per image: annotated pixel = 255, background = 0.

    All classes collapse to a single foreground value — a binary mask answers
    "annotated or not", not "which class" (use the colour masks for that). Mode
    "L" (single channel) is the interchange standard other tools expect.

    A task without image dimensions is skipped and reported: there is no canvas
    to size the mask to, exactly as in the colour-mask export.
    """
    entries: List[Tuple[str, bytes]] = []
    skipped: List[dict] = []

    for task in tasks:
        name = task.description or f"task-{task.id}"
        width, height = image_size(task, db=db, persist=db is not None)
        if not width or not height:
            skipped.append(_skip(
                name, "image dimensions are unknown, so no mask canvas can be sized", task.id))
            continue

        mask = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(mask)
        for ann in ordered_annotations(task):
            polygon = polygon_points(ann)
            if polygon is None:
                continue
            draw.polygon(polygon, fill=_FOREGROUND)

        buf = io.BytesIO()
        mask.save(buf, format="PNG", optimize=True)
        entries.append((f"{safe_stem(task)}.png", buf.getvalue()))

    return entries, skipped

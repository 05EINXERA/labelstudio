"""Rasterized segmentation masks — EXPORT ONLY.

Four outputs over two axes, matching the reference archive layout:

    semantic_segmentations/<stem>.png   pixel identifies the *class*
    instance_segmentations/<stem>.png   pixel identifies the *instance*

  - direct colour: an RGB image; the pixel is the colour itself
  - index colour:  a palette ("P" mode) image; the pixel is an index and the
                   palette maps it to a colour

Index 0 / black is background in every variant.

**Masks are never imported.** Contour-tracing a raster back to polygons is not
a faithful inverse — the traced contour has neither the vertex count nor the
vertex positions of the polygon that produced it — and the raster carries no
trustworthy class identity. This is a settled product decision; see
.devnotes/data-refactor/00_FORMAT_ANALYSIS.md § 8 before adding a parser.

Deliberate deviation from the reference: it writes **JPEG** for the
direct-colour variants, and that lossy compression destroys the exact class
colours the format exists to convey (sampling the reference files yields RGB
values like (3,0,0) and (14,0,32) where flat class colours should be). We emit
PNG for all four, so a consumer can actually read a class off the pixel.
"""
import io
import logging
from typing import List, Sequence, Tuple

import models
from PIL import Image, ImageDraw

from formats.common import (
    hex_to_rgb,
    image_size,
    ordered_annotations,
    polygon_points,
    safe_stem,
)

logger = logging.getLogger(__name__)

SEMANTIC_DIR = "semantic_segmentations"
INSTANCE_DIR = "instance_segmentations"

# Per-instance colours, cycled. This is ColorBrewer Set1 followed by Set2 —
# the exact palette the reference instance masks carry, read out of
# .devnotes/data-examples/.../mask_index_color/instance_segmentations/.
INSTANCE_PALETTE: List[Tuple[int, int, int]] = [
    (228, 26, 28), (55, 126, 184), (77, 175, 74), (152, 78, 163),
    (255, 127, 0), (255, 255, 51), (166, 86, 40), (247, 129, 191),
    (153, 153, 153), (102, 194, 165), (252, 141, 98), (141, 160, 203),
    (231, 138, 195), (166, 216, 84),
]

# A palette image addresses 256 entries; index 0 is background.
_MAX_PALETTE_ENTRIES = 255

# Rasterizing is the one genuinely expensive export, and it holds the single
# uvicorn worker (rule 9) for its whole duration.
#
# Measured on the reference data (5184x3888, 446 polygons per image): ~0.5 s
# per mask, almost all of it PNG encoding rather than drawing. Each task
# produces two masks, so the cost is ~1 s per task. 150 tasks is therefore a
# ~2.5 minute ceiling — long, but a bounded wait rather than the ~8 minutes
# a 500-task cap would have allowed.
#
# Raising this is not the right fix if it proves too tight: moving JOBS out of
# process is (see rule 9), which is separate work.
MAX_MASK_TASKS = 150


def _render(task: models.Task, labels_by_id: dict, width: int, height: int,
            *, instance: bool, indexed: bool) -> Tuple[Image.Image, int]:
    """One mask for one task, plus the count of instances it could not hold.

    The overflow count is non-zero only for indexed instance masks, where a
    palette addresses 255 entries — real annotation sets exceed that (the
    reference data has 446 shapes on one image), so it is a routine case, not
    a defensive branch.
    """
    if indexed:
        image = Image.new("P", (width, height), 0)
        palette: List[int] = [0, 0, 0]  # index 0 = background
    else:
        image = Image.new("RGB", (width, height), (0, 0, 0))
        palette = []

    draw = ImageDraw.Draw(image)

    # Class ordinals are stable across every task in the export, so index N
    # means the same class in every semantic mask.
    class_index = {label_id: i + 1 for i, label_id in enumerate(labels_by_id)}
    if indexed and not instance:
        for label_id in labels_by_id:
            palette.extend(hex_to_rgb(labels_by_id[label_id].color))

    instance_ordinal = 0
    overflow = 0
    for ann in ordered_annotations(task):
        label = labels_by_id.get(ann.get("labelId"))
        if not label:
            continue  # references a deleted label
        polygon = polygon_points(ann)
        if polygon is None:
            continue

        if instance:
            instance_ordinal += 1
            colour = INSTANCE_PALETTE[(instance_ordinal - 1) % len(INSTANCE_PALETTE)]
            if indexed:
                if instance_ordinal > _MAX_PALETTE_ENTRIES:
                    # A palette image cannot address more. Counted and reported
                    # by the caller rather than only logged: dropping shapes
                    # from an export must not be invisible.
                    overflow += 1
                    continue
                fill = instance_ordinal
                palette.extend(colour)
            else:
                fill = colour
        else:
            ordinal = class_index.get(label.id, 0)
            fill = ordinal if indexed else hex_to_rgb(label.color)

        draw.polygon(polygon, fill=fill)

    if indexed:
        # Pad to a full 256-entry palette; Pillow requires 768 values.
        palette.extend([0] * (768 - len(palette)))
        image.putpalette(palette[:768])
    return image, overflow


def _encode(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    # PNG for every variant, including direct colour — see the module
    # docstring for why we do not follow the reference's lossy JPEG.
    image.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def build(tasks: Sequence[models.Task], labels: Sequence[models.Label],
          indexed: bool, db=None) -> Tuple[List[Tuple[str, bytes]], List[dict]]:
    """Project -> (archive entries, skipped tasks).

    `indexed` selects palette masks over direct-colour ones. Both semantic and
    instance folders are always produced, mirroring the reference archive.

    A task whose image dimensions are unknown is skipped and reported: there is
    no canvas to render onto, and inventing a size would produce a mask that
    does not align with the image.
    """
    labels_by_id = {l.id: l for l in labels}
    entries: List[Tuple[str, bytes]] = []
    skipped: List[dict] = []

    for task in tasks:
        name = task.description or f"task-{task.id}"
        width, height = image_size(task, db=db, persist=db is not None)
        if not width or not height:
            skipped.append({
                "filename": name,
                "reason": "image dimensions are unknown, so no mask canvas can be sized",
            })
            logger.warning("Mask export skipping task %s (%s): no image dimensions", task.id, name)
            continue

        stem = safe_stem(task)
        for folder, is_instance in ((SEMANTIC_DIR, False), (INSTANCE_DIR, True)):
            image, overflow = _render(task, labels_by_id, width, height,
                                      instance=is_instance, indexed=indexed)
            entries.append((f"{folder}/{stem}.png", _encode(image)))
            if overflow:
                # Reported, not just logged: 446 shapes on one image is normal
                # in the reference data, so this fires on real projects and the
                # user has to know the instance mask is incomplete.
                skipped.append({
                    "filename": f"{folder}/{stem}.png",
                    "reason": (
                        f"{overflow} instance(s) beyond the {_MAX_PALETTE_ENTRIES}-entry "
                        "palette limit were omitted; use the direct-colour masks for "
                        "images with this many shapes"
                    ),
                })
                logger.warning(
                    "Task %s: %d instances exceeded the indexed mask's palette limit.",
                    task.id, overflow)

    return entries, skipped

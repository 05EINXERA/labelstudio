"""LabelMe JSON — parse only.

LabelMe (wkentaro/labelme) writes one JSON file per image:

    {"version": "5.5.0", "flags": {},
     "shapes": [{"label": "...", "points": [[x, y], ...], "group_id": null,
                 "shape_type": "polygon", "flags": {}, "mask": null}, ...],
     "imagePath": "P1000678.JPG", "imageData": null,
     "imageHeight": 1944, "imageWidth": 2592}

Import-only: nothing in this app exports LabelMe, so there is no builder here to
keep in agreement. The parse contract is the one `coco.parse` and
`annotations_json.parse` implement — {filename: [annotation, ...]} where each
annotation carries `labelName` for the caller to resolve to a project label id.

Three things distinguish it from the formats already handled and drive the
design below:

* **Points are `[[x, y], ...]`**, not the flat `[x1, y1, ...]` of the interop
  format nor `[{x, y}, ...]` as stored. They are converted here, so nothing
  downstream has to know LabelMe's nesting.
* **`shape_type` is a wider vocabulary than the canvas has.** Only `polygon` and
  `rectangle` have faithful equivalents; the rest are skipped rather than
  coerced (see `_points_for_shape`).
* **`imagePath` may carry a directory** ("../images/P1000678.JPG" is valid
  LabelMe). Only the basename is reported, because that is what the caller
  matches against `tasks.description`.

There is no colour in a LabelMe file — it derives shape colours from a hash of
the label name at display time — so `labelColor` is left unset and the caller
assigns from its palette.
"""
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Sequence
from uuid import uuid4

from formats.common import bbox_of, round2

logger = logging.getLogger(__name__)

# LabelMe shape_types that map onto a canvas annotation, and what they become.
# `rectangle` is stored as two opposite corners and expanded to four below.
_POLYGON_TYPES = ("polygon",)
_RECT_TYPES = ("rectangle",)

# Recognised but unsupported: the canvas has no circle/line/point primitive, and
# `mask` is a raster (the same reason a mask export cannot be re-imported — see
# the mask rejection in api/routers/imports.py).
_UNSUPPORTED_TYPES = ("circle", "line", "linestrip", "points", "point", "mask")


def looks_like_document(data) -> bool:
    """True for a LabelMe per-image document.

    Keys on `shapes` plus `imagePath`: `shapes` alone is too weak (it would
    catch unrelated documents), while COCO is identified by `images` +
    `annotations` and the native format is a list or has `annotations`, so
    there is no overlap with either.
    """
    if not isinstance(data, dict):
        return False
    return isinstance(data.get("shapes"), list) and "imagePath" in data


def _basename(path: str) -> str:
    """Filename from a LabelMe `imagePath`, which may include directories."""
    return (path or "").replace("\\", "/").split("/")[-1]


def _points_for_shape(shape_type: Optional[str], raw_points: Sequence) -> Optional[List[dict]]:
    """LabelMe `points` -> [{x, y}, ...], or None if the shape can't be imported.

    An absent `shape_type` is treated as a polygon: that is LabelMe's own
    default, and a point list with no type is a polygon in every file we have
    seen.
    """
    points = []
    for pair in raw_points:
        # A malformed entry loses one vertex, which would silently deform the
        # shape, so the whole shape is dropped instead.
        if not isinstance(pair, (list, tuple)) or len(pair) < 2:
            return None
        try:
            points.append({"x": round2(float(pair[0])), "y": round2(float(pair[1]))})
        except (TypeError, ValueError):
            return None

    kind = (shape_type or "polygon").lower()

    if kind in _RECT_TYPES:
        # LabelMe stores a rectangle as two opposite corners; the canvas stores
        # four. Derived from the bounding box rather than the two points
        # directly, so a rectangle dragged bottom-right to top-left (giving
        # corners in the reverse order) still comes out wound correctly.
        if len(points) < 2:
            return None
        x, y, w, h = bbox_of(points)
        return [
            {"x": round2(x), "y": round2(y)},
            {"x": round2(x + w), "y": round2(y)},
            {"x": round2(x + w), "y": round2(y + h)},
            {"x": round2(x), "y": round2(y + h)},
        ]

    if kind in _POLYGON_TYPES:
        # Two points is a line, not an area; the canvas cannot represent it.
        return points if len(points) >= 3 else None

    return None


def parse(data: dict) -> Dict[str, List[dict]]:
    """A LabelMe document -> {filename: [annotation, ...]}.

    `label` becomes the label name; the caller resolves names to this project's
    label ids, creating any that are missing. Names are used verbatim, so
    non-ASCII classes (the reference files use 健全箇所 / 汚れ1 / 汚れ2) survive
    as-is rather than being transliterated.

    A shape that cannot be represented is skipped and logged, never coerced
    into a different geometry — an imported annotation that silently changed
    shape is worse than one that is reported missing.
    """
    filename = _basename(data.get("imagePath"))
    if not filename:
        raise ValueError("LabelMe file has no 'imagePath', so it cannot be matched to a task.")

    out: Dict[str, List[dict]] = defaultdict(list)
    skipped: Dict[str, int] = defaultdict(int)

    for shape in data.get("shapes", []):
        if not isinstance(shape, dict):
            continue
        shape_type = shape.get("shape_type")
        points = _points_for_shape(shape_type, shape.get("points") or [])
        if not points:
            skipped[(shape_type or "polygon").lower()] += 1
            continue

        x, y, w, h = bbox_of(points)
        record = {
            "id": uuid4().hex,
            "labelName": shape.get("label") or "object",
            "points": points,
            "x": round2(x), "y": round2(y),
            "width": round2(w), "height": round2(h),
            "type": "bbox" if (shape_type or "").lower() in _RECT_TYPES else "polygon",
        }
        # LabelMe's group_id ties shapes of one object together; the canvas has
        # the same concept, so it is carried across when present.
        group_id = shape.get("group_id")
        if group_id is not None:
            record["group_id"] = str(group_id)
        out[filename].append(record)

    for kind, count in skipped.items():
        if kind in _UNSUPPORTED_TYPES:
            logger.warning(
                "Skipped %d LabelMe '%s' shape(s) in %r: the canvas has no "
                "equivalent primitive.", count, kind, filename,
            )
        else:
            logger.warning(
                "Skipped %d malformed LabelMe '%s' shape(s) in %r.",
                count, kind, filename,
            )

    return dict(out)

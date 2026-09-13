"""LabelMe format — build and parse.

The reference is LabelMe 5.x (`"version": "5.5.0"`), verified against the real
files in .devnotes/task-imports-new/sample/. One JSON document per image:

    {
      "version": "5.5.0",
      "flags": {},
      "shapes": [
        {"label": "...", "points": [[x, y], ...], "group_id": null,
         "description": "", "shape_type": "polygon", "flags": {}, "mask": null}
      ],
      "imagePath": "P1000676.JPG",
      "imageData": null,
      "imageHeight": 1944,
      "imageWidth": 2592
    }

Three properties make this the easiest format we import:

  - coordinates are **absolute pixels**, so unlike YOLO nothing has to be
    scaled by the matched task's image size and a task with unknown dimensions
    still imports correctly;
  - the image is named in the document itself (`imagePath`), so the existing
    filename task matching applies unchanged;
  - a dataset is a folder of these, which the importer's generic JSON walk
    already handles — a ZIP needs no special casing here.

Detection is structural, not version-gated: `looks_like` asks for a `shapes`
list plus an image key. Several tools (labelme forks, AnyLabeling) emit this
exact schema with a different or absent `version`, and gating on the string
would reject files we read perfectly.

Deliberately lossy, in both directions (see .devnotes/task-imports-new/fix/
02_DESIGN.md § 8): `group_id` multi-part instances, image- and shape-level
`flags`, and per-shape `description` have no equivalent here — they are dropped
on import and emitted empty on export. `imageData` is never embedded; pairing
this format with the "original image" output produces a dataset LabelMe opens
directly, without multiplying the export size with base64 copies of images the
archive already carries.

`point` and `mask` shapes cannot be represented (we have no keypoint type, and
a raster mask is export-only for the same reason masks are elsewhere). They are
skipped and **counted**, so the caller can report the loss rather than let it
pass silently.
"""
import json
import logging
import posixpath
import uuid
from typing import Dict, List, Optional, Sequence, Tuple

import models
from formats.common import (
    annotation_dicts,
    annotation_type_of,
    bbox_of,
    image_size,
    is_annotation,
    points_of,
    round2,
    safe_stem,
)

logger = logging.getLogger(__name__)

# The version we write. Matches the reference samples; LabelMe itself only
# reads this field for migration decisions between its own major versions.
VERSION = "5.5.0"

# LabelMe shape_type -> our annotation type. Shapes whose geometry we can hold
# faithfully enough to be worth keeping. `linestrip`/`line` become polygons:
# the vertices survive, the openness of the path does not.
_SHAPE_TYPES = {
    "polygon": "polygon",
    "rectangle": "bbox",
    "circle": "bbox",
    "linestrip": "polygon",
    "line": "polygon",
}

# `circle` needs its own geometry: its two points are the centre and a point on
# the circumference, so the enclosing box is derived from the radius. Treating
# them as opposite corners (as for a rectangle) produces a degenerate box —
# for a horizontal radius, one of zero height.
_CIRCLE = "circle"

# Shapes we recognise but cannot represent. Listed explicitly so they are
# counted and reported, rather than falling into the generic "malformed" bucket
# alongside a shape that is genuinely broken.
_UNSUPPORTED_TYPES = ("point", "mask")


def looks_like(data) -> bool:
    """True for a LabelMe document.

    Checked before the task-JSON fall-through in the importer's per-file
    dispatch. The `shapes` list is the payload itself and is a key no other
    format we accept uses, so this cannot collide with COCO (`images` +
    `annotations`), the task JSON (`tasks`, or `name` + `annotations`) or a
    class-set array.

    `imagePath` or `imageHeight` — either alone is enough. Older LabelMe files
    omit one or the other, and requiring both would reject them for no gain;
    requiring neither would match any dict that happens to carry a `shapes`
    key.
    """
    return (
        isinstance(data, dict)
        and isinstance(data.get("shapes"), list)
        and ("imagePath" in data or "imageHeight" in data)
    )


def _points_from_pairs(raw) -> List[dict]:
    """LabelMe's [[x, y], ...] -> our [{x, y}, ...].

    Returns [] for anything malformed. A partially-readable shape is rejected
    whole: half a polygon is a wrong shape drawn confidently, which is worse
    than a shape the caller is told was dropped.
    """
    if not isinstance(raw, list):
        return []
    points = []
    for pair in raw:
        if not isinstance(pair, (list, tuple)) or len(pair) != 2:
            return []
        x, y = pair
        if isinstance(x, bool) or isinstance(y, bool):
            return []  # bools are ints in Python; not coordinates
        if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            return []
        points.append({"x": round2(float(x)), "y": round2(float(y))})
    return points


def _rect_corners(points: Sequence[dict]) -> List[dict]:
    """The four corners of the axis-aligned box enclosing `points`.

    A LabelMe `rectangle` carries two opposite corners; our bbox annotations
    carry four points plus the x/y/width/height bound. Deriving from the
    enclosing box rather than the given pair also normalises a rectangle stored
    bottom-right-first, which LabelMe permits.
    """
    x, y, w, h = bbox_of(points)
    return [{"x": x, "y": y}, {"x": round2(x + w), "y": y},
            {"x": round2(x + w), "y": round2(y + h)}, {"x": x, "y": round2(y + h)}]


def _circle_corners(points: Sequence[dict]) -> List[dict]:
    """The four corners of the square enclosing a LabelMe circle.

    `points` is [centre, a point on the circumference]; the radius is the
    distance between them.
    """
    (cx, cy), (ex, ey) = (points[0]["x"], points[0]["y"]), (points[1]["x"], points[1]["y"])
    r = ((ex - cx) ** 2 + (ey - cy) ** 2) ** 0.5
    return [{"x": round2(cx - r), "y": round2(cy - r)},
            {"x": round2(cx + r), "y": round2(cy - r)},
            {"x": round2(cx + r), "y": round2(cy + r)},
            {"x": round2(cx - r), "y": round2(cy + r)}]


def _image_name(data: dict, source_name: Optional[str]) -> str:
    """The filename this document's annotations are keyed under.

    `imagePath` can carry directory components from either OS ("../img/a.jpg"),
    and only the base name can match a task's description.

    Falling back to the source file's own name matters inside a ZIP walk, where
    a document with no usable `imagePath` is still identifiable by its entry
    name — and the importer's stem matching resolves "P1000676.json" to a
    "P1000676.JPG" task anyway.
    """
    raw = data.get("imagePath")
    if isinstance(raw, str):
        base = posixpath.basename(raw.replace("\\", "/").strip())
        if base and base not in (".", ".."):
            return base
    if source_name:
        return posixpath.basename(source_name.replace("\\", "/"))
    return ""


def parse(data, source_name: Optional[str] = None) -> Tuple[Dict[str, List[dict]], List[str]]:
    """A LabelMe document -> ({filename: [annotation, ...]}, notes).

    Coordinates are absolute pixels and are passed through unscaled — no
    `normalized` flag, so the importer's apply step needs no image dimensions.

    The class is carried on `labelName` for the caller to resolve against the
    project's labels; LabelMe has no slug or colour concept, so neither
    `labelValue` nor `labelColor` is set and the palette assigns a colour to
    any class the import creates.

    `notes` describes shapes that were dropped, so the caller can surface the
    loss. A malformed shape is skipped rather than raised: one bad polygon must
    not cost the other eighteen.
    """
    if not looks_like(data):
        return {}, []

    name = _image_name(data, source_name)
    if not name:
        return {}, []

    annotations: List[dict] = []
    unsupported: Dict[str, int] = {}
    malformed = 0

    for shape in data.get("shapes", []):
        if not isinstance(shape, dict):
            malformed += 1
            continue

        raw_type = shape.get("shape_type")
        shape_type = raw_type if isinstance(raw_type, str) else None

        if shape_type in _UNSUPPORTED_TYPES:
            unsupported[shape_type] = unsupported.get(shape_type, 0) + 1
            continue

        points = _points_from_pairs(shape.get("points"))
        if len(points) < 2:
            malformed += 1
            continue

        # An absent or unrecognised shape_type is treated as a polygon when it
        # has the vertices for one, matching the lenient default the other
        # parsers take. Two points cannot be a polygon, so that falls to bbox.
        kind = _SHAPE_TYPES.get(shape_type) or ("polygon" if len(points) >= 3 else "bbox")

        if shape_type == _CIRCLE:
            points = _circle_corners(points)
        elif kind == "bbox":
            points = _rect_corners(points)

        x, y, w, h = bbox_of(points)
        annotations.append({
            "id": uuid.uuid4().hex,
            "labelName": (shape.get("label") or "").strip() or "object",
            "type": kind,
            "points": points,
            "x": round2(x), "y": round2(y),
            "width": round2(w), "height": round2(h),
        })

    notes = []
    for kind, count in sorted(unsupported.items()):
        notes.append(f"{count} {kind} shape{'s' if count != 1 else ''} skipped "
                     f"({kind} annotations are not supported)")
    if malformed:
        notes.append(f"{malformed} malformed shape{'s' if malformed != 1 else ''} skipped")

    return ({name: annotations} if annotations else {}), notes


def _shapes_of(task: models.Task, labels_by_id: dict) -> List[dict]:
    """One task's annotations as LabelMe shape objects."""
    shapes = []
    for ann in annotation_dicts(task):
        if not is_annotation(ann):
            continue
        label = labels_by_id.get(ann.get("labelId"))
        if not label:
            continue  # annotation references a deleted label

        points = points_of(ann)
        if len(points) < 2:
            continue

        if annotation_type_of(ann) == "bbox":
            # LabelMe's rectangle is two opposite corners, not four.
            x, y, w, h = bbox_of(points)
            pairs = [[round2(x), round2(y)], [round2(x + w), round2(y + h)]]
            shape_type = "rectangle"
        else:
            pairs = [[round2(p["x"]), round2(p["y"])] for p in points]
            shape_type = "polygon"

        shapes.append({
            # The display name, not the interop `value` slug: LabelMe has no
            # slug concept, it is what a human reads in the LabelMe UI, and it
            # is what re-import matches on.
            "label": label.name,
            "points": pairs,
            "group_id": None,
            "description": "",
            "shape_type": shape_type,
            "flags": {},
            "mask": None,
        })
    return shapes


def build(tasks: Sequence[models.Task], labels: Sequence[models.Label],
          db=None) -> Tuple[List[Tuple[str, bytes]], List[dict]]:
    """Every task as its own LabelMe JSON — bare arcnames, caller prefixes.

    `skipped` is always empty, and that is the point of difference from YOLO:
    coordinates here are absolute, so a task whose image dimensions are unknown
    is exported with null height/width rather than dropped. LabelMe tolerates
    that, and the geometry is still correct.

    Written with `ensure_ascii=False`. Real label sets are not ASCII — the
    reference samples are entirely Japanese — and escaping them to \\uXXXX
    would be valid JSON that no human can read and that diverges from what
    LabelMe itself writes.
    """
    labels_by_id = {l.id: l for l in labels}
    entries: List[Tuple[str, bytes]] = []

    for task in tasks:
        width, height = image_size(task, db=db, persist=db is not None)
        document = {
            "version": VERSION,
            "flags": {},
            "shapes": _shapes_of(task, labels_by_id),
            "imagePath": task.description or f"task-{task.id}",
            "imageData": None,
            # image_size reports (0, 0) for unknown; null is the honest answer
            # in a format that accepts it.
            "imageHeight": height or None,
            "imageWidth": width or None,
        }
        # Compact, matching the reference samples: LabelMe writes one line per
        # document. Indenting would put every coordinate pair of a 223-point
        # polygon on four lines of its own and multiply the archive size for
        # nothing — these files are read by tools, not by hand.
        body = json.dumps(document, ensure_ascii=False)
        entries.append((f"{safe_stem(task)}.json", body.encode("utf-8")))

    return entries, []

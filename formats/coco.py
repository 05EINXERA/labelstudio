"""COCO JSON — build and parse.

Matches the interop shape (verified against
.devnotes/data-examples/exports/fast-label/coco_option/coco_annotations.json),
which is standard COCO plus a few extensions: `color` on categories, and
`attributes` / `rotation` / `keypoints` on annotations.

Three fidelity bugs in the previous inline implementation are fixed here
(.devnotes/data-refactor/01_PLAN.md § 2):
  - `images` carried hard-coded width/height of 0
  - `area` was the bounding box's area, not the polygon's
  - annotations were missing num_keypoints/keypoints/attributes/rotation
"""
import json
import logging
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple
from uuid import uuid4

import models
from formats.common import (
    annotation_type_of,
    bbox_of,
    flatten_points,
    image_size,
    is_annotation,
    points_of,
    polygon_area,
    round2,
    unflatten_points,
    value_from_name,
    values_for_labels,
)

logger = logging.getLogger(__name__)

# Marker written into a COCO annotation's `attributes` so our own re-import can
# tell a box from a polygon. COCO has no shape-type concept — every annotation
# carries a `segmentation` regardless — so without this the bbox/polygon
# distinction is lost on every round trip through the format. Interop consumers
# ignore unknown keys inside `attributes`, and standard COCO tooling ignores
# `attributes` entirely, so this is inert everywhere else.
SHAPE_TYPE_KEY = "shapeType"


def build(tasks: Sequence[models.Task], labels: Sequence[models.Label], db=None) -> dict:
    """Project -> one COCO document.

    `db` is optional and only used to persist image dimensions recovered from
    disk; pass it from the export job (which owns a writable session), never
    from a GET handler.
    """
    values = values_for_labels(labels)
    categories = [
        {
            "id": i + 1,
            "name": label.name,
            # the interop format puts the `value` form here, not the display name.
            "supercategory": values[label.id],
            "color": label.color,
            "skeleton": [],
            "keypoints": [],
            "keypoint_colors": [],
        }
        for i, label in enumerate(labels)
    ]
    label_to_cat = {label.id: i + 1 for i, label in enumerate(labels)}

    images: List[dict] = []
    annotations: List[dict] = []
    ann_id = 1

    for image_id, task in enumerate(tasks, start=1):
        width, height = image_size(task, db=db, persist=db is not None)
        images.append({
            "id": image_id,
            "file_name": task.description or f"task-{task.id}",
            "width": width,
            "height": height,
        })

        for ann in _annotations_of(task):
            category_id = label_to_cat.get(ann.get("labelId"))
            if category_id is None:
                continue  # references a label that no longer exists
            points = points_of(ann)
            if len(points) < 2:
                continue
            x, y, w, h = bbox_of(points)
            annotations.append({
                "id": ann_id,
                "image_id": image_id,
                "category_id": category_id,
                "segmentation": [flatten_points(points)],
                "bbox": [round2(x), round2(y), round2(w), round2(h)],
                # The polygon's own area, not the bounding box's.
                "area": round(polygon_area(points), 4),
                "iscrowd": 0,
                "num_keypoints": 0,
                "keypoints": [],
                # An object in COCO annotations, but an array in the per-task
                # format. Do not unify them.
                "attributes": {SHAPE_TYPE_KEY: annotation_type_of(ann)},
                "rotation": ann.get("rotation", 0),
            })
            ann_id += 1

    return {"images": images, "categories": categories, "annotations": annotations}


def _annotations_of(task: models.Task) -> List[dict]:
    try:
        anns = json.loads(task.annotations) if task.annotations else []
    except (ValueError, TypeError) as exc:
        logger.warning("Task %s has unparseable annotations, skipping in export: %s", task.id, exc)
        return []
    if not isinstance(anns, list):
        return []
    return [a for a in anns if is_annotation(a)]


def parse(data: dict) -> Dict[str, List[dict]]:
    """COCO document -> {filename: [annotation, ...]}.

    Category name becomes the label name; the caller resolves names to this
    project's label ids, creating any that don't exist.

    Both `name` and `supercategory` are reported, because the interop COCO puts
    the `value` form in `supercategory` while its per-task JSON uses the display
    name. Importing both files from the same source project would otherwise
    create two labels for one class — the caller matches on either.
    """
    images_by_id = {img["id"]: img for img in data.get("images", []) if "id" in img}
    categories_by_id = {
        c["id"]: (c.get("name", "object"), c.get("color"), c.get("supercategory"))
        for c in data.get("categories", []) if "id" in c
    }

    out: Dict[str, List[dict]] = defaultdict(list)
    for ann in data.get("annotations", []):
        if not isinstance(ann, dict):
            continue
        img = images_by_id.get(ann.get("image_id"))
        if not img or not img.get("file_name"):
            continue
        label_name, label_color, label_value = categories_by_id.get(
            ann.get("category_id"), ("object", None, None)
        )

        seg = ann.get("segmentation")
        if seg and isinstance(seg, list) and seg and isinstance(seg[0], list):
            groups = seg
        elif ann.get("bbox"):
            groups = [None]  # sentinel: fall back to the bbox
        else:
            continue

        attributes = ann.get("attributes")
        shape_type = attributes.get(SHAPE_TYPE_KEY) if isinstance(attributes, dict) else None

        for group in groups:
            if group is None:
                bx, by, bw, bh = ann["bbox"]
                points = unflatten_points([bx, by, bx + bw, by, bx + bw, by + bh, bx, by + bh])
                # A bbox-only annotation is a box by construction.
                resolved_type = shape_type or "bbox"
            else:
                points = unflatten_points(group)
                resolved_type = shape_type
            if len(points) < 2:
                continue
            x, y, w, h = bbox_of(points)
            record = {
                "id": uuid4().hex,
                "labelName": label_name,
                "labelValue": label_value,
                "labelColor": label_color,
                "points": points,
                "x": round2(x), "y": round2(y),
                "width": round2(w), "height": round2(h),
            }
            if resolved_type:
                record["type"] = resolved_type
            rotation = ann.get("rotation")
            if rotation:
                record["rotation"] = rotation
            out[img["file_name"]].append(record)

    return dict(out)

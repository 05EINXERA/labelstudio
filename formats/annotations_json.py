"""Interop task JSON — build and parse.

One task object per image, carrying the image's dimensions, workflow state and
a flat annotation list. Two containers share the identical object shape
(verified against the reference exports in .devnotes/data-examples/):

  - `annotations_json`    a JSON **array** of task objects, one file
  - `annotations_pertask` a ZIP of one **object** per file, under jsons/

Because the array element and the per-task root are byte-identical, both
containers are built from the same `task_object()` — there is no second
serializer to keep in sync.

Points are flattened to [x1,y1,x2,y2,...] per interop convention, and label
info (title, value, color) is embedded in each annotation because the source
project's label ids mean nothing to an importer.
"""
import json
import logging
import uuid
from typing import Dict, List, Optional, Sequence, Tuple

import models
from formats.common import (
    annotation_type_of,
    bbox_of,
    flatten_points,
    image_size,
    is_annotation,
    points_of,
    round2,
    safe_stem,
    to_external_status,
    unflatten_points,
    values_for_labels,
)

logger = logging.getLogger(__name__)


def _annotations_of(task: models.Task) -> List[dict]:
    try:
        anns = json.loads(task.annotations) if task.annotations else []
    except (ValueError, TypeError) as exc:
        logger.warning("Task %s has unparseable annotations, skipping in export: %s", task.id, exc)
        return []
    if not isinstance(anns, list):
        return []
    return [a for a in anns if is_annotation(a)]


def task_object(task: models.Task, labels_by_id: dict, values: Optional[Dict[str, str]] = None,
                db=None) -> dict:
    """One task as an interop task-JSON object.

    `values` is the collision-free {label_id: value} map from
    values_for_labels; pass it so every task in an export agrees on the
    identifiers. Omitted, it is derived per label, which is correct but cannot
    see collisions across the project's full class set.
    """
    external_anns = []
    for i, ann in enumerate(_annotations_of(task), start=1):
        label = labels_by_id.get(ann.get("labelId"))
        if not label:
            continue  # annotation references a deleted label

        points = points_of(ann)
        value = values.get(label.id) if values else None
        if value is None:
            value = values_for_labels([label])[label.id]

        external_anns.append({
            "id": ann.get("id") or uuid.uuid4().hex,
            # The shape the annotation was actually drawn as, rather than a
            # hard-coded "polygon" that turned every box into one (gap G1).
            "type": annotation_type_of(ann),
            "title": label.name,
            "value": value,
            "color": label.color,
            "order": i,
            "attributes": [],
            "points": flatten_points(points),
            "rotation": ann.get("rotation", 0),
            "keypoints": [],
            "confidenceScore": ann.get("score", -1),
        })

    width, height = image_size(task, db=db, persist=db is not None)
    status, external_status = to_external_status(task.status)

    return {
        "id": str(task.id),
        "name": task.description or f"task-{task.id}",
        "status": status,
        "externalStatus": external_status,
        # The reference format carries a presigned URL to the hosted image.
        # We have no equivalent: a relative path would be a dead link outside
        # this server, so an empty string is the honest answer.
        "url": "",
        "width": width,
        "height": height,
        "secondsToAnnotate": task.time_spent or 0,
        # Assignment/review fields: only `assignee` is a real column here. The
        # reviewer/approver and external* pair are emitted empty rather than
        # dropped so the object stays shape-compatible with the reference for
        # consumers that index them; they are not workflow state we track.
        "assignee": task.assignee or "",
        "reviewer": "",
        "approver": "",
        "externalAssignee": "",
        "externalReviewer": "",
        "externalApprover": "",
        "tags": [],
        "metadatas": [],
        "relations": [],
        "createdAt": task.created_at.isoformat() if task.created_at else None,
        "updatedAt": task.updated_at.isoformat() if task.updated_at else None,
        "annotations": external_anns,
    }


def build_single(tasks: Sequence[models.Task], labels: Sequence[models.Label], db=None) -> str:
    """Every task as one JSON array — the `annotations_json` format."""
    labels_by_id = {l.id: l for l in labels}
    values = values_for_labels(labels)
    return json.dumps(
        [task_object(t, labels_by_id, values, db=db) for t in tasks], indent=2
    )


def build_entries(tasks: Sequence[models.Task], labels_by_id: dict,
                  values: Optional[Dict[str, str]] = None, db=None) -> List[Tuple[str, bytes]]:
    """One JSON file per task — the `annotations_pertask` archive's entries.

    Returns (arcname, content) pairs relative to this format's own folder; the
    caller prepends the prefix and owns the ZIP.
    """
    return [
        (f"{safe_stem(task)}.json",
         json.dumps(task_object(task, labels_by_id, values, db=db), indent=2).encode("utf-8"))
        for task in tasks
    ]


def parse(data) -> Dict[str, List[dict]]:
    """Task JSON -> {filename: [annotation, ...]}.

    Accepts every container this format ships in:
      1. a list of task objects          (the single-file export)
      2. {"tasks": [...]}                (a wrapped variant)
      3. one task object                 (a per-task file)

    Each annotation carries `labelId` in the source project, but those ids mean
    nothing here, so the class is resolved from `title` (display name) with
    `value` reported alongside for name-or-value matching.
    """
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and "tasks" in data:
        items = data["tasks"]
    elif isinstance(data, dict) and "name" in data and "annotations" in data:
        items = [data]
    else:
        items = []

    out: Dict[str, List[dict]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue

        anns = []
        for a in item.get("annotations", []):
            if not isinstance(a, dict):
                continue
            pts = a.get("points")
            if not pts:
                continue
            # The wire format flattens to [x1,y1,...]; the canvas's own
            # annotations are already {x, y} dicts.
            if isinstance(pts[0], (int, float)):
                points = unflatten_points(pts)
            else:
                points = [{"x": round2(p["x"]), "y": round2(p["y"])} for p in pts]
            if len(points) < 2:
                continue

            x, y, w, h = bbox_of(points)
            record = {
                "id": uuid.uuid4().hex,
                # Display name first; `value` is the fallback identifier.
                "labelName": a.get("title") or a.get("value") or a.get("labelName") or "object",
                "labelValue": a.get("value"),
                "labelColor": a.get("color"),
                "points": points,
                "x": round2(x), "y": round2(y),
                "width": round2(w), "height": round2(h),
            }
            # Keep the shape so a box does not come back as a polygon (gap G1).
            shape = a.get("type")
            if shape in ("bbox", "box"):
                record["type"] = "bbox"
            elif shape == "polygon":
                record["type"] = "polygon"
            if a.get("rotation"):
                record["rotation"] = a["rotation"]
            anns.append(record)

        if anns:
            out[name] = anns
    return out

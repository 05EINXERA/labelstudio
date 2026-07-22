"""Annotation import (tracker P4.2, G5).

Imports COCO-style JSON (the shape `frontend/js/export/coco.js` produces) or
the app's own per-task JSON export, matching images to existing tasks by
filename (`Task.description`). Tasks are matched, not created — an import
cannot add new images, since an image file itself is not part of either
export format; use the Tasks view's bulk upload for that first.

A dry-run preview (`/preview`) reports the match before anything is written,
because a failed match is silent and expensive to discover after the fact: an
annotation for "img_01.jpg" that does not match any task's description is
simply skipped, and the only way to know that happened is to have asked first.
"""
import json
import logging
import uuid
from collections import defaultdict
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session

import models
from database import get_db
from api.auth import get_current_user
from api.routers.projects import get_owned_project

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/imports", tags=["imports"], dependencies=[Depends(get_current_user)])


def _round(v: float) -> float:
    return round(v, 2)


def _points_from_bbox(bbox: List[float]) -> List[dict]:
    x, y, w, h = bbox
    return [
        {"x": _round(x), "y": _round(y)},
        {"x": _round(x + w), "y": _round(y)},
        {"x": _round(x + w), "y": _round(y + h)},
        {"x": _round(x), "y": _round(y + h)},
    ]


def _points_from_segmentation(seg: List[float]) -> List[dict]:
    pts = []
    for i in range(0, len(seg) - 1, 2):
        pts.append({"x": _round(seg[i]), "y": _round(seg[i + 1])})
    return pts


def _bbox_of(points: List[dict]) -> tuple:
    xs = [p["x"] for p in points]
    ys = [p["y"] for p in points]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def _parse_coco(data: dict) -> Dict[str, List[dict]]:
    """COCO-shaped `{images, categories, annotations}` -> {filename: [annotation, ...]}.

    Category name becomes the label name; the caller resolves label names to
    this project's label ids (creating any that don't exist, tracker P4.2).
    """
    images_by_id = {img["id"]: img for img in data.get("images", [])}
    categories_by_id = {c["id"]: c.get("name", "object") for c in data.get("categories", [])}

    out = defaultdict(list)
    for ann in data.get("annotations", []):
        img = images_by_id.get(ann.get("image_id"))
        if not img or not img.get("file_name"):
            continue
        label_name = categories_by_id.get(ann.get("category_id"), "object")
        seg = ann.get("segmentation")
        if seg and isinstance(seg, list) and seg and isinstance(seg[0], list):
            groups = seg
        elif ann.get("bbox"):
            groups = [None]  # sentinel: use bbox directly below
        else:
            continue

        for group in groups:
            points = _points_from_segmentation(group) if group is not None else _points_from_bbox(ann["bbox"])
            if len(points) < 2:
                continue
            x, y, w, h = _bbox_of(points)
            out[img["file_name"]].append({
                "id": uuid.uuid4().hex, "labelName": label_name,
                "points": points, "x": _round(x), "y": _round(y),
                "width": _round(w), "height": _round(h),
            })
    return out


def _parse_native(data) -> Dict[str, List[dict]]:
    """The app's own per-task export: a list of `{name, annotations: [...]}`.

    Each annotation already carries `labelId`; since the target project's
    label ids will not match the source project's, only `title`/`value` (the
    label's display name at export time) survive the round trip.
    """
    items = data if isinstance(data, list) else data.get("tasks", [])
    out = {}
    for item in items:
        name = item.get("name")
        if not name:
            continue
        anns = []
        for a in item.get("annotations", []):
            pts = a.get("points")
            if not pts:
                continue
            # buildExportAnnotation() flattens to [x1,y1,x2,y2,...]; the
            # canvas's own annotations are already {x,y} dicts.
            if pts and isinstance(pts[0], (int, float)):
                points = _points_from_segmentation(pts)
            else:
                points = [{"x": _round(p["x"]), "y": _round(p["y"])} for p in pts]
            if len(points) < 2:
                continue
            x, y, w, h = _bbox_of(points)
            label_name = a.get("value") or a.get("title") or a.get("labelName") or "object"
            anns.append({
                "id": uuid.uuid4().hex, "labelName": label_name,
                "points": points, "x": _round(x), "y": _round(y),
                "width": _round(w), "height": _round(h),
            })
        if anns:
            out[name] = anns
    return out


def _parse_import_file(filename: str, raw: bytes) -> Dict[str, List[dict]]:
    try:
        data = json.loads(raw.decode("utf-8-sig", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if isinstance(data, dict) and "images" in data and "annotations" in data:
        return _parse_coco(data)
    return _parse_native(data)


def _match_to_tasks(by_filename: Dict[str, List[dict]], project_id: int, db: Session):
    """Resolve each filename to a Task by exact `description` match.

    Matching by filename rather than any embedded id, since the imported file
    was very likely produced by a different system with no knowledge of this
    project's task ids.
    """
    tasks = db.query(models.Task).filter(models.Task.project_id == project_id).all()
    tasks_by_desc = {t.description: t for t in tasks if t.description}

    matched, unmatched = [], []
    for filename, anns in by_filename.items():
        task = tasks_by_desc.get(filename)
        if task:
            matched.append({"filename": filename, "task_id": task.id, "annotation_count": len(anns)})
        else:
            unmatched.append({"filename": filename, "annotation_count": len(anns)})
    return matched, unmatched


def _resolve_label_ids(by_filename: Dict[str, List[dict]], project_id: int, db: Session) -> Dict[str, str]:
    """Map label name (case-insensitive) -> label id, creating missing labels.

    Import must not silently drop annotations because their class doesn't
    exist yet in the target project; it creates the label instead, consistent
    with the Classes import behaviour in labels.py.
    """
    existing = {l.name.lower(): l.id for l in db.query(models.Label).filter(models.Label.project_id == project_id).all()}
    palette = ["#ef4444", "#f97316", "#eab308", "#22c55e", "#0f8b8d", "#3b82f6", "#8b5cf6", "#ec4899"]
    i = len(existing)
    for anns in by_filename.values():
        for a in anns:
            key = a["labelName"].lower()
            if key not in existing:
                new_label = models.Label(id=uuid.uuid4().hex, name=a["labelName"], color=palette[i % len(palette)], project_id=project_id)
                db.add(new_label)
                existing[key] = new_label.id
                i += 1
    return existing


@router.post("/annotations/preview")
async def preview_annotation_import(
    projectId: int = Query(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Report what an import would do, without writing anything."""
    get_owned_project(projectId, user, db)
    raw = await file.read()
    try:
        by_filename = _parse_import_file(file.filename or "", raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not by_filename:
        raise HTTPException(status_code=422, detail="No recognizable annotations found in the uploaded file.")

    matched, unmatched = _match_to_tasks(by_filename, projectId, db)
    label_names = sorted({a["labelName"] for anns in by_filename.values() for a in anns})
    existing_names = {l.name.lower() for l in db.query(models.Label).filter(models.Label.project_id == projectId).all()}
    new_labels = [n for n in label_names if n.lower() not in existing_names]

    return {
        "matched": matched,
        "unmatched": unmatched,
        "new_labels": new_labels,
        "total_annotations": sum(len(v) for v in by_filename.values()),
    }


@router.post("/annotations")
async def import_annotations(
    projectId: int = Query(...),
    mode: str = Query("merge", pattern="^(merge|replace)$"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Apply an annotation import. `merge` appends to each matched task's
    existing annotations; `replace` overwrites them.
    """
    get_owned_project(projectId, user, db)
    raw = await file.read()
    try:
        by_filename = _parse_import_file(file.filename or "", raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not by_filename:
        raise HTTPException(status_code=422, detail="No recognizable annotations found in the uploaded file.")

    label_ids = _resolve_label_ids(by_filename, projectId, db)
    matched, unmatched = _match_to_tasks(by_filename, projectId, db)
    tasks_by_id = {t.id: t for t in db.query(models.Task).filter(models.Task.project_id == projectId).all()}

    applied = 0
    for m in matched:
        task = tasks_by_id[m["task_id"]]
        anns = by_filename[m["filename"]]
        resolved = [
            {**{k: v for k, v in a.items() if k != "labelName"}, "labelId": label_ids[a["labelName"].lower()]}
            for a in anns
        ]

        if mode == "replace":
            existing_kept = []
        else:
            try:
                existing_kept = json.loads(task.annotations) if task.annotations else []
                if not isinstance(existing_kept, list):
                    existing_kept = []
            except (ValueError, TypeError) as exc:
                logger.warning("Task %s had unparseable annotations, replacing: %s", task.id, exc)
                existing_kept = []

        task.annotations = json.dumps(existing_kept + resolved)
        applied += 1

    db.commit()
    return {
        "status": "ok",
        "tasks_updated": applied,
        "annotations_imported": sum(m["annotation_count"] for m in matched),
        "unmatched": unmatched,
    }

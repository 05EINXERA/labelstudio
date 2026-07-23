"""Annotation import (tracker P4.2, G5).

Imports COCO-style JSON (the shape `frontend/js/export/coco.js` produces), the
app's own per-task JSON export, or a ZIP of either (the per-task export's
`jsons/<image>.json` archive), matching images to existing tasks by filename
(`Task.description`). Tasks are matched, not created — an import cannot add
new images, since an image file itself is not part of either export format;
use the Tasks view's bulk upload for that first.

The container is detected from the bytes, not the extension: `_parse_import_file`
routes ZIPs to `_parse_zip`, which unwraps entries and feeds each back through
the same per-file detection. Everything downstream works on the resulting
`{filename: [annotation, ...]}` dict and is format-agnostic.

A dry-run preview (`/preview`) reports the match before anything is written,
because a failed match is silent and expensive to discover after the fact: an
annotation for "img_01.jpg" that does not match any task's description is
simply skipped, and the only way to know that happened is to have asked first.
"""
import io
import json
import logging
import uuid
import zipfile
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

    `color` is not part of the COCO spec — it is an extension our own export
    emits (and FastLabel's). Carrying it through as `labelColor` is what makes
    an export/import round trip preserve class colors; a standard third-party
    COCO file has no `color` key and falls through to the palette in
    `_ensure_labels`, unchanged.
    """
    images_by_id = {img["id"]: img for img in data.get("images", [])}
    categories_by_id = {
        c["id"]: (c.get("name", "object"), c.get("color"))
        for c in data.get("categories", [])
    }

    out = defaultdict(list)
    for ann in data.get("annotations", []):
        img = images_by_id.get(ann.get("image_id"))
        if not img or not img.get("file_name"):
            continue
        label_name, label_color = categories_by_id.get(ann.get("category_id"), ("object", None))
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
                "labelColor": label_color,  # preserved for label creation
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
    # Handle three cases:
    # 1. A list of task objects: [{name, annotations}, ...]
    # 2. A dict with "tasks" key: {tasks: [{name, annotations}, ...]}
    # 3. A single task object (FastLabel per-task format): {name, annotations: [...]}
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict) and "tasks" in data:
        items = data["tasks"]
    elif isinstance(data, dict) and "name" in data and "annotations" in data:
        # Single per-task object - wrap in a list
        items = [data]
    else:
        items = []
    
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
            # Use title (display name) first, fallback to value/labelName
            label_name = a.get("title") or a.get("value") or a.get("labelName") or "object"
            anns.append({
                "id": uuid.uuid4().hex, 
                "labelName": label_name,
                "labelColor": a.get("color"),  # Preserve color for label creation
                "points": points, "x": _round(x), "y": _round(y),
                "width": _round(w), "height": _round(h),
            })
        if anns:
            out[name] = anns
    return out


# Zip-bomb guards. This endpoint takes an upload from any authenticated user,
# and an archive's uncompressed size is unbounded by its compressed size.
_ZIP_MAX_ENTRIES = 10_000        # a project writes one JSON per task
_ZIP_MAX_ENTRY_BYTES = 25 * 1024 * 1024
_ZIP_MAX_TOTAL_BYTES = 250 * 1024 * 1024


def _parse_single_json(raw: bytes) -> Dict[str, List[dict]]:
    """Dispatch one JSON document to the COCO or native parser."""
    try:
        data = json.loads(raw.decode("utf-8-sig", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc}") from exc

    if isinstance(data, dict) and "images" in data and "annotations" in data:
        return _parse_coco(data)
    return _parse_native(data)


def _parse_zip(raw: bytes) -> Dict[str, List[dict]]:
    """A ZIP export -> {filename: [annotation, ...]}, merging every entry.

    Reads `*.json` at any depth rather than only the `jsons/` folder the
    exporter writes today: the archive is deliberately multi-folder (a future
    export may add `coco/`), so binding the importer to one prefix would make
    it wrong as soon as that lands. Non-JSON entries are skipped, not errors —
    an archive bundling images is legitimate.

    Each entry is dispatched through the same per-file detection as a bare
    upload, so an archive can mix per-task files and a COCO file.

    One unreadable entry must not lose the rest of the archive; it is logged
    and skipped. An archive with nothing usable falls through to the caller's
    existing "no recognizable annotations" 422.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid ZIP archive: {exc}") from exc

    with zf:
        infos = [i for i in zf.infolist() if not i.is_dir()]
        if len(infos) > _ZIP_MAX_ENTRIES:
            raise ValueError(
                f"Archive has {len(infos)} files; the limit is {_ZIP_MAX_ENTRIES}."
            )
        total = sum(i.file_size for i in infos)
        if total > _ZIP_MAX_TOTAL_BYTES:
            raise ValueError(
                f"Archive expands to {total // (1024 * 1024)} MB; the limit is "
                f"{_ZIP_MAX_TOTAL_BYTES // (1024 * 1024)} MB."
            )

        merged: Dict[str, List[dict]] = defaultdict(list)
        for info in infos:
            name = info.filename
            if not name.lower().endswith(".json"):
                continue  # images and other bundled files are not annotations
            # Nothing is written to disk, but an archive with absolute or
            # traversing paths is malformed and its names are not trustworthy.
            if name.startswith("/") or ".." in name.replace("\\", "/").split("/"):
                logger.warning("Skipping unsafe archive entry %r", name)
                continue
            # Checked from the header, before decompressing.
            if info.file_size > _ZIP_MAX_ENTRY_BYTES:
                raise ValueError(
                    f"Archive entry '{name}' is {info.file_size // (1024 * 1024)} MB; "
                    f"the limit is {_ZIP_MAX_ENTRY_BYTES // (1024 * 1024)} MB per file."
                )
            try:
                entry_raw = zf.read(info)
            except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
                logger.warning("Could not read archive entry %r, skipping: %s", name, exc)
                continue
            try:
                parsed = _parse_single_json(entry_raw)
            except ValueError as exc:
                logger.warning("Archive entry %r is not valid JSON, skipping: %s", name, exc)
                continue
            # Concatenate rather than overwrite: two entries may legitimately
            # target the same image (per-task file plus a COCO file).
            for image_name, anns in parsed.items():
                merged[image_name].extend(anns)

    return dict(merged)


def _parse_import_file(filename: str, raw: bytes) -> Dict[str, List[dict]]:
    """Detect the container by content, not by filename.

    The extension is a hint the caller controls; the magic bytes are not. A
    ZIP uploaded as `.json` (or the reverse) still imports correctly.
    """
    if raw[:4] == b"PK\x03\x04":
        return _parse_zip(raw)
    return _parse_single_json(raw)


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
    
    Uses the color from the annotation if provided, otherwise falls back to palette.
    """
    existing = {l.name.lower(): l.id for l in db.query(models.Label).filter(models.Label.project_id == project_id).all()}
    palette = ["#ef4444", "#f97316", "#eab308", "#22c55e", "#0f8b8d", "#3b82f6", "#8b5cf6", "#ec4899"]
    i = len(existing)
    
    # Track which labels we've seen (first occurrence wins for color)
    labels_to_create = {}
    
    for anns in by_filename.values():
        for a in anns:
            key = a["labelName"].lower()
            if key not in existing and key not in labels_to_create:
                # Use annotation color if provided, otherwise use palette
                color = a.get("labelColor") or palette[i % len(palette)]
                labels_to_create[key] = {
                    "name": a["labelName"],
                    "color": color
                }
                i += 1
    
    # Create all new labels
    for key, label_data in labels_to_create.items():
        new_label = models.Label(
            id=uuid.uuid4().hex, 
            name=label_data["name"], 
            color=label_data["color"], 
            project_id=project_id
        )
        db.add(new_label)
        existing[key] = new_label.id
    
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
            {**{k: v for k, v in a.items() if k not in ("labelName", "labelColor")}, "labelId": label_ids[a["labelName"].lower()]}
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

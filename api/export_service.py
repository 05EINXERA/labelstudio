"""Building an export: the work behind `POST /api/exports`, without the HTTP.

Moved out of `api/routers/exports.py` so the heavy-job worker process
(`jobs/worker.py`) can run it without importing a router, FastAPI's auth
stack, or anything that loads ML models. The router keeps request validation,
the job table and the download; this module turns (project, request) into
bytes. See .devnotes/fix-exports-imports/04_PLAN.md Phase 2.

An export is two independent axes bundled into one ZIP:

- an annotation FORMAT: COCO, task JSON (single array or per-task), YOLO
  segmentation, or LabelMe per-image JSON (plus legacy CSV);
- an IMAGE OUTPUT: none, the original image, the annotated image, or a mask
  (direct / index colour / binary).

Each axis lives in its own named top-level folder (`coco/`, `yolo/`,
`mask_direct_color/`, `annotated_image/`, …) so the two never collide.

Archive assembly:
- `_format_entries` / `_image_entries` turn each axis into bare (arcname,
  bytes) pairs plus a `skipped` list of tasks it could not represent;
- `_prefixed` namespaces one axis's bare arcnames under its folder;
- `_zip_entries` packs the merged, fully-qualified entries into the ZIP.

Two carve-outs skip the ZIP entirely for backward compatibility: CSV, and a
single-file annotation format (COCO / annotations_json) with image output
"none". Both stay a bare download.
"""
import csv
import io
import json
import os
import zipfile
from typing import List, Tuple

from sqlalchemy.orm import Session

import models
from formats import annotations_json
from formats import coco as coco_format
from formats import images as images_format
from formats import labelme as labelme_format
from formats import masks as masks_format
from formats import yolo as yolo_format
from formats.common import (
    annotation_dicts,
    archive_name,
    points_of,
    round2,
    values_for_labels,
)
from schemas import ExportRequest, resolve_export_request

# Compact separators for the single-file JSON bodies. Only the whitespace
# between tokens changes: every key, string and number is serialised exactly
# as before, so `json.loads` of the new file equals `json.loads` of the old one
# (tests/test_export_service.py pins that). On the 1 M-vertex task that stalled
# production it is 17.3 MB instead of 41.6 MB, and 0.37 s instead of 0.97 s.
COMPACT = (",", ":")

# The per-task JSON archive entries live in formats/annotations_json.py.
_entries_pertask = annotations_json.build_entries


# --- Two-axis archive layout ------------------------------------------------
#
# Adding an axis value is a row in one of these maps plus a builder returning
# (bare-arcname, bytes) pairs; the folder prefix is applied here, once.

# Annotation-format axis -> folder prefix. Each builder follows the
# `build(tasks, labels, db) -> (entries, skipped)` contract, entries bare.
FORMAT_FOLDERS = {
    "coco": "coco/",
    "annotations_json": "json/",
    "annotations_pertask": "jsons/",
    "yolo": "yolo/",
    "labelme": "labelme/",
}

# Image-output axis -> folder prefix. "none" has no folder.
IMAGE_FOLDERS = {
    "original": "original_image/",
    "annotated": "annotated_image/",
    "mask_direct": "mask_direct_color/",
    "mask_index": "mask_index_color/",
    "mask_binary": "mask_binary_color/",
}


def _zip_entries(entries: List[Tuple[str, bytes]]) -> bytes:
    """Pack (arcname, content) pairs into an archive, as given.

    No prefix is applied: a format that owns its whole directory layout (YOLO's
    root classes.txt plus annotations/) supplies complete arcnames. Duplicates
    are suffixed rather than overwritten, since two tasks can legitimately
    share an image name.
    """
    buf = io.BytesIO()
    seen = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, content in entries:
            if arcname in seen:
                stem, ext = os.path.splitext(arcname)
                n = 2
                candidate = f"{stem}-{n}{ext}"
                while candidate in seen:
                    n += 1
                    candidate = f"{stem}-{n}{ext}"
                arcname = candidate
            seen.add(arcname)
            zf.writestr(arcname, content)
    return buf.getvalue()


def _prefixed(entries: List[Tuple[str, bytes]], prefix: str) -> List[Tuple[str, bytes]]:
    """Namespace a builder's bare arcnames under one folder, de-duplicating.

    Two tasks can share an image name, so a genuine collision inside the folder
    is suffixed rather than overwritten. Different folders never collide because
    the prefix makes the full arcname unique.
    """
    out: List[Tuple[str, bytes]] = []
    seen: set = set()
    for name, content in entries:
        arcname = f"{prefix}{name}"
        if arcname in seen:
            stem, ext = os.path.splitext(arcname)
            n = 2
            candidate = f"{stem}-{n}{ext}"
            while candidate in seen:
                n += 1
                candidate = f"{stem}-{n}{ext}"
            arcname = candidate
        seen.add(arcname)
        out.append((arcname, content))
    return out


def _format_entries(fmt: str, tasks, labels, values, db) -> Tuple[List[Tuple[str, bytes]], List[dict]]:
    """Annotation-format axis -> (bare entries, skipped), before folder prefix.

    A single-file format (COCO, annotations_json) yields exactly one entry named
    `annotations.json`; the multi-file formats yield their own layout inside the
    folder (YOLO's classes.txt + annotations/, the per-task jsons).
    """
    if fmt == "coco":
        body = json.dumps(coco_format.build(tasks, labels, db=db), separators=COMPACT)
        return [("annotations.json", body.encode("utf-8"))], []
    if fmt == "annotations_json":
        body = annotations_json.build_single(tasks, labels, db=db)
        raw = body.encode("utf-8") if isinstance(body, str) else body
        return [("annotations.json", raw)], []
    if fmt == "annotations_pertask":
        entries = list(_entries_pertask(tasks, {l.id: l for l in labels}, values=values, db=db))
        norm = [(n, c.encode("utf-8") if isinstance(c, str) else c) for n, c in entries]
        return norm, []
    if fmt == "yolo":
        entries, skipped = yolo_format.build(tasks, labels, db=db)
        norm = [(n, c.encode("utf-8") if isinstance(c, str) else c) for n, c in entries]
        return norm, skipped
    if fmt == "labelme":
        # Unlike YOLO, `skipped` is always empty: LabelMe coordinates are
        # absolute, so a task with unknown image dimensions still exports
        # correctly (with null height/width) rather than being dropped.
        entries, skipped = labelme_format.build(tasks, labels, db=db)
        norm = [(n, c.encode("utf-8") if isinstance(c, str) else c) for n, c in entries]
        return norm, skipped
    raise ValueError(f"Unknown export format {fmt!r}.")


def _image_entries(image_output: str, tasks, labels, db) -> Tuple[List[Tuple[str, bytes]], List[dict]]:
    """Image-output axis -> (bare entries, skipped), before folder prefix."""
    if image_output == "original":
        return images_format.build_original(tasks, labels, db=db)
    if image_output == "annotated":
        return images_format.build_annotated(tasks, labels, db=db)
    if image_output == "mask_binary":
        return images_format.build_binary(tasks, labels, db=db)
    if image_output in ("mask_direct", "mask_index"):
        return masks_format.build(tasks, labels, indexed=image_output == "mask_index", db=db)
    raise ValueError(f"Unknown image output {image_output!r}.")


def _build_csv(tasks: List[models.Task], labels_by_id: dict) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["image", "label", "x", "y", "width", "height", "status"])
    for task in tasks:
        anns = annotation_dicts(task)
        for ann in anns:
            if not isinstance(ann, dict) or ann.get("type") == "comment":
                continue
            label = labels_by_id.get(ann.get("labelId"))
            points = points_of(ann)
            xs = [p["x"] for p in points]
            ys = [p["y"] for p in points]
            writer.writerow([
                task.description or f"task-{task.id}",
                label.name if label else "unknown",
                round2(min(xs)), round2(min(ys)),
                round2(max(xs) - min(xs)), round2(max(ys) - min(ys)),
                task.status or "New",
            ])
    return buf.getvalue()


def build_export(db: Session, req: ExportRequest, project_id: int) -> Tuple[bytes, dict]:
    """Build one export. Returns (body, meta).

    `meta` carries what the status and download endpoints report:
    media_type, filename, task_count, format, image_output, skipped.

    Transaction discipline (02_ISSUES.md I-7): everything the builders read is
    loaded first, then the transaction is ended *before* the CPU phase, so a
    long export never holds a Postgres snapshot ("idle in transaction") for its
    whole run. The session must be created with `expire_on_commit=False` so the
    loaded objects stay usable after that commit. Image dimensions the builders
    recover from disk are set on the loaded tasks and written by the caller's
    final commit, a short transaction of its own.
    """
    # Resolve deprecated single-axis codes (json, masks_index, …) into the
    # canonical (format, imageOutput) pair.
    fmt, image_output = resolve_export_request(req.format, req.imageOutput)

    project = db.query(models.Project).filter(models.Project.id == project_id).first()
    query = db.query(models.Task).filter(models.Task.project_id == project_id)
    if req.statusFilter:
        query = query.filter(models.Task.status.in_(req.statusFilter))
    tasks = query.all()   # annotation_rows arrive with them (lazy="selectin")
    labels = db.query(models.Label).filter(models.Label.project_id == project_id).all()
    labels_by_id = {l.id: l for l in labels}
    project_archive_name = archive_name(project) if project else f"export-{project_id}.zip"

    # End the read transaction before the CPU-bound work.
    db.commit()

    # One collision-free {label_id: value} map for the whole export, so every
    # format in it agrees on the class identifiers.
    values = values_for_labels(labels)

    meta = {"task_count": len(tasks), "format": fmt, "image_output": image_output, "skipped": []}

    # CSV is a legacy single-file format with no folder and no image axis.
    if fmt == "csv":
        body = _build_csv(tasks, labels_by_id).encode("utf-8")
        meta.update(media_type="text/csv", filename=f"export-{project_id}.csv")
        return body, meta

    # Compat carve-out: a single-file annotation format with no image output
    # stays a bare .json download (no folder wrapper), preserving the long-
    # standing behaviour and the clients that depend on it.
    if image_output == "none" and fmt in ("coco", "annotations_json"):
        if fmt == "coco":
            text = json.dumps(coco_format.build(tasks, labels, db=db), separators=COMPACT)
        else:
            text = annotations_json.build_single(tasks, labels, db=db)
        body = text.encode("utf-8") if isinstance(text, str) else text
        meta.update(media_type="application/json", filename=f"export-{project_id}.json")
        return body, meta

    # General case: one ZIP, each axis in its own top-level folder.
    entries: List[Tuple[str, bytes]] = []
    skipped: List[dict] = []

    fmt_entries, fmt_skipped = _format_entries(fmt, tasks, labels, values, db)
    entries += _prefixed(fmt_entries, FORMAT_FOLDERS[fmt])
    skipped += fmt_skipped

    if image_output != "none":
        img_entries, img_skipped = _image_entries(image_output, tasks, labels, db)
        entries += _prefixed(img_entries, IMAGE_FOLDERS[image_output])
        skipped += img_skipped

    meta.update(media_type="application/zip", filename=project_archive_name, skipped=skipped)
    return _zip_entries(entries), meta

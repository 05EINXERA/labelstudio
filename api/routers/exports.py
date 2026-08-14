"""Annotation export (tracker P4.4, G6).

An export is two independent axes bundled into one ZIP:

- an annotation FORMAT — COCO, task JSON (single array or per-task), or YOLO
  segmentation (plus legacy CSV);
- an IMAGE OUTPUT — none, the original image, the annotated image, or a mask
  (direct / index colour / binary).

Each axis lives in its own named top-level folder (`coco/`, `yolo/`,
`mask_direct_color/`, `annotated_image/`, …) so the two never collide. Format
logic lives in `formats/` (see ARCHITECTURE.md § 2.1); this module owns request
validation, the job queue, the download handler, and archive assembly.

Archive assembly:
- `_format_entries` / `_image_entries` — turn each axis into bare (arcname,
  bytes) pairs plus a `skipped` list of tasks it could not represent;
- `_prefixed` — namespaces one axis's bare arcnames under its folder;
- `_zip_entries` — packs the merged, fully-qualified entries into the ZIP.

The finished archive is named `<project>-<short-random>.zip`. Two carve-outs
skip the ZIP entirely for backward compatibility: CSV, and a single-file
annotation format (COCO / annotations_json) with image output "none", both stay
a bare download.

A builder's `skipped` list (YOLO and the masks need image dimensions) is
threaded through the job status to the UI so a short export is never silent.

Uses the in-process background-job pattern from detect.py (JOBS dict +
BackgroundTasks). Rasterizing images is the slow path, so the job plumbing
earns its keep; the task cap (checked before the job starts) keeps a request
from holding the worker for minutes.

Rule 9 applies: this JOBS dict is in-process state, same constraint as
detect.py's — the app must stay a single uvicorn worker.

Deprecated single-axis codes (`json` → coco, `pertask` → annotations_pertask,
`masks_direct`/`masks_index` → coco + that mask image output) resolve in
`resolve_export_request`; see GOTCHAS.md § 16.

Not implemented (left as an explicit rejection, not a silent no-op):
- format=pascal_voc
"""
import csv
import io
import json
import logging
import os
import traceback
import uuid
import zipfile
from typing import List, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session, selectinload

import models
from database import get_db, SessionLocal
from schemas import (
    ExportRequest,
    EXPORT_FORMATS,
    EXPORT_INCLUDE_OPTIONS,
    IMAGE_OUTPUTS,
    TASK_STATUSES,
    resolve_export_request,
)
from api.auth import get_current_user, require_csrf, get_current_annotator
from api.routers.projects import get_owned_project
from formats import annotations_json
from formats import coco as coco_format
from formats import images as images_format
from formats import masks as masks_format
from formats import yolo as yolo_format
from formats.common import archive_name, points_of, round2, values_for_labels

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/exports", tags=["exports"], dependencies=[Depends(get_current_user), Depends(require_csrf)])

JOBS = {}


# The legacy CSV builder still reads through these short aliases; the shared
# helpers themselves live in formats/common.py.
_round = round2
_points_of = points_of

# The per-task JSON archive entries live in formats/annotations_json.py.
_entries_pertask = annotations_json.build_entries


# --- Two-axis archive layout ------------------------------------------------
#
# An export bundles up to two axes into one ZIP, each in its own named
# top-level folder so the two never collide:
#
#   <format-folder>/     the annotation format's files    (json/, yolo/, ...)
#   <image-folder>/      the image output's files         (original_image/, ...)
#
# Adding an axis value is a row in one of these maps plus a builder returning
# (bare-arcname, bytes) pairs; the folder prefix is applied here, once.

# Annotation-format axis -> (folder prefix, builder). Each builder follows the
# `build(tasks, labels, db) -> (entries, skipped)` contract, entries bare.
FORMAT_FOLDERS = {
    "coco": "coco/",
    "annotations_json": "json/",
    "annotations_pertask": "jsons/",
    "yolo": "yolo/",
}

# Image-output axis -> (folder prefix, builder). "none" has no folder.
IMAGE_FOLDERS = {
    "original": "original_image/",
    "annotated": "annotated_image/",
    "mask_direct": "mask_direct_color/",
    "mask_index": "mask_index_color/",
    "mask_binary": "mask_binary_color/",
}


def _zip_entries(entries: List[Tuple[str, bytes]]) -> bytes:
    """Pack (arcname, content) pairs into an archive, as given.

    Unlike `_build_zip`, no prefix is applied: a format that owns its whole
    directory layout (YOLO's root classes.txt plus annotations/) supplies
    complete arcnames. Duplicates are suffixed rather than overwritten, since
    two tasks can legitimately share an image name.
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
        body = json.dumps(coco_format.build(tasks, labels, db=db), indent=2)
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
        anns = task.annotations
        for ann in anns:
            if ann.type == "comment":
                continue
            label = labels_by_id.get(ann.label_id)
            points_list = []
            if ann.points:
                try: points_list = json.loads(ann.points)
                except ValueError: pass
            ann_dict = {"points": points_list, "x": ann.x, "y": ann.y, "width": ann.width, "height": ann.height}
            points = _points_of(ann_dict)
            xs = [p["x"] for p in points]
            ys = [p["y"] for p in points]
            writer.writerow([
                task.description or f"task-{task.id}",
                label.name if label else "unknown",
                _round(min(xs)), _round(min(ys)),
                _round(max(xs) - min(xs)), _round(max(ys) - min(ys)),
                task.status or "New",
            ])
    return buf.getvalue()


def _run_export_job(job_id: str, req: ExportRequest, project_id: int):
    db = SessionLocal()
    try:
        # Resolve deprecated single-axis codes (json, masks_index, …) into the
        # canonical (format, imageOutput) pair. The field validator that used
        # to do this is gone; resolution spans two fields now, so it lives here.
        fmt, image_output = resolve_export_request(req.format, req.imageOutput)

        project = db.query(models.Project).filter(models.Project.id == project_id).first()

        query = db.query(models.Task).filter(models.Task.project_id == project_id)
        if req.statusFilter:
            query = query.filter(models.Task.status.in_(req.statusFilter))
        tasks = query.options(selectinload(models.Task.annotations)).all()
        labels = db.query(models.Label).filter(models.Label.project_id == project_id).all()
        labels_by_id = {l.id: l for l in labels}

        # One collision-free {label_id: value} map for the whole export, so
        # every format in it agrees on the class identifiers.
        values = values_for_labels(labels)

        # `db` is passed to the builders so image dimensions recovered from disk
        # are written back to the Task — this is a POST-initiated background job
        # with its own session, not a GET handler (rule 4).

        # CSV is a legacy single-file format with no folder and no image axis.
        if fmt == "csv":
            body = _build_csv(tasks, labels_by_id)
            db.commit()
            JOBS[job_id] = {
                "status": "completed", "body": body, "media_type": "text/csv",
                "filename": f"export-{project_id}.csv", "task_count": len(tasks),
                "format": fmt, "image_output": image_output, "skipped": [],
            }
            return

        # Compat carve-out: a single-file annotation format with no image output
        # stays a bare .json download (no folder wrapper), preserving the long-
        # standing behaviour and the clients that depend on it.
        if image_output == "none" and fmt in ("coco", "annotations_json"):
            if fmt == "coco":
                body = json.dumps(coco_format.build(tasks, labels, db=db), indent=2)
            else:
                body = annotations_json.build_single(tasks, labels, db=db)
            db.commit()
            JOBS[job_id] = {
                "status": "completed", "body": body, "media_type": "application/json",
                "filename": f"export-{project_id}.json", "task_count": len(tasks),
                "format": fmt, "image_output": image_output, "skipped": [],
            }
            return

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

        body = _zip_entries(entries)
        db.commit()

        JOBS[job_id] = {
            "status": "completed", "body": body, "media_type": "application/zip",
            "filename": archive_name(project) if project else f"export-{project_id}.zip",
            "task_count": len(tasks), "format": fmt,
            "image_output": image_output, "skipped": skipped,
        }
    except Exception:
        traceback.print_exc()
        JOBS[job_id] = {"status": "failed", "error": "Export failed."}
    finally:
        db.close()


@router.post("")
def create_export(req: ExportRequest, background_tasks: BackgroundTasks, db: Session = Depends(get_db), user: models.User = Depends(get_current_user), annotator: Optional[models.TeamMember] = Depends(get_current_annotator)):
    get_owned_project(req.projectId, user, db, annotator)

    # Resolve deprecated single-axis codes before validating, so an old client
    # sending format=masks_index or format=json still passes.
    fmt, image_output = resolve_export_request(req.format, req.imageOutput)

    if fmt not in EXPORT_FORMATS:
        raise HTTPException(status_code=422, detail=f"format must be one of {EXPORT_FORMATS}.")
    if image_output not in IMAGE_OUTPUTS:
        raise HTTPException(status_code=422, detail=f"imageOutput must be one of {IMAGE_OUTPUTS}.")
    if req.include not in EXPORT_INCLUDE_OPTIONS:
        raise HTTPException(
            status_code=422,
            detail=f"include='{req.include}' is not implemented yet. Supported: {EXPORT_INCLUDE_OPTIONS}. "
                   "Mask rendering and image bundling are tracked but not built (see REFACTOR_MANAGEMENT.md Phase 4).",
        )
    if req.statusFilter:
        bad = [s for s in req.statusFilter if s not in TASK_STATUSES]
        if bad:
            raise HTTPException(status_code=422, detail=f"Unknown status filter values: {bad}. Valid: {TASK_STATUSES}.")

    # Any raster image output is the slow path that holds the single worker
    # (rule 9): colour masks emit two full-size PNGs per task, and annotated/
    # binary one each. Cap on the resolved image output, not the format code.
    if image_output in ("mask_direct", "mask_index", "mask_binary", "annotated", "original"):
        # Counted before the job starts so an oversized request fails fast
        # rather than holding the worker for minutes and looking like a hang.
        count_query = db.query(models.Task).filter(models.Task.project_id == req.projectId)
        if req.statusFilter:
            count_query = count_query.filter(models.Task.status.in_(req.statusFilter))
        task_count = count_query.count()
        if task_count > masks_format.MAX_MASK_TASKS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Image output is limited to {masks_format.MAX_MASK_TASKS} tasks per "
                    f"request; this project has {task_count}. Narrow the status filter "
                    "and export in batches."
                ),
            )

    job_id = str(uuid.uuid4())
    JOBS[job_id] = {"status": "pending"}
    background_tasks.add_task(_run_export_job, job_id, req, req.projectId)
    return {"job_id": job_id}


@router.get("/{job_id}")
def get_export_status(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Export job not found or expired")
    if job["status"] == "completed":
        return {
            "status": "completed",
            "task_count": job["task_count"],
            "format": job["format"],
            "image_output": job.get("image_output", "none"),
            # Tasks a format could not represent (YOLO without image
            # dimensions, for example). Reported so a short export is visible
            # rather than silently missing files.
            "skipped": job.get("skipped", []),
        }
    if job["status"] == "failed":
        return {"status": "failed", "error": job["error"]}
    return {"status": "pending"}


@router.get("/{job_id}/download")
def download_export(job_id: str):
    job = JOBS.get(job_id)
    if not job or job["status"] != "completed":
        raise HTTPException(status_code=404, detail="Export not ready or expired")
    body = job["body"]
    media_type = job["media_type"]
    filename = job["filename"]
    del JOBS[job_id]  # one-shot download, consistent with detect.py's job cleanup
    # Response, not PlainTextResponse: the per-task format is a binary ZIP that
    # a text response would UTF-8 encode and corrupt. Response takes str or
    # bytes, so the CSV and COCO branches are unaffected.
    return Response(
        content=body, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

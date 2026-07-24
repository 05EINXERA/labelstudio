"""Annotation import (tracker P4.2, G5).

Imports COCO JSON, the task-JSON formats (single array or per-task), a YOLO
segmentation archive, or a ZIP of the JSON formats — matching images to
existing tasks by filename (`Task.description`), or by stem where the
extensions cannot line up (a YOLO `P1000015.txt` against a `P1000015.JPG`
task). Tasks are matched, not created: an image file is not part of any of
these formats, so use the Tasks view's bulk upload first.

Masks are export-only and rejected with a message that says so — a raster mask
cannot be traced back to the original polygons faithfully. A class-set file
(label definitions, no geometry) is redirected to Classes -> Import.

The container is detected from the bytes, not the extension: `_parse_import_file`
routes ZIPs to `_parse_zip`, which recognises mask and YOLO archives before
falling back to a JSON walk. Everything downstream works on the resulting
`{filename: [annotation, ...]}` dict.

YOLO annotations stay normalized to [0, 1] out of the parser and are scaled to
pixels in the apply step, which is the first point the matched task's image
size is known. A matched task with unknown dimensions is reported in `skipped`
rather than written with meaningless coordinates.

A dry-run preview (`/preview`) reports the match before anything is written,
because a failed match is silent and expensive to discover after the fact: an
annotation for "img_01.jpg" that does not match any task's description is
simply skipped, and the only way to know that happened is to have asked first.

Format parsing lives in `formats/` alongside the builders it has to agree with;
this module owns the HTTP shape, the container detection, task matching and
label resolution.

Label resolution matches an existing class on its display name *or* its derived
interop `value`. An interop COCO export puts the value form in
`supercategory` ("RustArea") while its per-task JSON uses the display name
("Rust Area"), so importing both files from one source project would
otherwise create two labels for the same class.
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
from formats import annotations_json
from formats import coco as coco_format
from formats import yolo as yolo_format
from formats.common import image_size, value_from_name

# Folder names that mark a mask archive. Masks are export-only — tracing a
# raster back to polygons is not a faithful inverse — so an archive of them is
# rejected with a clear message rather than falling through as "nothing
# recognizable". See .devnotes/data-refactor/00_FORMAT_ANALYSIS.md § 8.
_MASK_DIRS = ("semantic_segmentations", "instance_segmentations")

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/imports", tags=["imports"], dependencies=[Depends(get_current_user)])


# Parsing lives in formats/, each alongside the builder it has to agree with.
# The geometry helpers that used to sit here moved to formats/common.py.
_parse_coco = coco_format.parse
_parse_native = annotations_json.parse


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

    # A class-set file (the Classes export) is a JSON array of label
    # definitions — {type, title, value, color, ...} with no per-image
    # `annotations`. It carries no geometry, so it belongs in the Classes
    # importer; caught here it gets a message that says where to take it, rather
    # than the generic "nothing recognizable" it would otherwise produce.
    if _looks_like_class_set(data):
        raise ValueError(
            "This looks like a class-set file (label definitions, no "
            "annotations). Import it from Classes -> Import, not here."
        )

    return _parse_native(data)


def _looks_like_class_set(data) -> bool:
    """True for a JSON array of label definitions with no annotation payload."""
    if not isinstance(data, list) or not data:
        return False
    return all(
        isinstance(item, dict) and "title" in item and "value" in item
        and "annotations" not in item and "name" not in item
        for item in data
    )


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

        names = [i.filename for i in infos]

        # A mask archive is recognised before anything else so it can be
        # rejected with a message that names the reason, rather than falling
        # through the JSON walk to a generic "nothing recognizable".
        if any(part in _MASK_DIRS
               for name in names
               for part in name.replace("\\", "/").split("/")):
            raise ValueError(
                "This looks like a mask export (semantic/instance segmentation "
                "images). Masks are export-only: a raster mask cannot be traced "
                "back to the original polygons faithfully, so it cannot be imported."
            )

        # A YOLO archive (classes.txt + label files) is dispatched to the YOLO
        # parser, whose annotations stay normalized until the apply step scales
        # them by the matched task's dimensions.
        if yolo_format.looks_like_archive(names):
            entries = {}
            for info in infos:
                if info.filename.lower().endswith(".txt") and info.file_size <= _ZIP_MAX_ENTRY_BYTES:
                    try:
                        entries[info.filename] = zf.read(info)
                    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
                        logger.warning("Could not read archive entry %r, skipping: %s",
                                       info.filename, exc)
            return yolo_format.parse_archive(entries)

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


def _stem(name: str) -> str:
    """Filename without its extension, e.g. "P1000015.JPG" -> "P1000015"."""
    base = name.replace("\\", "/").split("/")[-1]
    dot = base.rfind(".")
    return base[:dot] if dot > 0 else base


def _match_to_tasks(by_filename: Dict[str, List[dict]], project_id: int, db: Session):
    """Resolve each imported key to a Task.

    Matching by filename rather than any embedded id, since the imported file
    was very likely produced by a different system with no knowledge of this
    project's task ids.

    An exact `description` match wins. Failing that, the key is matched on its
    stem — a YOLO label file is `P1000015.txt` while the task is
    `P1000015.JPG`, so the extension cannot line up. Stem matching is only a
    fallback, and an ambiguous stem (two tasks sharing it) is left unmatched
    rather than guessed.
    """
    tasks = db.query(models.Task).filter(models.Task.project_id == project_id).all()
    tasks_by_desc = {t.description: t for t in tasks if t.description}

    # Stems that map to exactly one task; a collision drops out of this map so
    # it is never used to match ambiguously.
    stem_counts: Dict[str, int] = defaultdict(int)
    stem_to_task: Dict[str, models.Task] = {}
    for t in tasks:
        if t.description:
            s = _stem(t.description)
            stem_counts[s] += 1
            stem_to_task[s] = t
    unique_stems = {s: t for s, t in stem_to_task.items() if stem_counts[s] == 1}

    matched, unmatched = [], []
    for filename, anns in by_filename.items():
        task = tasks_by_desc.get(filename) or unique_stems.get(_stem(filename))
        if task:
            matched.append({"filename": filename, "task_id": task.id,
                            "annotation_count": len(anns)})
        else:
            unmatched.append({"filename": filename, "annotation_count": len(anns)})
    return matched, unmatched


# Keys a parser attaches to carry class identity through label resolution.
# They are stripped before the annotation is stored — the task's annotations
# reference a labelId, not a name.
_TRANSIENT_KEYS = ("labelName", "labelColor", "labelValue")


def _label_key(a: dict) -> str:
    """The key an annotation's class resolves under, case-insensitive."""
    return (a.get("labelName") or "object").lower()


def _resolve_label_ids(by_filename: Dict[str, List[dict]], project_id: int, db: Session) -> Dict[str, str]:
    """Map label name (case-insensitive) -> label id, creating missing labels.

    Import must not silently drop annotations because their class doesn't
    exist yet in the target project; it creates the label instead, consistent
    with the Classes import behaviour in labels.py.

    An existing label is matched on its display name *or* its derived interop
    `value`. An interop COCO export puts the value form in `supercategory`
    ("RustArea") while its per-task JSON uses the display name ("Rust Area"),
    so importing both files from one source project used to create two
    labels for the same class. Matching on either collapses them.

    Uses the color from the annotation if provided, otherwise falls back to the
    palette.
    """
    labels = db.query(models.Label).filter(models.Label.project_id == project_id).all()
    existing = {l.name.lower(): l.id for l in labels}
    # Secondary index: value form -> id. Only consulted when the display name
    # does not match, so an exact name match always wins.
    by_value = {value_from_name(l.name).lower(): l.id for l in labels}

    palette = ["#ef4444", "#f97316", "#eab308", "#22c55e", "#0f8b8d", "#3b82f6", "#8b5cf6", "#ec4899"]
    i = len(existing)

    # Track which labels we've seen (first occurrence wins for color)
    labels_to_create = {}

    for anns in by_filename.values():
        for a in anns:
            key = _label_key(a)
            if key in existing or key in labels_to_create:
                continue
            # The incoming name may itself be a value form, or the file may
            # report the value separately — try both against existing labels.
            candidates = [value_from_name(a.get("labelName") or "").lower()]
            if a.get("labelValue"):
                candidates.append(a["labelValue"].lower())
            matched = next((by_value[c] for c in candidates if c in by_value), None)
            if matched:
                existing[key] = matched
                continue

            color = a.get("labelColor") or palette[i % len(palette)]
            labels_to_create[key] = {"name": a.get("labelName") or "object", "color": color}
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

    # Which classes this import would create, using the same name-or-value
    # matching the apply path uses — otherwise the preview would promise a new
    # label that the import then resolves to an existing one.
    labels = db.query(models.Label).filter(models.Label.project_id == projectId).all()
    existing_names = {l.name.lower() for l in labels}
    existing_values = {value_from_name(l.name).lower() for l in labels}

    new_labels = []
    for name in sorted({a.get("labelName") or "object" for anns in by_filename.values() for a in anns}):
        if name.lower() in existing_names or value_from_name(name).lower() in existing_values:
            continue
        new_labels.append(name)

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
    annotations_imported = 0
    skipped = []
    for m in matched:
        task = tasks_by_id[m["task_id"]]
        anns = by_filename[m["filename"]]

        # YOLO annotations arrive normalized to [0, 1]; they can only be scaled
        # into pixels once the task — and therefore the image size — is known,
        # which is here. A task with unknown dimensions cannot be scaled, so it
        # is reported rather than written with meaningless coordinates.
        if any(a.get("normalized") for a in anns):
            width, height = image_size(task, db=db, persist=True)
            if not width or not height:
                skipped.append({
                    "filename": m["filename"],
                    "reason": "normalized coordinates need the image's dimensions, which are unknown",
                })
                continue
            anns = [yolo_format.denormalize(a, width, height) if a.get("normalized") else a
                    for a in anns]

        resolved = [
            {**{k: v for k, v in a.items() if k not in _TRANSIENT_KEYS},
             "labelId": label_ids[_label_key(a)]}
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
        annotations_imported += len(resolved)

    db.commit()
    return {
        "status": "ok",
        "tasks_updated": applied,
        "annotations_imported": annotations_imported,
        "unmatched": unmatched,
        # Tasks that matched but could not be written (normalized coordinates
        # with no image dimensions). Reported so the loss is visible.
        "skipped": skipped,
    }

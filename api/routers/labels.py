import csv
import datetime
import io
import json
import logging
import uuid

from fastapi import APIRouter, Depends, Query, UploadFile, File, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import List, Optional

import models
from logging_service import log_event
from database import get_db, commit_with_retry
from api.uploads import read_capped
from formats.common import clean_label_name, normalize_label_name
from schemas import LabelModel, LabelBulkUpsert, LabelBulkDelete, LabelBulkResult, LabelImportResult
from api.auth import get_current_user, require_csrf
from api.permissions import ProjectRole, require_project

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/labels", tags=["labels"], dependencies=[Depends(get_current_user), Depends(require_csrf)])

def purge_annotations_for_labels(project_id: int, label_ids: set, db: Session) -> int:
    """Delete every annotation in `project_id` that references a deleted label.

    One DELETE, scoped by a join on the label id. This used to load every task
    in the project, parse its annotation blob, filter in Python and write the
    whole blob back -- so deleting a single class rewrote every task in the
    project, a multi-GB write storm on a large one. Normalisation turned it
    into a statement the database executes from an index.

    Comments carry no labelId and are therefore never matched.

    Returns the number of annotations removed. The caller commits.
    """
    if not label_ids:
        return 0

    task_ids = [
        row[0]
        for row in db.query(models.Task.id)
        .filter(models.Task.project_id == project_id)
        .all()
    ]
    if not task_ids:
        return 0

    removed = (
        db.query(models.Annotation)
        .filter(
            models.Annotation.task_id.in_(task_ids),
            models.Annotation.label_id.in_(label_ids),
        )
        .delete(synchronize_session=False)
    )

    if removed:
        # `Task.updated_at` carries no `onupdate` (models.py), so a write that
        # does not assign it leaves the task looking untouched -- and leaves
        # every open tab holding a token that no longer describes the stored
        # annotations.
        (
            db.query(models.Task)
            .filter(models.Task.id.in_(task_ids))
            .update(
                {models.Task.updated_at: datetime.datetime.now(datetime.timezone.utc)},
                synchronize_session=False,
            )
        )

    return removed


@router.get("", response_model=List[LabelModel])
def get_labels(projectId: int = Query(...), db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    require_project(projectId, user, db, minimum=ProjectRole.VIEWER)
    labels = db.query(models.Label).filter(models.Label.project_id == projectId).all()
    return [{"id": l.id, "name": l.name, "color": l.color, "projectId": l.project_id} for l in labels]


@router.get("/usage")
def get_label_usage(projectId: int = Query(...), db: Session = Depends(get_db),
                    user: models.User = Depends(get_current_user)):
    """How many annotations reference each label in a project.

    The Classes view shows a usage count per class. It used to get that by
    fetching every task *with its full annotation blob* and tallying in the
    browser — the same payload problem as the Tasks view, for one column
    (.devnotes/server-optimization/03_TASKS_PAGE.md).

    Since the normalisation this is one GROUP BY on an indexed column. The
    docstring here used to record finding F16 -- "annotations are opaque
    `Text`, so the parse cannot be pushed into Postgres" -- and the whole
    endpoint existed to keep megabytes of blob from crossing the wire while
    still paying to parse them server-side. Storing annotations as rows
    retires F16: neither the transfer nor the parse happens now.

    Declared before `DELETE /{label_id}` -- a literal path must be registered
    ahead of a parameterised sibling or FastAPI matches it as `label_id`.
    Returns every label id that has at least one annotation; the client treats
    a missing id as zero.
    """
    require_project(projectId, user, db, minimum=ProjectRole.VIEWER)

    rows = (
        db.query(models.Annotation.label_id, func.count(models.Annotation.id))
        .join(models.Task, models.Task.id == models.Annotation.task_id)
        .filter(
            models.Task.project_id == projectId,
            models.Annotation.label_id.isnot(None),
        )
        .group_by(models.Annotation.label_id)
        .all()
    )
    return {label_id: count for label_id, count in rows}

@router.post("")
def create_or_update_label(label: LabelModel, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Create a class, or update the one the caller named.

    **Resolution is by id first, then by name.** It used to be by id only, and
    since the canvas mints a fresh uuid for every class it has not seen
    (`ensureLabel`), a fresh id never matched and this endpoint *always*
    inserted. That is how project 410 collected six classes named "object" on
    2026-09-08 — one per task open, created by nobody
    (.devnotes/fix-class-creation/01_AUDIT.md).

    The order is what makes both callers correct:

    1. **id hit** -> a rename/recolour of a class the caller already knows.
       This is the Classes page's edit modal, and the only way to rename, so it
       must stay first: a rename to a *new* name would otherwise be read as a
       create by step 2 and silently do nothing to the original row.
    2. **name hit** -> the class already exists. Return its real id rather than
       inserting a twin. This is what makes `ensureLabel` idempotent: the canvas
       gets the true id back, repoints its annotation at it, and a stale bundle
       posting an unknown uuid can no longer create a duplicate.
    3. neither -> insert.

    A rename whose new name collides with a *different* existing row is a 409,
    not a silent merge: merging would move every annotation of one class onto
    another with no way back, and the unique index would refuse the insert
    anyway.
    """
    require_project(label.projectId, user, db, minimum=ProjectRole.MANAGER)

    # Display name keeps the caller's casing; the key is what we match on.
    name = clean_label_name(label.name)
    key = normalize_label_name(label.name)

    db_label = db.query(models.Label).filter(
        models.Label.id == label.id, models.Label.project_id == label.projectId
    ).first()

    by_name = db.query(models.Label).filter(
        models.Label.project_id == label.projectId,
        models.Label.name_key == key,
    ).first()

    if db_label:
        if by_name is not None and by_name.id != db_label.id:
            raise HTTPException(
                status_code=409,
                detail=f'Another class in this project is already named "{name}".',
            )
        db_label.name = name
        db_label.name_key = key
        db_label.color = label.color
        created = False
    elif by_name:
        # The class exists under a different id. Adopt it; only the colour is
        # worth carrying over, and only because the Classes page's create form
        # is the one caller that sets it deliberately. The stored display name
        # is left alone: the class already has one its author chose, and a
        # canvas that merely referenced it must not restyle it for everyone.
        db_label = by_name
        db_label.color = label.color
        created = False
    else:
        db_label = models.Label(id=label.id, name=name, name_key=key,
                                color=label.color, project_id=label.projectId)
        db.add(db_label)
        created = True

    try:
        commit_with_retry(db)
    except IntegrityError:
        # Unreachable through the resolution above, which is the point: this is
        # the backstop that keeps the guarantee true if a future call path
        # forgets to resolve by name. A 500 here would read as a server fault
        # for what is a legible, actionable conflict.
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f'A class named "{name}" already exists in this project.',
        )

    log_event("label.upsert", project=label.projectId, label=db_label.id,
              name=db_label.name, created=created)
    return {"status": "ok", "id": db_label.id, "created": created}

@router.post("/bulk", response_model=LabelBulkResult)
def bulk_upsert_labels(payload: LabelBulkUpsert, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Upsert many labels in one transaction (tracker P3.3 / G4).

    Used by the Classes view's inline bulk-color-edit and by the class-set
    importer. A label whose projectId does not match `payload.projectId` is
    rejected rather than silently moved between projects.
    """
    require_project(payload.projectId, user, db, minimum=ProjectRole.MANAGER)

    mismatched = [l.id for l in payload.labels if l.projectId != payload.projectId]
    if mismatched:
        raise HTTPException(
            status_code=422,
            detail=f"Labels {mismatched} do not belong to projectId {payload.projectId}.",
        )

    rows = db.query(models.Label).filter(models.Label.project_id == payload.projectId).all()
    existing = {l.id: l for l in rows}
    # Name index, consulted when the id misses — same two-step resolution as
    # POST /api/labels, and for the same reason: an id-only lookup here would
    # insert a twin for any name the caller sent under an id we do not have.
    # Rows created within this call are added as we go, so two payload entries
    # normalising to one name collapse instead of colliding on the index.
    by_name = {normalize_label_name(l.name): l for l in rows}

    created = updated = 0
    for label in payload.labels:
        name = clean_label_name(label.name)
        key = normalize_label_name(label.name)
        row = existing.get(label.id) or by_name.get(key)
        if row:
            if row.name_key != key and by_name.get(key) not in (None, row):
                raise HTTPException(
                    status_code=409,
                    detail=f'Another class in this project is already named "{name}".',
                )
            by_name.pop(row.name_key, None)
            row.name = name
            row.name_key = key
            row.color = label.color
            by_name[key] = row
            updated += 1
        else:
            row = models.Label(id=label.id, name=name, name_key=key,
                               color=label.color, project_id=payload.projectId)
            db.add(row)
            existing[row.id] = row
            by_name[key] = row
            created += 1

    try:
        commit_with_retry(db)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Two classes in this request resolve to the same name.",
        )

    log_event("label.bulk_upsert", project=payload.projectId,
              created=created, updated=updated)
    return LabelBulkResult(created=created, updated=updated)
@router.post("/bulk-delete")
def bulk_delete_labels(payload: LabelBulkDelete, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    require_project(payload.projectId, user, db, minimum=ProjectRole.MANAGER)
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No ids provided")
    existing_ids = {
        row.id for row in db.query(models.Label.id).filter(
            models.Label.project_id == payload.projectId, models.Label.id.in_(payload.ids)
        ).all()
    }
    annotations_deleted = purge_annotations_for_labels(payload.projectId, existing_ids, db)
    deleted = (
        db.query(models.Label)
        .filter(models.Label.project_id == payload.projectId, models.Label.id.in_(payload.ids))
        .delete(synchronize_session=False)
    )
    commit_with_retry(db)
    # `annotations_deleted` is the number that matters here and the reason this
    # is WARN: deleting a class silently removes every annotation using it, in
    # every task, for every annotator. An annotator reporting "my boxes are
    # gone" whose task was never saved by anyone is very often this line.
    log_event(
        "label.bulk_delete",
        level="WARN",
        project=payload.projectId,
        deleted=deleted,
        annotations_deleted=annotations_deleted,
        ids=",".join(str(i) for i in sorted(existing_ids)),
    )
    return {"status": "ok", "deleted": deleted, "annotationsDeleted": annotations_deleted}


def _label_to_fastlabel(label: models.Label, order: int) -> dict:
    """Serialize a Label to the FastLabel class-set format.

    The FastLabel schema carries ~25 configuration fields that this app does
    not store (min/max dimensions, rotation locks, vertex count, etc.). They
    are emitted with their documented defaults so the file can be round-tripped
    into FastLabel without errors.

    `title` is the human-readable display name; `value` is the identifier used
    inside annotations. We derive `value` from `name` by stripping spaces —
    that matches the FastLabel convention closely enough for our use case.
    """
    value = label.name.replace(" ", "").replace("/", "").replace("(", "").replace(")", "").replace(",", "")
    return {
        "type": "polygon",
        "title": label.name,
        "value": value,
        "color": label.color,
        "order": order,
        "useBBox": False,
        "useRotation": False,
        "defaultWidth": 0,
        "defaultHeight": 0,
        "defaultLength": 0,
        "minWidth": 0,
        "minHeight": 0,
        "isAllowMinAtLeastOne": False,
        "minLength": 0,
        "maxWidth": 0,
        "maxHeight": 0,
        "isAllowMaxAtLeastOne": False,
        "maxLength": 0,
        "verticalRatio": None,
        "horizontalRatio": None,
        "maxAreaCount": None,
        "minArea": None,
        "maxInstanceCount": 0,
        "vertex": 0,
        "isOverlapFrameSelect": False,
        "isOutsideAnnotationFrameSelect": False,
        "isUniformSizeAcrossFrames": False,
        "isFrameGapRestricted": False,
        "lockRotationX": False,
        "lockRotationY": False,
        "lockRotationZ": False,
        "attributes": [],
        "keypoints": [],
    }


@router.get("/export")
def export_labels(
    projectId: int = Query(...),
    format: str = Query("json", pattern="^(json|csv|txt|fastlabel)$"),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Export this project's class set (tracker P3.3 / G4, enhanced T2.1).

    Formats:
      json       — simple [{id, name, color}] (our internal format)
      fastlabel  — full FastLabel class-set JSON (for import into FastLabel or
                   other tools; matches the structure of classes.json examples)
      csv        — id, name, color rows
      txt        — one name per line
    """
    require_project(projectId, user, db, minimum=ProjectRole.VIEWER)
    labels = db.query(models.Label).filter(models.Label.project_id == projectId).order_by(models.Label.name).all()

    if format == "txt":
        body = "\n".join(l.name for l in labels)
        return PlainTextResponse(body, media_type="text/plain")

    if format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["id", "name", "color"])
        for l in labels:
            writer.writerow([l.id, l.name, l.color])
        return PlainTextResponse(buf.getvalue(), media_type="text/csv")

    if format == "fastlabel":
        body = json.dumps([_label_to_fastlabel(l, i + 1) for i, l in enumerate(labels)], indent=2)
        return PlainTextResponse(body, media_type="application/json",
                                 headers={"Content-Disposition": "attachment; filename=\"classes.json\""})

    return [{"id": l.id, "name": l.name, "color": l.color} for l in labels]


def _parse_import_file(filename: str, raw: bytes) -> List[dict]:
    """Best-effort parse of a class-set file into {name, color?} dicts.

    Supports:
      - FastLabel format (array of {type, title, value, color, ...})
      - Simple JSON ({id, name, color} or string array or {labels: [...]})
      - CSV (with header or bare list)
      - .txt (one name per line)

    Raises ValueError with a message safe to show the user on anything
    unparseable, rather than leaking a stack trace.
    """
    ext = (filename or "").lower().rsplit(".", 1)[-1] if "." in (filename or "") else ""
    text = raw.decode("utf-8-sig", errors="replace")

    if ext == "json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc
        if isinstance(data, dict) and "labels" in data:
            data = data["labels"]
        if not isinstance(data, list):
            raise ValueError("Expected a JSON array of labels, or {\"labels\": [...]}.")
        out = []
        for item in data:
            if isinstance(item, str):
                out.append({"name": item})
            elif isinstance(item, dict):
                # FastLabel format: has "title" + "value" + "type" + many config fields
                # Our simple format: has "name" + "color"
                # Detect by presence of "title" field (FastLabel-specific)
                if item.get("title"):
                    out.append({"name": item["title"], "color": item.get("color")})
                elif item.get("name"):
                    out.append({"name": item["name"], "color": item.get("color")})
        return out

    if ext == "csv":
        reader = csv.DictReader(io.StringIO(text))
        if reader.fieldnames and "name" in reader.fieldnames:
            return [{"name": row["name"], "color": row.get("color")} for row in reader if row.get("name")]
        # No header, or a header without "name": treat every non-empty first
        # column as a class name.
        out = []
        for row in csv.reader(io.StringIO(text)):
            if row and row[0].strip():
                out.append({"name": row[0].strip()})
        return out

    # .txt or unrecognized extension: one class name per non-empty line.
    return [{"name": line.strip()} for line in text.splitlines() if line.strip()]


_PALETTE = ["#ef4444", "#f97316", "#eab308", "#22c55e", "#0f8b8d", "#3b82f6", "#8b5cf6", "#ec4899"]


@router.post("/import", response_model=LabelImportResult)
async def import_labels(
    projectId: int = Query(...),
    mode: str = Query("merge", pattern="^(merge|replace)$"),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """Import a class set from JSON, CSV, or a newline-delimited .txt file.

    `merge` adds/updates by name (case-insensitive) and leaves other existing
    labels alone. `replace` deletes every existing label for the project first.
    Names are deduplicated case-insensitively so re-importing the same file is
    a no-op rather than piling up near-duplicates.
    """
    require_project(projectId, user, db, minimum=ProjectRole.MANAGER)

    raw = await read_capped(file)
    try:
        parsed = _parse_import_file(file.filename or "", raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not parsed:
        raise HTTPException(status_code=422, detail="No classes found in the uploaded file.")

    replaced_count = replaced_annotations = 0
    if mode == "replace":
        # Replacing the class set orphans every annotation in the project, so
        # purge them too rather than leaving unnamed "Object" shapes behind.
        old_ids = {row.id for row in db.query(models.Label.id).filter(models.Label.project_id == projectId).all()}
        replaced_annotations = purge_annotations_for_labels(projectId, old_ids, db)
        replaced_count = len(old_ids)
        db.query(models.Label).filter(models.Label.project_id == projectId).delete()
        by_name = {}
    else:
        existing = db.query(models.Label).filter(models.Label.project_id == projectId).all()
        # Canonical form, not the raw stored name: identical after migration
        # a1c4e7b09f52, but a database that has not run it yet still holds
        # "Object"-style rows that an incoming "object" must match rather than
        # duplicate.
        by_name = {normalize_label_name(l.name): l for l in existing}

    created = updated = skipped = 0
    seen_this_import = set()
    for i, item in enumerate(parsed):
        raw = item.get("name") or ""
        # Matched on the case-folded key so "Rust_Area" and "rust area" are one
        # class, but *stored* with the file's own casing — a class set is often
        # imported precisely to establish the display names.
        name = clean_label_name(raw) if raw.strip() else ""
        key = normalize_label_name(raw) if raw.strip() else ""
        if not name or key in seen_this_import:
            skipped += 1
            continue
        seen_this_import.add(key)

        color = item.get("color") or _PALETTE[i % len(_PALETTE)]
        row = by_name.get(key)
        if row:
            row.color = color
            updated += 1
        else:
            row = models.Label(id=uuid.uuid4().hex, name=name, name_key=key,
                               color=color, project_id=projectId)
            db.add(row)
            by_name[key] = row
            created += 1

    commit_with_retry(db)
    # WARN on replace, for the same reason label.bulk_delete is WARN: that mode
    # deleted the project's entire class set and every annotation using it, for
    # every annotator, and until now left nothing in the log but a bare 200. An
    # annotator reporting vanished boxes is very often this line.
    log_event(
        "label.import",
        level="WARN" if mode == "replace" else "INFO",
        project=projectId,
        mode=mode,
        created=created,
        updated=updated,
        skipped=skipped,
        deleted=replaced_count,
        annotations_deleted=replaced_annotations,
    )
    final = db.query(models.Label).filter(models.Label.project_id == projectId).order_by(models.Label.name).all()
    return LabelImportResult(
        created=created, updated=updated, skipped=skipped,
        labels=[{"id": l.id, "name": l.name, "color": l.color, "projectId": l.project_id} for l in final],
    )


@router.delete("/{label_id}")
def delete_label(label_id: str, projectId: int = Query(...), db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    require_project(projectId, user, db, minimum=ProjectRole.MANAGER)
    db_label = db.query(models.Label).filter(
        models.Label.id == label_id, models.Label.project_id == projectId
    ).first()
    annotations_deleted = 0
    if db_label:
        annotations_deleted = purge_annotations_for_labels(projectId, {db_label.id}, db)
        log_event(
            "label.delete",
            level="WARN",
            project=projectId,
            label=db_label.id,
            name=db_label.name,
            annotations_deleted=annotations_deleted,
        )
        db.delete(db_label)
        commit_with_retry(db)
    return {"status": "ok", "annotationsDeleted": annotations_deleted}

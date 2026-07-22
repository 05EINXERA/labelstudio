import csv
import io
import json
import logging
import uuid

from fastapi import APIRouter, Depends, Query, UploadFile, File, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session
from typing import List, Optional

import models
from database import get_db
from schemas import LabelModel, LabelBulkUpsert, LabelBulkDelete, LabelBulkResult, LabelImportResult
from api.auth import get_current_user
from api.routers.projects import get_owned_project

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/labels", tags=["labels"], dependencies=[Depends(get_current_user)])

@router.get("", response_model=List[LabelModel])
def get_labels(projectId: int = Query(...), db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    get_owned_project(projectId, user, db)
    labels = db.query(models.Label).filter(models.Label.project_id == projectId).all()
    return [{"id": l.id, "name": l.name, "color": l.color, "projectId": l.project_id} for l in labels]

@router.post("")
def create_or_update_label(label: LabelModel, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    get_owned_project(label.projectId, user, db)
    db_label = db.query(models.Label).filter(
        models.Label.id == label.id, models.Label.project_id == label.projectId
    ).first()
    if db_label:
        db_label.name = label.name
        db_label.color = label.color
    else:
        db_label = models.Label(id=label.id, name=label.name, color=label.color, project_id=label.projectId)
        db.add(db_label)
    db.commit()
    return {"status": "ok", "id": db_label.id}

@router.post("/bulk", response_model=LabelBulkResult)
def bulk_upsert_labels(payload: LabelBulkUpsert, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    """Upsert many labels in one transaction (tracker P3.3 / G4).

    Used by the Classes view's inline bulk-color-edit and by the class-set
    importer. A label whose projectId does not match `payload.projectId` is
    rejected rather than silently moved between projects.
    """
    get_owned_project(payload.projectId, user, db)

    mismatched = [l.id for l in payload.labels if l.projectId != payload.projectId]
    if mismatched:
        raise HTTPException(
            status_code=422,
            detail=f"Labels {mismatched} do not belong to projectId {payload.projectId}.",
        )

    existing = {
        l.id: l for l in db.query(models.Label).filter(models.Label.project_id == payload.projectId).all()
    }
    created = updated = 0
    for label in payload.labels:
        row = existing.get(label.id)
        if row:
            row.name = label.name
            row.color = label.color
            updated += 1
        else:
            db.add(models.Label(id=label.id, name=label.name, color=label.color, project_id=payload.projectId))
            created += 1

    db.commit()
    return LabelBulkResult(created=created, updated=updated)


@router.post("/bulk-delete")
def bulk_delete_labels(payload: LabelBulkDelete, db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    get_owned_project(payload.projectId, user, db)
    if not payload.ids:
        raise HTTPException(status_code=400, detail="No ids provided")
    deleted = (
        db.query(models.Label)
        .filter(models.Label.project_id == payload.projectId, models.Label.id.in_(payload.ids))
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"status": "ok", "deleted": deleted}


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
    get_owned_project(projectId, user, db)
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
    get_owned_project(projectId, user, db)

    raw = await file.read()
    try:
        parsed = _parse_import_file(file.filename or "", raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not parsed:
        raise HTTPException(status_code=422, detail="No classes found in the uploaded file.")

    if mode == "replace":
        db.query(models.Label).filter(models.Label.project_id == projectId).delete()
        by_name = {}
    else:
        existing = db.query(models.Label).filter(models.Label.project_id == projectId).all()
        by_name = {l.name.lower(): l for l in existing}

    created = updated = skipped = 0
    seen_this_import = set()
    for i, item in enumerate(parsed):
        name = (item.get("name") or "").strip()
        key = name.lower()
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
            row = models.Label(id=uuid.uuid4().hex, name=name, color=color, project_id=projectId)
            db.add(row)
            by_name[key] = row
            created += 1

    db.commit()
    final = db.query(models.Label).filter(models.Label.project_id == projectId).order_by(models.Label.name).all()
    return LabelImportResult(
        created=created, updated=updated, skipped=skipped,
        labels=[{"id": l.id, "name": l.name, "color": l.color, "projectId": l.project_id} for l in final],
    )


@router.delete("/{label_id}")
def delete_label(label_id: str, projectId: int = Query(...), db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    get_owned_project(projectId, user, db)
    db_label = db.query(models.Label).filter(
        models.Label.id == label_id, models.Label.project_id == projectId
    ).first()
    if db_label:
        db.delete(db_label)
        db.commit()
    return {"status": "ok"}

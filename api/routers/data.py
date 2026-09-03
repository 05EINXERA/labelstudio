"""Per-user workspace key/value state.

Every row is scoped to the authenticated user. This table was previously keyed
on `key` alone, which meant a single global blackboard: any annotator's write
clobbered the same key for everyone else. On a shared LAN instance that is a
data-integrity bug, so `owner_id` is now part of the primary key and every
query filters on it. See .devnotes/deployment-hardening/00_AUDIT.md P1-1.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import models
from logging_service import log_event
from database import get_db, commit_with_retry
from schemas import WorkspaceData
from api.auth import get_current_user, require_csrf

router = APIRouter(
    prefix="/api/data",
    tags=["data"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)

@router.get("")
def get_data(db: Session = Depends(get_db), user: models.User = Depends(get_current_user)):
    rows = db.query(models.WorkspaceData).filter(
        models.WorkspaceData.owner_id == user.id
    ).all()
    return {row.key: row.value for row in rows}

@router.post("")
def set_data(
    data: WorkspaceData,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    item = db.query(models.WorkspaceData).filter(
        models.WorkspaceData.key == data.key,
        models.WorkspaceData.owner_id == user.id,
    ).first()
    if item:
        item.value = data.value
    else:
        item = models.WorkspaceData(key=data.key, value=data.value, owner_id=user.id)
        db.add(item)
    commit_with_retry(db)
    # Key only, never the value: this is a free-form per-user key/value store
    # and the value could be anything the client chose to put there.
    log_event("data.set", data_key=data.key)
    return {"status": "ok"}

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import or_

import models
from api.auth import get_current_user
from database import get_db
from schemas import LabelModel

router = APIRouter(
    prefix="/api/labels", tags=["labels"], dependencies=[Depends(get_current_user)]
)


@router.get("", response_model=List[LabelModel])
def get_labels(projectId: Optional[int] = Query(None), db: Session = Depends(get_db)):
    if projectId:
        labels = db.query(models.Label).filter(
            or_(models.Label.project_id == projectId, models.Label.project_id.is_(None))
        ).all()
    else:
        labels = db.query(models.Label).filter(models.Label.project_id.is_(None)).all()
    return [{"id": l.id, "name": l.name, "color": l.color, "project_id": l.project_id} for l in labels]


@router.post("")
def create_or_update_label(label: LabelModel, db: Session = Depends(get_db)):
    db_label = db.query(models.Label).filter(models.Label.id == label.id).first()
    if db_label:
        db_label.name = label.name
        db_label.color = label.color
        if label.project_id is not None:
            db_label.project_id = label.project_id
    else:
        db_label = models.Label(id=label.id, name=label.name, color=label.color, project_id=label.project_id)
        db.add(db_label)
    db.commit()
    return {"status": "ok", "id": db_label.id}


@router.delete("/{label_id}")
def delete_label(label_id: str, db: Session = Depends(get_db)):
    db_label = db.query(models.Label).filter(models.Label.id == label_id).first()
    if db_label:
        db.delete(db_label)
        db.commit()
    return {"status": "ok"}

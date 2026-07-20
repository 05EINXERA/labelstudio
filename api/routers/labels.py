from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Optional

import models
from database import get_db
from schemas import LabelModel
from api.auth import get_current_user

router = APIRouter(prefix="/api/labels", tags=["labels"], dependencies=[Depends(get_current_user)])

@router.get("", response_model=List[LabelModel])
def get_labels(projectId: int = Query(...), db: Session = Depends(get_db)):
    labels = db.query(models.Label).filter(models.Label.project_id == projectId).all()
    return [{"id": l.id, "name": l.name, "color": l.color, "projectId": l.project_id} for l in labels]

@router.post("")
def create_or_update_label(label: LabelModel, db: Session = Depends(get_db)):
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

@router.delete("/{label_id}")
def delete_label(label_id: str, projectId: int = Query(...), db: Session = Depends(get_db)):
    db_label = db.query(models.Label).filter(
        models.Label.id == label_id, models.Label.project_id == projectId
    ).first()
    if db_label:
        db.delete(db_label)
        db.commit()
    return {"status": "ok"}

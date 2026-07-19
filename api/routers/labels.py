from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List

import models
from database import get_db
from schemas import LabelModel
from api.auth import get_current_user

router = APIRouter(prefix="/api/labels", tags=["labels"], dependencies=[Depends(get_current_user)])

@router.get("", response_model=List[LabelModel])
def get_labels(db: Session = Depends(get_db)):
    labels = db.query(models.Label).all()
    return [{"id": l.id, "name": l.name, "color": l.color} for l in labels]

@router.post("")
def create_or_update_label(label: LabelModel, db: Session = Depends(get_db)):
    db_label = db.query(models.Label).filter(models.Label.id == label.id).first()
    if db_label:
        db_label.name = label.name
        db_label.color = label.color
    else:
        db_label = models.Label(id=label.id, name=label.name, color=label.color)
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

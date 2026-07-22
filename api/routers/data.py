from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

import models
from database import get_db
from schemas import WorkspaceData
from api.auth import get_current_user

router = APIRouter(prefix="/api/data", tags=["data"], dependencies=[Depends(get_current_user)])

@router.get("")
def get_data(db: Session = Depends(get_db)):
    rows = db.query(models.WorkspaceData).all()
    return {row.key: row.value for row in rows}

@router.post("")
def set_data(data: WorkspaceData, db: Session = Depends(get_db)):
    item = db.query(models.WorkspaceData).filter(models.WorkspaceData.key == data.key).first()
    if item:
        item.value = data.value
    else:
        item = models.WorkspaceData(key=data.key, value=data.value)
        db.add(item)
    db.commit()
    return {"status": "ok"}

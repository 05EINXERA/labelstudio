from typing import List
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import models
import schemas
from api.auth import get_current_user, require_csrf
from database import get_db, commit_with_retry

router = APIRouter(
    prefix="/api/notifications",
    tags=["notifications"],
    dependencies=[Depends(get_current_user)]
)

@router.get("", response_model=List[schemas.NotificationResponse])
def get_unread_notifications(
    user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all unread notifications for the current user."""
    notifications = db.query(models.Notification).filter(
        models.Notification.recipient_name == user,
        models.Notification.is_read == 0
    ).order_by(models.Notification.created_at.desc()).all()
    
    return [schemas.NotificationResponse.from_orm(n) for n in notifications]


@router.post("/mark-read", dependencies=[Depends(require_csrf)])
def mark_notifications_read(
    payload: schemas.MarkReadRequest,
    user: str = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Mark a list of notifications as read."""
    if not payload.notification_ids:
        return {"success": True}
        
    notifications = db.query(models.Notification).filter(
        models.Notification.id.in_(payload.notification_ids),
        models.Notification.recipient_name == user,
        models.Notification.is_read == 0
    ).all()
    
    if notifications:
        for n in notifications:
            n.is_read = 1
        commit_with_retry(db)
        
    return {"success": True}

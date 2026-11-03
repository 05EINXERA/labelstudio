"""Hull-model training from the app's data (the Hull "Train" button)."""
from fastapi import APIRouter, Depends, HTTPException

import config
import models
from api import hull_training
from api.auth import get_current_user, is_admin, require_admin, require_csrf
from ml import semseg_available
from schemas import HullTrainingStatus

router = APIRouter(
    prefix="/api/training",
    tags=["training"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)


def _status(user: models.User) -> dict:
    return {**hull_training.status(), "can_train": is_admin(user.username) and semseg_available()}


@router.get("/hull", response_model=HullTrainingStatus)
def get_hull_training(user: models.User = Depends(get_current_user)):
    """Progress of the latest run; everyone sees it, only admins get `can_train`."""
    return _status(user)


@router.post("/hull", response_model=HullTrainingStatus, status_code=202)
def start_hull_training(user: models.User = Depends(require_admin)):
    """Train a new hull model on every finished task; it is added to the model picker when done."""
    if not semseg_available():
        raise HTTPException(status_code=503, detail="Semantic segmentation is not enabled on this server.")
    if not config.AI_FEATURES_ENABLED:
        raise HTTPException(status_code=503, detail="AI features are temporarily disabled.")
    try:
        hull_training.start()
    except hull_training.TrainingBusy as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return _status(user)


@router.delete("/hull", response_model=HullTrainingStatus)
def cancel_hull_training(user: models.User = Depends(require_admin)):
    """Stop the running training. The default and picker models are untouched."""
    hull_training.cancel()
    return _status(user)

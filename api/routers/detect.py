from fastapi import APIRouter, HTTPException

from detector import DetectionClientError, detect_objects, classify_image
from schemas import DetectPayload, ClassifyPayload

router = APIRouter(prefix="/api/detect", tags=["detect"])

@router.post("")
def detect(payload: DetectPayload):
    try:
        response = detect_objects(payload.image, selection=payload.selection)
        return response
    except DetectionClientError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception:
        raise HTTPException(status_code=500, detail="Object detection failed.")

@router.post("/classify")
def classify(payload: ClassifyPayload):
    try:
        response = classify_image(payload.image)
        return response
    except DetectionClientError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as e:
        print(f"Classification error: {e}")
        raise HTTPException(status_code=500, detail="Image classification failed.")

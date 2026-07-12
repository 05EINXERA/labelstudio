from fastapi import APIRouter, HTTPException, Depends

from detector import DetectionClientError, detect_objects, classify_image
from schemas import DetectPayload, ClassifyPayload, SegmentPayload
from api.auth import get_current_user

router = APIRouter(prefix="/api/detect", tags=["detect"], dependencies=[Depends(get_current_user)])

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

@router.post("/segment")
def segment(payload: SegmentPayload):
    from detector import segment_point
    try:
        response = segment_point(payload.image, payload.point.x, payload.point.y, prompt=payload.prompt, precision=payload.precision, bbox=payload.bbox)
        return response
    except DetectionClientError as error:
        raise HTTPException(status_code=400, detail=str(error))
    except Exception as e:
        print(f"Segmentation error: {e}")
        raise HTTPException(status_code=500, detail="Image segmentation failed.")

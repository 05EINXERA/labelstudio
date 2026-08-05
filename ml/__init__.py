"""ML subsystem for object detection (YOLO), segmentation (SAM), and classification (CLIP)."""

from .common import (
    DATA_DIR,
    MODEL_DIR,
    DETECTOR_MODEL_DIR,
    WAND_MODEL_DIR,
    MODEL_FILE,
    model_path,
    download_url,
    INPUT_SIZE,
    CONFIDENCE,
    NMS_THRESHOLD,
    MAX_DETECTIONS,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_PIXELS,
    CLIP_MODEL_NAME,
    YOLO_WORLD_MODEL,
    COCO_CLASSES,
    CLIP_CANDIDATE_TAGS,
    DetectionClientError,
    get_inference_semaphore,
)
from .images import (
    decode_image,
    pil_to_bgr,
)
from .weights import (
    resolve_model_path,
    ensure_model_file,
)
from .yolo import (
    get_model,
    get_yolo_world_model,
    run_inference,
    detect_objects,
)
from .clip import (
    get_clip_model,
    classify_image,
)
from .sam import (
    embed_image,
    segment_point,
)

__all__ = [
    "DATA_DIR",
    "MODEL_DIR",
    "DETECTOR_MODEL_DIR",
    "WAND_MODEL_DIR",
    "MODEL_FILE",
    "model_path",
    "download_url",
    "INPUT_SIZE",
    "CONFIDENCE",
    "NMS_THRESHOLD",
    "MAX_DETECTIONS",
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_PIXELS",
    "CLIP_MODEL_NAME",
    "YOLO_WORLD_MODEL",
    "COCO_CLASSES",
    "CLIP_CANDIDATE_TAGS",
    "DetectionClientError",
    "get_inference_semaphore",
    "decode_image",
    "pil_to_bgr",
    "resolve_model_path",
    "ensure_model_file",
    "get_model",
    "get_yolo_world_model",
    "run_inference",
    "detect_objects",
    "get_clip_model",
    "classify_image",
    "embed_image",
    "segment_point",
]

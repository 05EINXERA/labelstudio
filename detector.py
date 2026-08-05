"""Backward-compatibility facade for the ML subsystem.

The ML architecture has been decomposed into the `ml/` sub-package per docs/ARCHITECTURE.md § 3.5:
- `ml.common`: Constants, exceptions, semaphore, cache & geometry helpers.
- `ml.images`: Image decoding & format conversions.
- `ml.weights`: Model weight path resolution & auto-downloads.
- `ml.yolo`: YOLOv8 ONNX & YOLO-World object detection.
- `ml.clip`: CLIP zero-shot classification.
- `ml.sam`: SAM / SAM2 interactive promptable segmentation.

This module re-exports all symbols to ensure 100% backward compatibility with existing
scripts and tests.
"""

from config import MAX_INFERENCE_CONCURRENCY

from ml.common import (
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
    _INFERENCE_SEMAPHORE,
    get_inference_semaphore,
    _evict_cache_if_needed,
    _get_image_hash,
    clamp_box,
    flatten_nms_indices,
    _normalize_selection_points,
)

from ml.images import (
    decode_image,
    pil_to_bgr,
)

from ml.weights import (
    resolve_model_path,
    ensure_model_file,
)

from ml.yolo import (
    _models,
    _model_lock,
    _yolo_world_model,
    _yolo_world_lock,
    _detect_cache,
    _detect_cache_lock,
    get_model,
    get_yolo_world_model,
    run_inference,
    detect_objects,
)

from ml.clip import (
    _clip_model,
    _clip_processor,
    _clip_text_features,
    _clip_model_lock,
    _clip_cache,
    _clip_cache_lock,
    get_clip_model,
    classify_image,
)

from ml.sam import (
    _sam_model,
    _sam_lock,
    _sam_embedding_cache,
    _sam_cache_lock,
    _hf_sam2_model,
    _hf_sam2_processor,
    _hf_sam2_lock,
    embed_image,
    segment_point,
)

__all__ = [
    "MAX_INFERENCE_CONCURRENCY",
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
    "_INFERENCE_SEMAPHORE",
    "get_inference_semaphore",
    "_evict_cache_if_needed",
    "_get_image_hash",
    "clamp_box",
    "flatten_nms_indices",
    "_normalize_selection_points",
    "decode_image",
    "pil_to_bgr",
    "resolve_model_path",
    "ensure_model_file",
    "_models",
    "_model_lock",
    "_yolo_world_model",
    "_yolo_world_lock",
    "_detect_cache",
    "_detect_cache_lock",
    "get_model",
    "get_yolo_world_model",
    "run_inference",
    "detect_objects",
    "_clip_model",
    "_clip_processor",
    "_clip_text_features",
    "_clip_model_lock",
    "_clip_cache",
    "_clip_cache_lock",
    "get_clip_model",
    "classify_image",
    "_sam_model",
    "_sam_lock",
    "_sam_embedding_cache",
    "_sam_cache_lock",
    "_hf_sam2_model",
    "_hf_sam2_processor",
    "_hf_sam2_lock",
    "embed_image",
    "segment_point",
]

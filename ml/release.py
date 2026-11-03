"""Drop every loaded model so the GPU is free for something else (hull training).

The loaders reload on next use, so this only costs the first request after it.
"""
import gc
import logging

from . import clip, sam, semseg, yolo

logger = logging.getLogger(__name__)


def release_gpu_models() -> None:
    with yolo._model_lock, yolo._yolo_world_lock, yolo._detect_cache_lock:
        yolo._models.clear()
        yolo._yolo_world_model = None
        yolo._detect_cache.clear()
    with sam._sam_lock, sam._sam_cache_lock:
        sam._sam_model = None
        sam._sam_embedding_cache.clear()
    with clip._clip_model_lock, clip._clip_cache_lock:
        clip._clip_model = None
        clip._clip_processor = None
        clip._clip_text_features = None
        clip._clip_cache.clear()
    with semseg._model_lock:
        semseg._loaded.clear()
    gc.collect()
    try:
        import torch
    except ImportError:
        return
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    logger.info("Released loaded ML models")

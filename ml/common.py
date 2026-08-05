import hashlib
import os
import threading
from config import DATA_DIR, MAX_INFERENCE_CONCURRENCY
from PIL import Image

MODEL_DIR = os.path.join(DATA_DIR, "models")
DETECTOR_MODEL_DIR = os.path.join(MODEL_DIR, "detector")
WAND_MODEL_DIR = os.path.join(MODEL_DIR, "wand")
MODEL_FILE = os.environ.get("YOLO_MODEL", "yolov8n-seg.onnx")
model_path = MODEL_FILE if os.path.isabs(MODEL_FILE) else os.path.join(DETECTOR_MODEL_DIR, MODEL_FILE)
download_url = os.environ.get(
    "YOLO_download_url",
    "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n-seg.pt",
)
INPUT_SIZE = int(os.environ.get("YOLO_INPUT_SIZE", "640"))
CONFIDENCE = float(os.environ.get("DETECT_CONFIDENCE", "0.35"))
NMS_THRESHOLD = float(os.environ.get("DETECT_NMS", "0.45"))
MAX_DETECTIONS = int(os.environ.get("DETECT_MAX", "100"))
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", str(50 * 1024 * 1024)))
MAX_IMAGE_PIXELS = int(os.environ.get("MAX_IMAGE_PIXELS", str(50_000_000)))

CLIP_MODEL_NAME = "openai/clip-vit-base-patch32"
YOLO_WORLD_MODEL = os.environ.get("YOLO_WORLD_MODEL", "yolov8s-worldv2.pt")

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

# ---------------------------------------------------------------------------
# ML Inference Concurrency Control & Memory Protection
# ---------------------------------------------------------------------------
_INFERENCE_SEMAPHORE = threading.BoundedSemaphore(value=max(1, MAX_INFERENCE_CONCURRENCY))


def get_inference_semaphore() -> threading.BoundedSemaphore:
    """Return the global semaphore gating concurrent PyTorch/OpenCV inferences."""
    return _INFERENCE_SEMAPHORE


def _evict_cache_if_needed(cache_dict: dict, lock: threading.RLock, max_size: int = 3) -> None:
    """Thread-safe bounded LRU cache eviction to prevent process memory growth."""
    with lock:
        while len(cache_dict) > max_size:
            oldest_key = next(iter(cache_dict))
            popped = cache_dict.pop(oldest_key, None)
            del popped


COCO_CLASSES = [
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse",
    "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator",
    "book", "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
]

CLIP_CANDIDATE_TAGS = COCO_CLASSES + ["daytime", "nighttime", "indoor", "outdoor", "screenshot", "document", "selfie", "landscape"]


class DetectionClientError(ValueError):
    """Invalid or unsupported client input."""


def _get_image_hash(image_data):
    if isinstance(image_data, str):
        return hashlib.md5(image_data.encode("utf-8")).hexdigest()
    elif isinstance(image_data, bytes):
        return hashlib.md5(image_data).hexdigest()
    return None


def clamp_box(x1, y1, x2, y2, width, height):
    left = max(0.0, min(x1, width))
    top = max(0.0, min(y1, height))
    right = max(left, min(x2, width))
    bottom = max(top, min(y2, height))
    box_width = max(1.0, right - left)
    box_height = max(1.0, bottom - top)
    return left, top, box_width, box_height


def flatten_nms_indices(indices):
    import numpy as np

    if indices is None or len(indices) == 0:
        return []

    if isinstance(indices, np.ndarray):
        return indices.flatten().tolist()

    if isinstance(indices, (list, tuple)):
        flattened = []
        for item in indices:
            if isinstance(item, (list, tuple, np.ndarray)):
                flattened.append(int(item[0]))
            else:
                flattened.append(int(item))
        return flattened

    return [int(indices)]


def _normalize_selection_points(selection):
    points = selection.get("points") if isinstance(selection, dict) else None
    if not points:
        return None

    normalized = []
    for item in points:
        if isinstance(item, dict):
            x = item.get("x")
            y = item.get("y")
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            x, y = item[0], item[1]
        else:
            continue

        if x is None or y is None:
            continue

        normalized.append((float(x), float(y)))

    if len(normalized) < 3:
        raise DetectionClientError("Selection must include at least three points.")

    return normalized

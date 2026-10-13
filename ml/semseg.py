"""Hull-condition semantic segmentation.

One class per pixel, trained by scripts/train_semseg.py; the network itself
is defined in ml/semseg_arch.py and named by the checkpoint. The class map is cut
into polygons shaped like `detect_objects` predictions so the canvas can add
them the same way. Off unless config.SEMSEG_ENABLED and the weights exist.
"""
import logging
import os
import threading

import cv2
import numpy as np
from PIL import Image

import config
from .common import DETECTOR_MODEL_DIR, DetectionClientError
from .images import decode_image

logger = logging.getLogger(__name__)

# Checkpoints record the photo scale they were validated at (`scale`) and their
# network (`arch`). These are the values of the first checkpoints, which
# predate both keys.
DEFAULT_SCALE = 0.5
DEFAULT_ARCH = "deeplabv3_mobilenet_v3_large"
_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)
# Contour simplification tolerance, in model-resolution pixels.
_APPROX_EPSILON = 1.0

# Model class name -> lower-case prefix of the project label it stands for.
# Projects spell the labels out in full ("AF Paint Deterioration / Peeling"),
# so the frontend matches on the prefix instead of creating new labels.
LABEL_PREFIXES = {
    "af_paint_deterioration": "af paint",
    "dirt1": "dirt 1",
    "dirt2": "dirt 2",
    "exposed_ac_paint": "exposed ac",
    "exposed_steel_plate": "exposed steel",
    "marine_organisms": "residual marine",
    "rust": "rust",
}

_model = None
_classes = None
_device = None
_scale = DEFAULT_SCALE
_model_lock = threading.RLock()


def semseg_weights_path() -> str:
    name = config.SEMSEG_MODEL
    return name if os.path.isabs(name) else os.path.join(DETECTOR_MODEL_DIR, name)


def semseg_available() -> bool:
    """True when the feature is switched on and its weights file is present."""
    return bool(config.SEMSEG_ENABLED) and os.path.isfile(semseg_weights_path())


def get_semseg_model():
    """Load the checkpoint once; returns (model, classes, device, scale)."""
    global _model, _classes, _device, _scale
    if _model is None:
        with _model_lock:
            if _model is None:
                # Imported here, like the other ml loaders: torch/torchvision are
                # heavy and the app must start (and run every other feature)
                # on a machine where this optional model is not installed.
                try:
                    import torch
                    from .semseg_arch import build_semseg_net
                except ImportError as error:
                    raise RuntimeError("Please install torch and torchvision to use semantic segmentation.") from error
                path = semseg_weights_path()
                logger.info("Loading semantic segmentation model %s", path)
                checkpoint = torch.load(path, map_location="cpu", weights_only=True)
                classes = checkpoint["classes"]
                model = build_semseg_net(checkpoint.get("arch", DEFAULT_ARCH), len(classes))
                model.load_state_dict(checkpoint["model"])
                device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
                _classes, _device = classes, device
                _scale = float(checkpoint.get("scale", DEFAULT_SCALE))
                _model = model.to(device).eval()
    return _model, _classes, _device, _scale


def mask_to_predictions(class_map, confidence, classes, scale, min_area, max_polygons):
    """Class-id map -> polygon predictions in source-image pixels.

    `class_map` and `confidence` are model-resolution arrays; `scale` is
    model pixels per source pixel. Class 0 (background / sound) yields nothing.
    Only outer contours are kept: the canvas has no holes, so a background
    island inside a region is filled.
    """
    inverse = 1.0 / scale
    found = []
    for class_id in range(1, len(classes)):
        selected = class_map == class_id
        if not selected.any():
            continue
        name = classes[class_id]["name"]
        contours, _ = cv2.findContours(selected.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            area = cv2.contourArea(contour) * inverse * inverse
            if area < min_area:
                continue
            approx = cv2.approxPolyDP(contour, _APPROX_EPSILON, True)
            if len(approx) < 3:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            inside = selected[y:y + h, x:x + w]
            score = float(confidence[y:y + h, x:x + w][inside].mean())
            found.append((area, {
                "class": name,
                "label_prefix": LABEL_PREFIXES.get(name, name.replace("_", " ")),
                "score": round(score, 4),
                "bbox": [x * inverse, y * inverse, w * inverse, h * inverse],
                "points": [{"x": float(p[0][0]) * inverse, "y": float(p[0][1]) * inverse} for p in approx],
            }))
    found.sort(key=lambda item: item[0], reverse=True)
    return [prediction for _, prediction in found[:max_polygons]]


def segment_semantic(image_data):
    """Segment a whole image into hull-condition polygons."""
    if not semseg_available():
        raise DetectionClientError("Semantic segmentation is not enabled on this server.")
    import torch  # see get_semseg_model
    from .semseg_arch import STRIDE

    image = decode_image(image_data)
    model, classes, device, scale = get_semseg_model()
    size = (max(STRIDE, round(image.width * scale)), max(STRIDE, round(image.height * scale)))
    resized = np.asarray(image.resize(size, Image.BILINEAR))
    # The network needs sides divisible by its stride; pad, then cut the pad off.
    pad_w, pad_h = (-size[0]) % STRIDE, (-size[1]) % STRIDE
    padded = np.pad(resized, ((0, pad_h), (0, pad_w), (0, 0)), mode="reflect")
    array = (padded.astype(np.float32) / 255.0 - _MEAN) / _STD
    tensor = torch.from_numpy(array.transpose(2, 0, 1))[None].to(device)
    with torch.inference_mode(), torch.autocast(device.type, enabled=device.type == "cuda"):
        probabilities = model(tensor)["out"].float().softmax(1)[0, :, :size[1], :size[0]]
    confidence, class_map = probabilities.max(0)
    predictions = mask_to_predictions(
        class_map.cpu().numpy(), confidence.cpu().numpy(), classes,
        size[0] / image.width, config.SEMSEG_MIN_AREA_PX, config.SEMSEG_MAX_POLYGONS,
    )
    return {"predictions": predictions}

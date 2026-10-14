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
# Outline smoothing (see smooth_outline). The class probabilities are blurred
# by this much (model pixels) before each pixel takes its class, so region
# borders do not zig-zag pixel by pixel between two classes.
_PROBABILITY_SIGMA = 1.0
# Outlines are smoothed along their length over about this many source pixels.
# 5 removed the most spikes without visibly rounding real shapes (2026-10-14,
# compared at 3, 5, 8 and 12 on a P2 photo).
_OUTLINE_SIGMA_PX = 5.0
# Each class mask is closed then opened with this kernel (model pixels) before
# outlining: one-pixel spurs, bridges and notches are what turn into needle
# peaks and deep V-cuts on the outline.
_CLEANUP_KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
# Fewest vertices an outline is given, however small the region.
_MIN_VERTICES = 6

# Model class name -> lower-case prefix of the project label it stands for.
# Projects spell the labels out in full ("AF Paint Deterioration / Peeling"),
# so the frontend matches on the prefix instead of creating new labels.
LABEL_PREFIXES = {
    "af_paint_deterioration": "af paint",
    "dirt1": "dirt 1",
    "dirt2": "dirt 2",
    # Checkpoints trained with Dirt 1 and Dirt 2 merged (build_semseg_dataset.py
    # --merge) predict one "dirt" class; its regions go on the Dirt 1 label.
    "dirt": "dirt 1",
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


def smooth_outline(points: np.ndarray, spacing: float, sigma: float = _OUTLINE_SIGMA_PX) -> np.ndarray:
    """Pixel-staircase outline -> smooth, evenly spaced vertices.

    `points` is a closed outline (N, 2) in source pixels, one point per
    boundary pixel, as cv2.CHAIN_APPROX_NONE gives it. Two steps:

    1. Gaussian smoothing along the outline (wrapping around, since it is
       closed). This removes the one-pixel steps and spikes of a pixel mask
       while keeping bends wider than about `sigma` pixels.
    2. Resampling at equal arc-length steps of about `spacing` pixels, so the
       vertices are spread the way freehand drawing lays them down
       (geometry.js appendSpacedPoints) instead of crowding on every pixel
       corner.
    """
    points = np.asarray(points, dtype=np.float64)
    if len(points) < 3:
        return points
    step = float(np.mean(np.linalg.norm(np.diff(np.vstack([points, points[:1]]), axis=0), axis=1))) or 1.0
    sigma_steps = sigma / step
    if sigma_steps >= 0.5:
        radius = min(int(np.ceil(3 * sigma_steps)), (len(points) - 1) // 2)
        offsets = np.arange(-radius, radius + 1)
        kernel = np.exp(-0.5 * (offsets / sigma_steps) ** 2)
        kernel /= kernel.sum()
        padded = np.concatenate([points[-radius:], points, points[:radius]]) if radius else points
        points = np.stack([np.convolve(padded[:, axis], kernel, mode="valid") for axis in (0, 1)], axis=1)
    closed = np.vstack([points, points[:1]])
    travelled = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(closed, axis=0), axis=1))])
    perimeter = travelled[-1]
    if perimeter <= 0:
        return points[:1]
    count = max(_MIN_VERTICES, int(round(perimeter / spacing)))
    targets = np.arange(count) * (perimeter / count)
    return np.stack([np.interp(targets, travelled, closed[:, axis]) for axis in (0, 1)], axis=1)


def mask_to_predictions(class_map, confidence, classes, scale, min_area, max_polygons, min_confidence=0.0,
                        vertex_spacing=10.0):
    """Class-id map -> polygon predictions in source-image pixels.

    Pixels predicted with less than `min_confidence` count as background, so a
    higher value trades regions found for regions that are right.

    `class_map` and `confidence` are model-resolution arrays; `scale` is
    model pixels per source pixel. Class 0 (background / sound) yields nothing.
    Only outer contours are kept: the canvas has no holes, so a background
    island inside a region is filled. Outlines are smoothed and given a vertex
    about every `vertex_spacing` source pixels (smooth_outline).
    """
    inverse = 1.0 / scale
    if min_confidence > 0:
        class_map = np.where(confidence >= min_confidence, class_map, 0)
    found = []
    for class_id in range(1, len(classes)):
        selected = class_map == class_id
        if not selected.any():
            continue
        cleaned = cv2.morphologyEx(selected.astype(np.uint8), cv2.MORPH_CLOSE, _CLEANUP_KERNEL)
        selected = cv2.morphologyEx(cleaned, cv2.MORPH_OPEN, _CLEANUP_KERNEL).astype(bool)
        if not selected.any():
            continue
        name = classes[class_id]["name"]
        contours, _ = cv2.findContours(selected.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        for contour in contours:
            area = cv2.contourArea(contour) * inverse * inverse
            if area < min_area:
                continue
            # +0.5: pixel centres, so the outline sits on the region rather than
            # half a pixel up and to the left of it.
            outline = smooth_outline((contour[:, 0, :] + 0.5) * inverse, vertex_spacing)
            if len(outline) < 3:
                continue
            x, y, w, h = cv2.boundingRect(contour)
            inside = selected[y:y + h, x:x + w]
            score = float(confidence[y:y + h, x:x + w][inside].mean())
            left, top = outline.min(axis=0)
            right, bottom = outline.max(axis=0)
            found.append((area, {
                "class": name,
                "label_prefix": LABEL_PREFIXES.get(name, name.replace("_", " ")),
                "score": round(score, 4),
                "bbox": [float(left), float(top), float(right - left), float(bottom - top)],
                "points": [{"x": float(px), "y": float(py)} for px, py in outline],
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
    probabilities = probabilities.cpu().numpy()
    for channel in probabilities:  # in place: smooth borders before argmax
        cv2.GaussianBlur(channel, (0, 0), _PROBABILITY_SIGMA, dst=channel)
    predictions = mask_to_predictions(
        probabilities.argmax(0), probabilities.max(0), classes,
        size[0] / image.width, config.SEMSEG_MIN_AREA_PX, config.SEMSEG_MAX_POLYGONS,
        min_confidence=config.SEMSEG_MIN_CONFIDENCE, vertex_spacing=config.SEMSEG_VERTEX_SPACING_PX,
    )
    return {"predictions": predictions}

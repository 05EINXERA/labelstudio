"""Hull-condition semantic segmentation.

One class per pixel, trained by scripts/train_semseg.py; the network itself
is defined in ml/semseg_arch.py and named by the checkpoint. The class map is cut
into polygons shaped like `detect_objects` predictions so the canvas can add
them the same way. Off unless config.SEMSEG_ENABLED and the weights exist.
"""
import logging
import os
import pickle
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
# Longest photo side the model is run on, in source pixels. Most photos are
# 2592 x 1944; some projects hold 5184 x 3888 ones, which need four times the
# memory, overflow a 4 GB GPU into system RAM and took a review run past 9 GB
# (2026-10-15). They are shrunk to this first. Training saw them at 0.5-1.0
# zoom, so half size is within what the model knows.
MAX_SIDE_PX = 2592

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
_info = None  # semseg_model_info() of the loaded checkpoint
_model_lock = threading.RLock()
# Details of the weights file before the model is loaded, keyed on
# (path, mtime, size) so a swapped file is read again.
_file_info = {}


def semseg_weights_path() -> str:
    name = config.SEMSEG_MODEL
    return name if os.path.isabs(name) else os.path.join(DETECTOR_MODEL_DIR, name)


def semseg_available() -> bool:
    """True when the feature is switched on and its weights file is present."""
    return bool(config.SEMSEG_ENABLED) and os.path.isfile(semseg_weights_path())


def _checkpoint_info(checkpoint: dict, path: str) -> dict:
    """The model's identity as the app shows it.

    `name` is the training run (train_semseg.py --name). Checkpoints that
    predate it fall back to the weights file name.
    """
    epoch = checkpoint.get("epoch")
    return {
        "name": str(checkpoint.get("name") or os.path.splitext(os.path.basename(path))[0]),
        "epoch": int(epoch) if epoch is not None else None,
    }


def semseg_model_info():
    """{"name", "epoch"} of the hull model, or None when it is unavailable.

    Once the model is loaded this describes the one in memory: the weights
    file can be replaced while the app runs, but only a restart loads it.
    Before that the file's metadata is read memory-mapped (the weights are
    not pulled into RAM) and cached until the file changes.
    """
    if _info is not None:
        return _info
    if not semseg_available():
        return None
    path = semseg_weights_path()
    try:
        stat = os.stat(path)
        key = (path, stat.st_mtime_ns, stat.st_size)
        if key not in _file_info:
            import torch  # see get_semseg_model
            checkpoint = torch.load(path, map_location="cpu", weights_only=True, mmap=True)
            _file_info.clear()
            _file_info[key] = _checkpoint_info(checkpoint, path)
        return _file_info[key]
    except (ImportError, OSError, RuntimeError, pickle.UnpicklingError) as error:
        logger.warning("Could not read hull model details from %s: %s", path, error)
        return None


def get_semseg_model():
    """Load the checkpoint once; returns (model, classes, device, scale)."""
    global _model, _classes, _device, _scale, _info
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
                _info = _checkpoint_info(checkpoint, path)
                _model = model.to(device).eval()
                logger.info("Hull model %s (epoch %s) loaded", _info["name"], _info["epoch"])
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


def flip_passes(flips: str) -> list[tuple[int, ...]]:
    """Tensor dims to mirror for each network pass: "" -> one plain pass,
    "h" -> plain + left-right, "hv" -> plain, left-right, up-down and both."""
    axes = [dim for flag, dim in (("h", -1), ("v", -2)) if flag in (flips or "").lower()]
    passes = [()]
    for dim in axes:
        passes += [dims + (dim,) for dims in passes]
    return passes


def probabilities_for(model, image: Image.Image, scale: float, device, flips: str = "") -> np.ndarray:
    """Blurred class probabilities (classes, h, w) of `image` at `scale`.

    Photos longer than MAX_SIDE_PX are shrunk to it first, so the result can
    be smaller than `scale` implies; callers take the ratio from its shape.
    With `flips` the network also sees mirrored copies of the photo; each
    answer is mirrored back and the passes are averaged (flip_passes).
    """
    import torch  # see get_semseg_model
    from .semseg_arch import STRIDE

    scale *= min(1.0, MAX_SIDE_PX / max(image.size))
    size = (max(STRIDE, round(image.width * scale)), max(STRIDE, round(image.height * scale)))
    resized = np.asarray(image.resize(size, Image.BILINEAR))
    # The network needs sides divisible by its stride; pad, then cut the pad off.
    pad_w, pad_h = (-size[0]) % STRIDE, (-size[1]) % STRIDE
    padded = np.pad(resized, ((0, pad_h), (0, pad_w), (0, 0)), mode="reflect")
    array = (padded.astype(np.float32) / 255.0 - _MEAN) / _STD
    tensor = torch.from_numpy(array.transpose(2, 0, 1))[None].to(device)
    passes = flip_passes(flips)
    total = None
    with torch.inference_mode(), torch.autocast(device.type, enabled=device.type == "cuda"):
        for dims in passes:
            # Mirroring the padded tensor moves the pad to the other side, so
            # the answer is mirrored back before the pad is cut off.
            logits = model(tensor.flip(dims) if dims else tensor)["out"]
            probabilities = logits.float().softmax(1)
            probabilities = probabilities.flip(dims) if dims else probabilities
            total = probabilities if total is None else total + probabilities
    probabilities = (total / len(passes))[0, :, :size[1], :size[0]].cpu().numpy()
    for channel in probabilities:  # in place: smooth borders before argmax
        cv2.GaussianBlur(channel, (0, 0), _PROBABILITY_SIGMA, dst=channel)
    return probabilities


def pick_classes(probabilities: np.ndarray, damage_min_probability: float = 0.0):
    """(class_map, confidence) from (classes, h, w) probabilities.

    Each pixel takes its likeliest class. With `damage_min_probability` > 0 a
    pixel whose likeliest damage class reaches that probability takes it even
    when sound hull is likelier: more damage is found, more of it wrongly.
    """
    class_map = probabilities.argmax(0)
    confidence = probabilities.max(0)
    if damage_min_probability > 0 and len(probabilities) > 1:
        damage = probabilities[1:].argmax(0) + 1
        damage_probability = np.take_along_axis(probabilities, damage[None], 0)[0]
        found = (class_map == 0) & (damage_probability >= damage_min_probability)
        class_map = np.where(found, damage, class_map)
        confidence = np.where(found, damage_probability, confidence)
    return class_map, confidence


def segment_semantic(image_data):
    """Segment a whole image into hull-condition polygons."""
    if not semseg_available():
        raise DetectionClientError("Semantic segmentation is not enabled on this server.")

    image = decode_image(image_data)
    model, classes, device, scale = get_semseg_model()
    probabilities = probabilities_for(model, image, scale, device, config.SEMSEG_FLIPS)
    class_map, confidence = pick_classes(probabilities, config.SEMSEG_DAMAGE_MIN_PROBABILITY)
    predictions = mask_to_predictions(
        class_map, confidence, classes,
        probabilities.shape[2] / image.width, config.SEMSEG_MIN_AREA_PX, config.SEMSEG_MAX_POLYGONS,
        min_confidence=config.SEMSEG_MIN_CONFIDENCE, vertex_spacing=config.SEMSEG_VERTEX_SPACING_PX,
    )
    return {"predictions": predictions, "model": _info}

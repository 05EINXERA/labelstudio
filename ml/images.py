import base64
import io
import os
import urllib.request
import cv2
import numpy as np
from PIL import Image

from .common import (
    DATA_DIR,
    MAX_IMAGE_BYTES,
    MAX_IMAGE_PIXELS,
    DetectionClientError,
)


def decode_image(image_data):
    """Decode raw base64, URL, local upload path, or bytes into an RGB PIL Image."""
    if not image_data:
        raise DetectionClientError("Missing image data.")

    if isinstance(image_data, str):
        if image_data.startswith("http://") or image_data.startswith("https://"):
            try:
                request = urllib.request.Request(image_data, headers={"User-Agent": "labelstudio"})
                with urllib.request.urlopen(request, timeout=10) as response:
                    raw = response.read()
            except Exception as error:
                raise DetectionClientError("Could not fetch image from URL.") from error
        elif image_data.startswith("/uploads/"):
            uploads_dir = os.path.realpath(os.path.join(DATA_DIR, "uploads"))
            filepath = os.path.realpath(os.path.join(DATA_DIR, image_data.lstrip("/")))
            if not filepath.startswith(uploads_dir):
                raise DetectionClientError("Invalid image path.")
            if os.path.isfile(filepath):
                try:
                    image = Image.open(filepath).convert("RGB")
                    image.load()
                    if image.width * image.height > MAX_IMAGE_PIXELS:
                        raise DetectionClientError("Image resolution is too large.")
                    return image
                except DetectionClientError:
                    raise
                except Exception as error:
                    raise DetectionClientError("Could not read local image.") from error
            else:
                raise DetectionClientError("Image not found.")
        else:
            if "," in image_data:
                image_data = image_data.split(",", 1)[1]
            try:
                raw = base64.b64decode(image_data, validate=True)
            except Exception as error:
                raise DetectionClientError("Invalid base64 image data.") from error
    elif isinstance(image_data, bytes):
        raw = image_data
    else:
        raise DetectionClientError("Unsupported image data type.")

    if len(raw) > MAX_IMAGE_BYTES:
        raise DetectionClientError(f"Image exceeds {MAX_IMAGE_BYTES // (1024 * 1024)} MB limit.")

    try:
        image = Image.open(io.BytesIO(raw)).convert("RGB")
        image.load()
        if image.width * image.height > MAX_IMAGE_PIXELS:
            raise DetectionClientError("Image resolution is too large.")
    except DetectionClientError:
        raise
    except Exception as error:
        raise DetectionClientError("Could not read image.") from error

    return image


def pil_to_bgr(image):
    """Convert an RGB PIL Image into an OpenCV BGR numpy array."""
    rgb = np.asarray(image)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

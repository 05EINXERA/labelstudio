"""Tests for the decomposed ml/ subpackage and detector.py backward-compatibility facade."""
import io
import os
import pytest
from PIL import Image

import detector
import ml
import ml.common
import ml.images
import ml.weights
import ml.yolo
import ml.clip
import ml.sam


def test_ml_subpackage_exports():
    """Verify that ml top-level package exports all expected public symbols."""
    expected_symbols = [
        "DetectionClientError",
        "COCO_CLASSES",
        "CLIP_CANDIDATE_TAGS",
        "CLIP_MODEL_NAME",
        "YOLO_WORLD_MODEL",
        "DATA_DIR",
        "MODEL_DIR",
        "DETECTOR_MODEL_DIR",
        "WAND_MODEL_DIR",
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
    for sym in expected_symbols:
        assert hasattr(ml, sym), f"ml is missing export {sym}"


def test_detector_facade_backward_compatibility():
    """Verify that detector.py facade re-exports identical symbols from ml package."""
    assert detector.DetectionClientError is ml.DetectionClientError
    assert detector.detect_objects is ml.detect_objects
    assert detector.classify_image is ml.classify_image
    assert detector.segment_point is ml.segment_point
    assert detector.embed_image is ml.embed_image
    assert detector.get_inference_semaphore is ml.get_inference_semaphore
    assert detector.resolve_model_path is ml.weights.resolve_model_path
    assert detector.ensure_model_file is ml.weights.ensure_model_file
    assert detector.COCO_CLASSES == ml.common.COCO_CLASSES
    assert detector.CLIP_CANDIDATE_TAGS == ml.common.CLIP_CANDIDATE_TAGS


def test_geometry_and_helpers():
    """Verify geometry utilities in ml.common."""
    # Clamp box
    left, top, w, h = ml.common.clamp_box(-10, -5, 120, 250, width=100, height=200)
    assert left == 0.0
    assert top == 0.0
    assert w == 100.0
    assert h == 200.0

    # NMS indices flattening
    assert ml.common.flatten_nms_indices([0, 1, 2]) == [0, 1, 2]
    assert ml.common.flatten_nms_indices([[0], [1], [2]]) == [0, 1, 2]
    assert ml.common.flatten_nms_indices([]) == []

    # Selection normalization
    selection_dict = {"points": [{"x": 10, "y": 20}, {"x": 30, "y": 40}, {"x": 50, "y": 60}]}
    pts = ml.common._normalize_selection_points(selection_dict)
    assert pts == [(10.0, 20.0), (30.0, 40.0), (50.0, 60.0)]

    # Too few points raises DetectionClientError
    with pytest.raises(ml.DetectionClientError):
        ml.common._normalize_selection_points({"points": [{"x": 1, "y": 1}, {"x": 2, "y": 2}]})


def test_image_decoding_and_conversion():
    """Verify image decoding in ml.images."""
    img = Image.new("RGB", (32, 32), color="red")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    raw_bytes = buf.getvalue()

    decoded = ml.images.decode_image(raw_bytes)
    assert decoded.size == (32, 32)

    bgr = ml.images.pil_to_bgr(decoded)
    assert bgr.shape == (32, 32, 3)
    # Red in RGB is (255, 0, 0) -> BGR is (0, 0, 255)
    assert bgr[0, 0, 0] == 0
    assert bgr[0, 0, 2] == 255


def test_weights_path_resolution(tmp_path):
    """Verify resolve_model_path behavior in ml.weights."""
    target_dir = str(tmp_path / "models")
    # Bare name should be joined with target_dir
    resolved = ml.weights.resolve_model_path("mobile_sam.pt", target_dir)
    assert resolved == os.path.join(target_dir, "mobile_sam.pt")

    # HF repo id should pass through untouched
    hf_repo = "facebook/sam2-hiera-large"
    assert ml.weights.resolve_model_path(hf_repo, target_dir) == hf_repo

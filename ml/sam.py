import logging
import threading
import cv2
import numpy as np
from PIL import Image

from .common import (
    CONFIDENCE,
    NMS_THRESHOLD,
    WAND_MODEL_DIR,
    COCO_CLASSES,
    _get_image_hash,
    _evict_cache_if_needed,
)
from .images import decode_image, pil_to_bgr
from .weights import resolve_model_path
from .yolo import run_inference, _model_lock

logger = logging.getLogger(__name__)

_sam_model = None
_sam_lock = threading.RLock()
_sam_embedding_cache = {}
_sam_cache_lock = threading.RLock()

_hf_sam2_model = None
_hf_sam2_processor = None
_hf_sam2_lock = threading.RLock()


def embed_image(image_data, sam_model=None):
    """Pre-compute and cache SAM embeddings for an image."""
    image_hash = _get_image_hash(image_data)
    if not image_hash:
        return {"status": "skipped"}

    with _sam_cache_lock:
        if image_hash in _sam_embedding_cache:
            return {"status": "cached"}

    image = decode_image(image_data)
    image_bgr = pil_to_bgr(image)

    sam_model_file = resolve_model_path(sam_model or 'mobile_sam.pt', WAND_MODEL_DIR)

    if sam_model_file == "facebook/sam2-hiera-large":
        import torch
        from transformers import Sam2Model, Sam2Processor
        global _hf_sam2_model, _hf_sam2_processor, _hf_sam2_lock
        with _hf_sam2_lock:
            if _hf_sam2_model is None:
                device = "cuda" if torch.cuda.is_available() else "cpu"
                _hf_sam2_processor = Sam2Processor.from_pretrained(sam_model_file)
                _hf_sam2_model = Sam2Model.from_pretrained(sam_model_file).to(device)

            image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
            image_pil = Image.fromarray(image_rgb)
            inputs = _hf_sam2_processor(images=image_pil, return_tensors="pt")
            device = "cuda" if torch.cuda.is_available() else "cpu"
            inputs = {k: v.to(device) for k, v in inputs.items() if hasattr(v, 'to')}

            with torch.no_grad():
                image_embeddings = _hf_sam2_model.get_image_embeddings(inputs["pixel_values"])

            with _sam_cache_lock:
                _sam_embedding_cache[image_hash] = {
                    "type": "sam2",
                    "embeddings": image_embeddings,
                    "original_shape": image_bgr.shape[:2]
                }
                _evict_cache_if_needed(_sam_embedding_cache, _sam_cache_lock, max_size=3)
    else:
        # For Ultralytics SAM
        global _sam_model
        try:
            from ultralytics import SAM
        except ImportError:
            raise RuntimeError("Please install ultralytics and torch to use SAM.")

        if type(_sam_model) is not dict:
            _sam_model = {}

        with _sam_lock:
            if sam_model_file not in _sam_model:
                _sam_model[sam_model_file] = SAM(sam_model_file)
                import torch
                if torch.cuda.is_available():
                    _sam_model[sam_model_file].to('cuda')

            active_sam = _sam_model[sam_model_file]

            # This triggers set_image internally and caches features in the predictor
            if hasattr(active_sam, 'predictor') and hasattr(active_sam.predictor, 'set_image'):
                active_sam.predictor.set_image(image_bgr)

            with _sam_cache_lock:
                # Store only the presence of the model features to avoid memory bloat
                _sam_embedding_cache[image_hash] = {
                    "type": "mobile_sam"
                }
                _evict_cache_if_needed(_sam_embedding_cache, _sam_cache_lock, max_size=3)

    return {"status": "embedded"}


def segment_point(image_data, points=None, labels=None, prompt=None, precision=0.001, bbox=None, sam_model=None):
    """Perform promptable interactive segmentation via SAM / SAM2."""
    model_size = "n"
    confidence = CONFIDENCE
    nms_threshold = NMS_THRESHOLD
    import torch  # lazy import: torch is heavy and only needed for SAM
    try:
        from ultralytics import SAM
    except ImportError:
        raise RuntimeError("Please install ultralytics and torch to use SAM.")

    if not points or len(points) == 0:
        return {"points": []}

    # Use the first point as the reference point for pointPolygonTest fallback logic
    x = points[0]["x"]
    y = points[0]["y"]

    pts_array = [[p["x"], p["y"]] for p in points]
    lbls_array = labels if labels else [1 for _ in points]

    image_hash = _get_image_hash(image_data)
    with _sam_cache_lock:
        cached = _sam_embedding_cache.get(image_hash) if image_hash else None

    image = decode_image(image_data)
    image_bgr = pil_to_bgr(image)

    if prompt:
        prompt_lower = prompt.lower()
        if prompt_lower in COCO_CLASSES:
            with _model_lock:
                raw_predictions = run_inference(image_bgr, model_size, confidence, nms_threshold)

            best_match = None

            for item in raw_predictions:
                if item.get("points") and item["class"].lower() == prompt_lower:
                    pts = np.array([[pt["x"], pt["y"]] for pt in item["points"]], np.float32)
                    dist = cv2.pointPolygonTest(pts, (x, y), measureDist=False)
                    if dist >= 0:
                        best_match = item
                        break

            if best_match:
                return {
                    "points": [{"x": float(pt["x"]), "y": float(pt["y"])} for pt in best_match["points"]]
                }

    global _sam_model, _hf_sam2_model, _hf_sam2_processor
    sam_model_file = resolve_model_path(sam_model or 'mobile_sam.pt', WAND_MODEL_DIR)

    if sam_model_file == "facebook/sam2-hiera-large":
        from transformers import Sam2Model, Sam2Processor
        with _hf_sam2_lock:
            if _hf_sam2_model is None:
                device = "cuda" if torch.cuda.is_available() else "cpu"
                _hf_sam2_processor = Sam2Processor.from_pretrained(sam_model_file)
                _hf_sam2_model = Sam2Model.from_pretrained(sam_model_file).to(device)

        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        image_pil = Image.fromarray(image_rgb)

        with _hf_sam2_lock:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            inputs = _hf_sam2_processor(
                images=image_pil,
                input_points=[[pts_array]],
                input_labels=[[lbls_array]],
                return_tensors="pt"
            )
            inputs = {k: v.to(device) for k, v in inputs.items() if hasattr(v, 'to')}

            # Use cached embeddings if available
            if cached and cached["type"] == "sam2":
                inputs["image_embeddings"] = cached["embeddings"]
                # We must delete pixel_values if we pass embeddings directly to Sam2Model forward
                if "pixel_values" in inputs:
                    del inputs["pixel_values"]

            with torch.no_grad():
                outputs = _hf_sam2_model(**inputs)

            # The model outputs 3 masks (for ambiguity) and IoU scores for each.
            # We must select the mask with the highest IoU score for the most accurate result.
            best_idx = torch.argmax(outputs.iou_scores[0, 0]).item()
            mask_np = outputs.pred_masks[0, 0, best_idx].cpu().numpy()
            orig_h, orig_w = image_bgr.shape[:2]
            mask_np = cv2.resize(mask_np, (orig_w, orig_h), interpolation=cv2.INTER_LINEAR)
            mask_np = (mask_np > 0.0).astype(np.uint8) * 255

        points_res = []
        contours, _ = cv2.findContours(mask_np, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            best_contour = None
            for c in contours:
                dist = cv2.pointPolygonTest(c, (x, y), False)
                if dist >= 0:
                    best_contour = c
                    break

            if best_contour is None:
                best_contour = max(contours, key=cv2.contourArea)

            contour_to_approx = best_contour

            epsilon = precision * cv2.arcLength(contour_to_approx, True)
            approx = cv2.approxPolyDP(contour_to_approx, epsilon, True)

            for pt in approx:
                points_res.append({"x": float(pt[0][0]), "y": float(pt[0][1])})

        return {
            "points": points_res
        }

    if type(_sam_model) is dict:
        pass
    else:
        _sam_model = {}

    if sam_model_file not in _sam_model:
        with _sam_lock:
            if sam_model_file not in _sam_model:
                _sam_model[sam_model_file] = SAM(sam_model_file)
                if torch.cuda.is_available():
                    _sam_model[sam_model_file].to('cuda')

    active_sam = _sam_model[sam_model_file]

    with _sam_lock:
        if bbox:
            results = active_sam(image_bgr, bboxes=[bbox], verbose=False)
        else:
            # Check if we can use the stateful predictor
            try:
                if hasattr(active_sam, 'predictor') and active_sam.predictor is not None:
                    # Initialize predictor if needed or if features belong to a different image
                    if getattr(active_sam.predictor, 'features', None) is None or getattr(active_sam, '_cached_hash', None) != image_hash:
                        active_sam.predictor.set_image(image_bgr)
                        active_sam._cached_hash = image_hash
                    # Try calling predictor directly to bypass re-encoding
                    results = active_sam.predictor(points=[pts_array], labels=[lbls_array])
                else:
                    results = active_sam(image_bgr, points=[pts_array], labels=[lbls_array], verbose=False)
            except Exception as exc:
                logger.warning("SAM predictor fast path failed, falling back: %s", exc)
                results = active_sam(image_bgr, points=[pts_array], labels=[lbls_array], verbose=False)

    points_res = []
    if results and len(results) > 0 and results[0].masks:
        masks = results[0].masks
        if masks.data is not None and len(masks.data) > 0:
            # Convert binary mask tensor to numpy array
            mask_np = (masks.data[0].cpu().numpy() * 255).astype(np.uint8)

            # Find external contours
            contours, _ = cv2.findContours(mask_np, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                # Find the contour that contains the clicked point (x, y)
                best_contour = None
                for c in contours:
                    dist = cv2.pointPolygonTest(c, (x, y), False)
                    if dist >= 0:
                        best_contour = c
                        break

                # Fallback to the largest contour if no contour contains the point directly
                if best_contour is None:
                    best_contour = max(contours, key=cv2.contourArea)

                contour_to_approx = best_contour

                # Approximate the contour to simplify it and remove redundant points/crisscross lines
                epsilon = precision * cv2.arcLength(contour_to_approx, True)
                approx = cv2.approxPolyDP(contour_to_approx, epsilon, True)

                for pt in approx:
                    points_res.append({"x": float(pt[0][0]), "y": float(pt[0][1])})

        # Fallback to masks.xy if masks.data is not accessible
        if not points_res and masks.xy and len(masks.xy) > 0:
            segment = masks.xy[0]
            for pt in segment:
                points_res.append({"x": float(pt[0]), "y": float(pt[1])})

    return {
        "points": points_res
    }

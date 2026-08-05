import threading
from PIL import Image

from .common import (
    CLIP_MODEL_NAME,
    CLIP_CANDIDATE_TAGS,
    _get_image_hash,
    _evict_cache_if_needed,
    _normalize_selection_points,
)
from .images import decode_image

_clip_model = None
_clip_processor = None
_clip_text_features = None
_clip_model_lock = threading.RLock()
_clip_cache = {}
_clip_cache_lock = threading.RLock()


def get_clip_model():
    """Retrieve or load the Hugging Face CLIP model and pre-compute tag embeddings."""
    global _clip_model, _clip_processor, _clip_text_features
    if _clip_model is None:
        with _clip_model_lock:
            if _clip_model is None:
                try:
                    from transformers import CLIPProcessor, CLIPModel
                    import torch
                except ImportError:
                    raise RuntimeError("Please install torch and transformers to use CLIP classification.")

                print(f"Loading CLIP model {CLIP_MODEL_NAME}...")
                device = "cuda" if torch.cuda.is_available() else "cpu"
                _clip_model = CLIPModel.from_pretrained(CLIP_MODEL_NAME).to(device)
                _clip_processor = CLIPProcessor.from_pretrained(CLIP_MODEL_NAME)

                print("Pre-computing CLIP text embeddings...")
                prompts = [f"a photo of a {c}" for c in CLIP_CANDIDATE_TAGS]
                text_inputs = _clip_processor(text=prompts, return_tensors="pt", padding=True).to(device)
                with torch.no_grad():
                    features_out = _clip_model.get_text_features(**text_inputs)
                    # get_text_features returns BaseModelOutputWithPooling where pooler_output is the projected feature
                    features = features_out.pooler_output if hasattr(features_out, 'pooler_output') else features_out
                    _clip_text_features = features / features.norm(p=2, dim=-1, keepdim=True)

    return _clip_model, _clip_processor, _clip_text_features


def classify_image(image_data, top_k=5, selection=None):
    """Classify an image or ROI using zero-shot CLIP tags."""
    import torch  # lazy import: torch is heavy and only needed for CLIP

    if not selection:
        image_hash = _get_image_hash(image_data)
        if image_hash:
            with _clip_cache_lock:
                if image_hash in _clip_cache:
                    return _clip_cache[image_hash]

    image = decode_image(image_data)

    if selection:
        original_width, original_height = image.size
        points = _normalize_selection_points(selection)
        if points is not None:
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            left = max(0.0, min(xs))
            top = max(0.0, min(ys))
            right = min(float(original_width), max(xs))
            bottom = min(float(original_height), max(ys))
            if right > left + 1 and bottom > top + 1:
                image = image.crop((left, top, right, bottom))
        else:
            try:
                left = float(selection.get("x", 0))
                top = float(selection.get("y", 0))
                box_width = float(selection.get("width", 0))
                box_height = float(selection.get("height", 0))
                if box_width > 0 and box_height > 0:
                    x1 = max(0.0, min(left, original_width))
                    y1 = max(0.0, min(top, original_height))
                    x2 = max(x1 + 1.0, min(original_width, x1 + box_width))
                    y2 = max(y1 + 1.0, min(original_height, y1 + box_height))
                    image = image.crop((x1, y1, x2, y2))
            except (TypeError, ValueError):
                pass

    model, processor, text_features = get_clip_model()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    inputs = processor(images=image, return_tensors="pt")
    inputs = {k: v.to(device) for k, v in inputs.items() if hasattr(v, 'to')}

    with torch.no_grad(), _clip_model_lock:
        image_features_out = model.get_image_features(**inputs)
        image_features = image_features_out.pooler_output if hasattr(image_features_out, 'pooler_output') else image_features_out
        image_features = image_features / image_features.norm(p=2, dim=-1, keepdim=True)

        logit_scale = model.logit_scale.exp()
        logits_per_image = logit_scale * image_features @ text_features.t()

    probs = logits_per_image.softmax(dim=1)

    probs_list = probs.squeeze().tolist()
    if not isinstance(probs_list, list):
        probs_list = [probs_list]

    results_with_scores = []
    for idx, prob in enumerate(probs_list):
        if idx < len(CLIP_CANDIDATE_TAGS):
            results_with_scores.append({
                "class": CLIP_CANDIDATE_TAGS[idx],
                "score": round(prob, 4)
            })

    results_with_scores.sort(key=lambda x: x["score"], reverse=True)
    results = results_with_scores[:top_k]

    if not selection:
        if image_hash:
            with _clip_cache_lock:
                _clip_cache[image_hash] = results
                _evict_cache_if_needed(_clip_cache, _clip_cache_lock, max_size=3)

    return {
        "tags": results
    }

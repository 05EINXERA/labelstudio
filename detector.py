import base64
import io
import os
import threading
import urllib.request
from config import DATA_DIR

import cv2
import numpy as np
from PIL import Image


MODEL_DIR = os.path.join(DATA_DIR, "models")
MODEL_FILE = os.environ.get("YOLO_MODEL", "yolov8n-seg.onnx")
MODEL_PATH = MODEL_FILE if os.path.isabs(MODEL_FILE) else os.path.join(MODEL_DIR, MODEL_FILE)
MODEL_URL = os.environ.get(
    "YOLO_MODEL_URL",
    "https://github.com/ultralytics/assets/releases/download/v8.2.0/yolov8n-seg.pt", 
)
INPUT_SIZE = int(os.environ.get("YOLO_INPUT_SIZE", "640"))
CONFIDENCE = float(os.environ.get("DETECT_CONFIDENCE", "0.35"))
NMS_THRESHOLD = float(os.environ.get("DETECT_NMS", "0.45"))
MAX_DETECTIONS = int(os.environ.get("DETECT_MAX", "100"))
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", str(50 * 1024 * 1024)))
MAX_IMAGE_PIXELS = int(os.environ.get("MAX_IMAGE_PIXELS", str(50_000_000)))

CLIP_MODEL_NAME = "openai/clip-vit-base-patch32"

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

_model = None
_model_lock = threading.RLock()

_clip_model = None
_clip_processor = None
_clip_model_lock = threading.RLock()

_sam_model = None

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


def ensure_model_file():
    if os.path.isfile(MODEL_PATH):
        return MODEL_PATH

    model_dir = os.path.dirname(MODEL_PATH) or "."
    os.makedirs(model_dir, exist_ok=True)
    
    is_pt_url = MODEL_URL.endswith(".pt")
    download_path = MODEL_PATH.replace(".onnx", ".pt") if is_pt_url else MODEL_PATH

    if not os.path.isfile(download_path):
        request = urllib.request.Request(
            MODEL_URL,
            headers={"User-Agent": "labelstudio-annotation-mvp/1.0"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                with open(download_path, "wb") as handle:
                    handle.write(response.read())
        except Exception as error:
            raise RuntimeError(
                f"Could not download YOLO model from {MODEL_URL} to {download_path}. "
                "Set YOLO_MODEL_URL or place the model file in the models folder."
            ) from error

    if is_pt_url:
        try:
            from ultralytics import YOLO
            print(f"Exporting {download_path} to ONNX format...")
            model = YOLO(download_path)
            model.export(format="onnx")
            
            # Ultralytics saves the exported model in the same directory as the .pt file
            exported_path = download_path.replace(".pt", ".onnx")
            if exported_path != MODEL_PATH and os.path.isfile(exported_path):
                import shutil
                shutil.move(exported_path, MODEL_PATH)
        except ImportError:
            raise RuntimeError(
                f"Model downloaded as PyTorch (.pt) to {download_path}, but OpenCV requires ONNX (.onnx).\n"
                "Please install ultralytics to automatically convert it:\n"
                "  pip install ultralytics\n"
                "Or manually run:\n"
                f"  yolo export model={download_path} format=onnx\n"
                f"And ensure the resulting .onnx file is at {MODEL_PATH}"
            )

    return MODEL_PATH


def get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                model_path = ensure_model_file()
                net = cv2.dnn.readNetFromONNX(model_path)
                net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
                _model = net
    return _model


def get_clip_model():
    global _clip_model, _clip_processor
    if _clip_model is None:
        with _clip_model_lock:
            if _clip_model is None:
                try:
                    from transformers import CLIPProcessor, CLIPModel
                except ImportError:
                    raise RuntimeError("Please install torch and transformers to use CLIP classification.")
                
                print(f"Loading CLIP model {CLIP_MODEL_NAME}...")
                _clip_model = CLIPModel.from_pretrained(CLIP_MODEL_NAME)
                _clip_processor = CLIPProcessor.from_pretrained(CLIP_MODEL_NAME)
    return _clip_model, _clip_processor


def decode_image(image_data):
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
    rgb = np.asarray(image)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def clamp_box(x1, y1, x2, y2, width, height):
    left = max(0.0, min(x1, width))
    top = max(0.0, min(y1, height))
    right = max(left, min(x2, width))
    bottom = max(top, min(y2, height))
    box_width = max(1.0, right - left)
    box_height = max(1.0, bottom - top)
    return left, top, box_width, box_height


def flatten_nms_indices(indices):
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


def run_inference(image_bgr):
    height, width = image_bgr.shape[:2]
    side = max(height, width)
    square = np.zeros((side, side, 3), np.uint8)
    square[0:height, 0:width] = image_bgr
    scale = side / INPUT_SIZE

    blob = cv2.dnn.blobFromImage(
        square,
        scalefactor=1 / 255.0,
        size=(INPUT_SIZE, INPUT_SIZE),
        swapRB=True,
    )

    net = get_model()
    net.setInput(blob)
    out_names = net.getUnconnectedOutLayersNames()
    outputs = net.forward(out_names)

    if len(outputs) == 2:
        if outputs[0].shape[1] == 32:
            proto_output = outputs[0]
            detect_output = outputs[1]
        else:
            proto_output = outputs[1]
            detect_output = outputs[0]
        out0 = np.array([cv2.transpose(detect_output[0])])
        proto = proto_output[0]
    else:
        out0 = np.array([cv2.transpose(outputs[0][0])])
        proto = None

    boxes = []
    scores = []
    class_ids = []
    mask_coeffs = []

    for row in out0[0]:
        class_scores = row[4:4+len(COCO_CLASSES)]
        _min_score, max_score, _min_loc, (_x, class_id) = cv2.minMaxLoc(class_scores)
        if float(max_score) < CONFIDENCE:
            continue

        boxes.append([
            float(row[0] - (0.5 * row[2])),
            float(row[1] - (0.5 * row[3])),
            float(row[2]),
            float(row[3]),
        ])
        scores.append(float(max_score))
        class_ids.append(int(class_id))
        
        if proto is not None:
            mask_coeffs.append(row[4+len(COCO_CLASSES):])

    if not boxes:
        return []

    indices = flatten_nms_indices(cv2.dnn.NMSBoxes(boxes, scores, CONFIDENCE, NMS_THRESHOLD))

    predictions = []
    for index in indices[:MAX_DETECTIONS]:
        box = boxes[index]
        left = box[0] * scale
        top = box[1] * scale
        box_width = box[2] * scale
        box_height = box[3] * scale
        class_id = class_ids[index]
        class_name = COCO_CLASSES[class_id] if class_id < len(COCO_CLASSES) else f"class_{class_id}"
        
        pred = {
            "class": class_name,
            "score": round(scores[index], 4),
            "bbox": [left, top, box_width, box_height],
        }
        
        if proto is not None:
            coeff = mask_coeffs[index]
            coeff = np.array(coeff).reshape(1, -1)
            proto_reshaped = proto.reshape(proto.shape[0], -1)
            mask_flat = np.dot(coeff, proto_reshaped)
            mask = 1 / (1 + np.exp(-mask_flat))
            mask = mask.reshape(proto.shape[1], proto.shape[2])
            mask = (mask > 0.5).astype(np.uint8) * 255
            mask = cv2.resize(mask, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
            
            bx, by, bw, bh = [int(v) for v in box]
            bx = max(0, bx)
            by = max(0, by)
            bw = max(1, bw)
            bh = max(1, bh)
            
            cropped_mask = np.zeros_like(mask)
            cropped_mask[by:by+bh, bx:bx+bw] = mask[by:by+bh, bx:bx+bw]
            
            contours, _ = cv2.findContours(cropped_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                contour = max(contours, key=cv2.contourArea)
                epsilon = 0.005 * cv2.arcLength(contour, True)
                approx = cv2.approxPolyDP(contour, epsilon, True)
                
                points = []
                for pt in approx:
                    points.append({"x": float(pt[0][0]) * scale, "y": float(pt[0][1]) * scale})
                pred["points"] = points
                
        predictions.append(pred)

    return predictions


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


def detect_objects(image_data, selection=None):
    image = decode_image(image_data)
    original_width, original_height = image.size
    origin_x = 0.0
    origin_y = 0.0
    working_image = image
    width, height = original_width, original_height

    if selection:
        points = _normalize_selection_points(selection)
        if points is not None:
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            left = max(0.0, min(xs))
            top = max(0.0, min(ys))
            right = min(float(original_width), max(xs))
            bottom = min(float(original_height), max(ys))

            if right <= left + 1 or bottom <= top + 1:
                raise DetectionClientError("Selection is too small.")

            roi_image = image.crop((left, top, right, bottom))
            roi_array = np.asarray(roi_image)
            roi_height, roi_width = roi_array.shape[:2]
            mask = np.zeros((roi_height, roi_width), dtype=np.uint8)
            polygon = np.array(
                [[int(round(x - left)), int(round(y - top))] for x, y in points],
                dtype=np.int32,
            )
            cv2.fillPoly(mask, [polygon], 255)
            masked_array = np.where(mask[..., None] > 0, roi_array, 0)
            working_image = Image.fromarray(masked_array.astype(np.uint8))
            origin_x = left
            origin_y = top
            width, height = roi_image.size
        else:
            try:
                left = float(selection.get("x", 0))
                top = float(selection.get("y", 0))
                box_width = float(selection.get("width", 0))
                box_height = float(selection.get("height", 0))
            except (TypeError, ValueError) as error:
                raise DetectionClientError("Invalid selection values.") from error

            if box_width <= 0 or box_height <= 0:
                raise DetectionClientError("Selection must have a positive width and height.")

            x1 = max(0.0, min(left, original_width))
            y1 = max(0.0, min(top, original_height))
            x2 = max(x1 + 1.0, min(original_width, x1 + box_width))
            y2 = max(y1 + 1.0, min(original_height, y1 + box_height))
            working_image = image.crop((x1, y1, x2, y2))
            origin_x = x1
            origin_y = y1
            width, height = working_image.size

    image_bgr = pil_to_bgr(working_image)

    with _model_lock:
        raw_predictions = run_inference(image_bgr)

    predictions = []
    for item in raw_predictions:
        x, y, box_width, box_height = item["bbox"]
        x2 = x + box_width
        y2 = y + box_height
        left, top, clamped_width, clamped_height = clamp_box(x, y, x2, y2, width, height)
        pred_dict = {
            "class": item["class"],
            "score": item["score"],
            "bbox": [
                round(left + origin_x, 2),
                round(top + origin_y, 2),
                round(clamped_width, 2),
                round(clamped_height, 2),
            ],
        }
        
        if "points" in item:
            pred_dict["points"] = [
                {"x": round(pt["x"] + origin_x, 2), "y": round(pt["y"] + origin_y, 2)}
                for pt in item["points"]
            ]
            
        predictions.append(pred_dict)

    return {
        "width": original_width,
        "height": original_height,
        "predictions": predictions,
    }


def classify_image(image_data, top_k=5):
    import torch
    image = decode_image(image_data)
    
    model, processor = get_clip_model()
    
    prompts = [f"a photo of a {c}" for c in CLIP_CANDIDATE_TAGS]
    
    inputs = processor(text=prompts, images=image, return_tensors="pt", padding=True)
    
    with torch.no_grad(), _clip_model_lock:
        outputs = model(**inputs)
        
    logits_per_image = outputs.logits_per_image
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
    
    return {
        "tags": results
    }

def segment_point(image_data, x, y, prompt=None):
    import torch
    try:
        from ultralytics import SAM
    except ImportError:
        raise RuntimeError("Please install ultralytics and torch to use SAM.")
    
    image = decode_image(image_data)
    image_bgr = pil_to_bgr(image)
    
    if prompt:
        prompt_lower = prompt.lower()
        if prompt_lower in COCO_CLASSES:
            with _model_lock:
                raw_predictions = run_inference(image_bgr)
            
            best_match = None
            import cv2
            import numpy as np
            
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
    
    global _sam_model
    if _sam_model is None:
        with _model_lock:
            if _sam_model is None:
                _sam_model = SAM('mobile_sam.pt')
                
    with _model_lock:
        results = _sam_model(image_bgr, points=[[x, y]], labels=[1], verbose=False)
    
    points_res = []
    if results and len(results) > 0 and results[0].masks:
        masks = results[0].masks
        if masks.data is not None and len(masks.data) > 0:
            import cv2
            import numpy as np
            
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
                
                # Approximate the contour to simplify it and remove redundant points/crisscross lines
                epsilon = 0.003 * cv2.arcLength(best_contour, True)
                approx = cv2.approxPolyDP(best_contour, epsilon, True)
                
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

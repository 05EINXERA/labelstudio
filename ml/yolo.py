import threading
import cv2
import numpy as np
from PIL import Image

from .common import (
    COCO_CLASSES,
    INPUT_SIZE,
    CONFIDENCE,
    NMS_THRESHOLD,
    MAX_DETECTIONS,
    YOLO_WORLD_MODEL,
    DETECTOR_MODEL_DIR,
    DetectionClientError,
    clamp_box,
    flatten_nms_indices,
    _normalize_selection_points,
    _get_image_hash,
    _evict_cache_if_needed,
)
from .images import decode_image, pil_to_bgr
from .weights import ensure_model_file, resolve_model_path

_models = {}
_model_lock = threading.RLock()

_yolo_world_model = None
_yolo_world_lock = threading.RLock()

_detect_cache = {}
_detect_cache_lock = threading.RLock()


def get_model(model_size='n'):
    """Retrieve or load the OpenCV DNN network for the requested YOLOv8 ONNX model size."""
    global _models
    with _model_lock:
        if model_size not in _models:
            path = ensure_model_file(model_size)
            net = cv2.dnn.readNetFromONNX(path)
            try:
                if hasattr(cv2, 'cuda') and cv2.cuda.getCudaEnabledDeviceCount() > 0:
                    net.setPreferableBackend(cv2.dnn.DNN_BACKEND_CUDA)
                    net.setPreferableTarget(cv2.dnn.DNN_TARGET_CUDA)
                else:
                    net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                    net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            except Exception:
                net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
                net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            _models[model_size] = net
    return _models[model_size]


def get_yolo_world_model():
    """Retrieve or load the Ultralytics YOLO-World model singleton."""
    global _yolo_world_model
    if _yolo_world_model is None:
        with _yolo_world_lock:
            if _yolo_world_model is None:
                try:
                    from ultralytics import YOLOWorld
                    import torch
                except ImportError:
                    raise RuntimeError("Please install ultralytics and torch to use YOLO-World.")
                world_path = resolve_model_path(YOLO_WORLD_MODEL, DETECTOR_MODEL_DIR)
                print(f"Loading YOLO-World model {world_path}...")
                _yolo_world_model = YOLOWorld(world_path)
                if torch.cuda.is_available():
                    _yolo_world_model.to('cuda')
    return _yolo_world_model


def run_inference(image_bgr, model_size, confidence, nms_threshold):
    """Run OpenCV DNN forward pass for YOLOv8-seg on image_bgr and return bounding boxes/contours."""
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

    net = get_model(model_size)
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
        if float(max_score) < confidence:
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

    indices = flatten_nms_indices(cv2.dnn.NMSBoxes(boxes, scores, confidence, nms_threshold))

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
            mask = cv2.resize(mask, (INPUT_SIZE, INPUT_SIZE), interpolation=cv2.INTER_LINEAR)
            mask = (mask > 0.5).astype(np.uint8) * 255

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
                epsilon = 0.001 * cv2.arcLength(contour, True)
                approx = cv2.approxPolyDP(contour, epsilon, True)

                points = []
                for pt in approx:
                    points.append({"x": float(pt[0][0]) * scale, "y": float(pt[0][1]) * scale})
                pred["points"] = points

        predictions.append(pred)

    return predictions


def detect_objects(image_data, selection=None, prompts=None, model_size=None, confidence=None, nms_threshold=None):
    """Detect objects in an image using YOLOv8 or open-vocabulary text prompts via YOLO-World."""
    model_size = model_size or "n"
    confidence = confidence or CONFIDENCE
    nms_threshold = nms_threshold or NMS_THRESHOLD

    if not selection and not prompts:
        image_hash = _get_image_hash(image_data)
        if image_hash:
            cache_key = f"{image_hash}_{model_size}_{confidence}_{nms_threshold}"
            with _detect_cache_lock:
                if cache_key in _detect_cache:
                    return _detect_cache[cache_key]

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

    predictions = []

    if prompts and len(prompts) > 0:
        world_model = get_yolo_world_model()
        with _yolo_world_lock:
            world_model.set_classes(prompts)
            results = world_model.predict(image_bgr, conf=confidence, verbose=False)

            if results and len(results) > 0:
                result = results[0]
                boxes = result.boxes
                if boxes:
                    for i in range(len(boxes)):
                        box = boxes[i]
                        x1, y1, x2, y2 = box.xyxy[0].tolist()
                        score = float(box.conf[0])
                        class_id = int(box.cls[0])
                        class_name = prompts[class_id] if class_id < len(prompts) else f"class_{class_id}"

                        left, top, clamped_width, clamped_height = clamp_box(x1, y1, x2, y2, width, height)
                        predictions.append({
                            "class": class_name,
                            "score": round(score, 4),
                            "bbox": [
                                round(left + origin_x, 2),
                                round(top + origin_y, 2),
                                round(clamped_width, 2),
                                round(clamped_height, 2),
                            ],
                        })
    else:
        with _model_lock:
            raw_predictions = run_inference(image_bgr, model_size, confidence, nms_threshold)

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

    result = {
        "width": original_width,
        "height": original_height,
        "predictions": predictions,
    }

    if not selection and not prompts:
        if image_hash:
            cache_key = f"{image_hash}_{model_size}_{confidence}_{nms_threshold}"
            with _detect_cache_lock:
                _detect_cache[cache_key] = result
                _evict_cache_if_needed(_detect_cache, _detect_cache_lock, max_size=3)

    return result

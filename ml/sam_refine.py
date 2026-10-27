"""Turn SAM masks into tighter YOLO labels (pure numpy/OpenCV, no model here).

`scripts/sam_refine_yolo_dataset.py` prompts SAM with each existing box and
hands the resulting mask to `refine_box` / `refine_polygon`. A mask is only
trusted when it agrees with the box it was prompted by; otherwise the original
geometry is kept, so a SAM miss can never make a label worse.
"""
import cv2
import numpy as np

MIN_POLYGON_AREA_PX = 4


def box_iou(a, b):
    """IoU of two (x1, y1, x2, y2) boxes."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def largest_component(mask):
    """Keep only the biggest connected blob of a binary mask (uint8, 0/1)."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    if count <= 1:
        return None
    biggest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return (labels == biggest).astype(np.uint8)


def mask_to_box(mask):
    """Tight (x1, y1, x2, y2) around a binary mask, or None if it is empty."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return None
    return (float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1))


def accept_mask(mask, prompt_box, min_iou=0.5, min_fill=0.1):
    """Return the cleaned mask if it plausibly segments `prompt_box`, else None.

    `min_iou` rejects masks whose extent disagrees with the prompt (SAM grabbed
    the background or a neighbour); `min_fill` rejects specks.
    """
    mask = largest_component(mask)
    if mask is None:
        return None
    box = mask_to_box(mask)
    prompt_area = (prompt_box[2] - prompt_box[0]) * (prompt_box[3] - prompt_box[1])
    if prompt_area <= 0 or mask.sum() / prompt_area < min_fill:
        return None
    if box_iou(box, prompt_box) < min_iou:
        return None
    return mask


def refine_box(mask, prompt_box, min_iou=0.5, min_fill=0.1):
    """(box, refined): the mask's tight box, or `prompt_box` unchanged if rejected."""
    accepted = accept_mask(mask, prompt_box, min_iou, min_fill)
    if accepted is None:
        return tuple(prompt_box), False
    return mask_to_box(accepted), True


def refine_polygon(mask, prompt_box, epsilon=0.002, min_iou=0.5, min_fill=0.1):
    """(points, refined): simplified outline of the mask as [(x, y), ...] in pixels.

    Falls back to the prompt box's four corners when the mask is rejected, so
    every label survives the conversion to a segmentation dataset.
    """
    x1, y1, x2, y2 = prompt_box
    corners = [(x1, y1), (x2, y1), (x2, y2), (x1, y2)]
    accepted = accept_mask(mask, prompt_box, min_iou, min_fill)
    if accepted is None:
        return corners, False
    contours, _ = cv2.findContours(accepted, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    contour = max(contours, key=cv2.contourArea)
    approx = cv2.approxPolyDP(contour, epsilon * cv2.arcLength(contour, True), True)
    if len(approx) < 3 or cv2.contourArea(approx) < MIN_POLYGON_AREA_PX:
        return corners, False
    return [(float(p[0][0]), float(p[0][1])) for p in approx], True


def parse_label_line(line, width, height):
    """(class_id, prompt_box_px, kind) from a YOLO line; kind is 'box' or 'polygon'.

    Polygon lines are prompted by their bounding box. Returns None for blank or
    malformed lines.
    """
    tokens = line.split()
    if len(tokens) == 5:
        cls, cx, cy, w, h = int(tokens[0]), *map(float, tokens[1:])
        box = ((cx - w / 2) * width, (cy - h / 2) * height, (cx + w / 2) * width, (cy + h / 2) * height)
        return cls, box, "box"
    if len(tokens) >= 7 and len(tokens) % 2 == 1:
        values = list(map(float, tokens[1:]))
        xs, ys = values[0::2], values[1::2]
        box = (min(xs) * width, min(ys) * height, max(xs) * width, max(ys) * height)
        return int(tokens[0]), box, "polygon"
    return None


def format_box_line(cls, box, width, height):
    x1, y1, x2, y2 = box
    cx, cy = (x1 + x2) / 2 / width, (y1 + y2) / 2 / height
    return f"{cls} {cx:.6f} {cy:.6f} {(x2 - x1) / width:.6f} {(y2 - y1) / height:.6f}"


def format_polygon_line(cls, points, width, height):
    coords = " ".join(
        f"{min(max(x / width, 0.0), 1.0):.6f} {min(max(y / height, 0.0), 1.0):.6f}" for x, y in points
    )
    return f"{cls} {coords}"

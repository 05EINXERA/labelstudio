"""YOLO segmentation format — build and parse.

The reference export is **YOLOv8 segmentation**, not the 5-token detection
format. Verified against
.devnotes/data-examples/exports/fast-label/yolo_option/: one line per
annotation, `<class_idx> x1 y1 x2 y2 ...` with every coordinate normalized to
[0, 1]; a sampled line carried 205 tokens (1 + 102 points). Since every
annotation in this app is a polygon with points, segmentation is also the
correct target for us.

Archive layout:

    classes.txt              one class `value` per line; the line number
                             (0-based) is the class index used in the labels
    annotations/<stem>.txt   one file per task

Two things the format cannot express, handled explicitly rather than silently:

  - Normalization needs the image's pixel dimensions. A task whose dimensions
    are unknown is **skipped and reported**, never exported with a guessed
    size or a division by zero.
  - A class index means nothing without classes.txt, so importing a bare
    .txt is rejected rather than guessed at.
"""
import json
import logging
import posixpath
from typing import Dict, List, Optional, Sequence, Tuple

import models
from formats.common import (
    annotation_type_of,
    bbox_of,
    image_size,
    is_annotation,
    points_of,
    round2,
    safe_stem,
    values_for_labels,
)

logger = logging.getLogger(__name__)

# Coordinate precision. The reference uses repr()-style formatting rather than
# a fixed width: 0.4384516 (7 dp), but also "0.0" and "8.87e-05". Rounding to
# 7 decimal places and letting repr() render matches it exactly, including the
# scientific-notation case that "%.7f" would flatten to 0.0000887.
_PRECISION = 7

ANNOTATIONS_DIR = "annotations"
CLASSES_FILE = "classes.txt"


def _fmt(v: float) -> str:
    return repr(round(v, _PRECISION))


def _normalize(value: float, extent: int) -> float:
    """Scale to [0, 1], clamped.

    An annotation can extend a little past the image edge after a drag, and a
    negative or >1 coordinate is invalid in this format.
    """
    return min(1.0, max(0.0, value / extent))


def build(tasks: Sequence[models.Task], labels: Sequence[models.Label],
          db=None) -> Tuple[List[Tuple[str, bytes]], List[dict]]:
    """Project -> (archive entries, skipped tasks).

    Entries are (arcname, content) pairs relative to the archive root. The
    skipped list carries {"filename", "reason"} for every task that could not
    be represented, so the caller can surface it instead of shipping a
    silently short export.
    """
    values = values_for_labels(labels)
    # The class index is this list's position. Label order is the same order
    # COCO's category_id uses, so the two exports agree.
    ordered = list(labels)
    class_index = {label.id: i for i, label in enumerate(ordered)}

    entries: List[Tuple[str, bytes]] = [
        (CLASSES_FILE, ("\n".join(values[l.id] for l in ordered) + "\n").encode("utf-8")),
    ]
    skipped: List[dict] = []

    for task in tasks:
        name = task.description or f"task-{task.id}"
        width, height = image_size(task, db=db, persist=db is not None)
        if not width or not height:
            # Normalizing by zero is a crash, and inventing a size would emit
            # coordinates that silently mean nothing.
            skipped.append({
                "filename": name,
                "reason": "image dimensions are unknown, so coordinates cannot be normalized",
            })
            logger.warning("YOLO export skipping task %s (%s): no image dimensions", task.id, name)
            continue

        lines = []
        for ann in _annotations_of(task):
            idx = class_index.get(ann.get("labelId"))
            if idx is None:
                continue  # references a label that no longer exists
            points = points_of(ann)
            if len(points) < 2:
                continue
            coords = []
            for p in points:
                coords.append(_fmt(_normalize(p["x"], width)))
                coords.append(_fmt(_normalize(p["y"], height)))
            lines.append(" ".join([str(idx)] + coords))

        # An empty file is the YOLO convention for a negative sample (an image
        # with no objects), and the reference export writes one too — so a task
        # with no annotations still gets its entry.
        body = ("\n".join(lines) + "\n") if lines else ""
        entries.append((f"{ANNOTATIONS_DIR}/{safe_stem(task)}.txt", body.encode("utf-8")))

    return entries, skipped


def _annotations_of(task: models.Task) -> List[dict]:
    try:
        anns = json.loads(task.annotations) if task.annotations else []
    except (ValueError, TypeError) as exc:
        logger.warning("Task %s has unparseable annotations, skipping in export: %s", task.id, exc)
        return []
    if not isinstance(anns, list):
        return []
    return [a for a in anns if is_annotation(a)]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_classes(raw: bytes) -> List[str]:
    """classes.txt -> [name, ...] indexed by line number."""
    text = raw.decode("utf-8-sig", errors="replace")
    return [line.strip() for line in text.splitlines() if line.strip()]


def parse_label_file(raw: bytes, class_names: Sequence[str]) -> List[dict]:
    """One <stem>.txt -> [annotation, ...] with **normalized** points.

    Coordinates stay in [0, 1] here: denormalizing needs the target task's
    pixel dimensions, which are only known once the file has been matched to a
    task. The caller scales them and drops the `normalized` marker.

    Accepts both shapes YOLO uses:
      - segmentation: `<cls> x1 y1 x2 y2 ...` (3+ points)
      - detection:    `<cls> cx cy w h`       (exactly 4 values)
    A 4-value line is unambiguously detection format, since a polygon needs at
    least 3 points (6 values).
    """
    out: List[dict] = []
    text = raw.decode("utf-8-sig", errors="replace")

    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        try:
            idx = int(parts[0])
            coords = [float(v) for v in parts[1:]]
        except (ValueError, IndexError):
            logger.warning("Skipping malformed YOLO line %d: %r", lineno, line[:80])
            continue

        if idx < 0 or idx >= len(class_names):
            logger.warning("Skipping YOLO line %d: class index %d is not in classes.txt", lineno, idx)
            continue

        if len(coords) == 4:
            # Detection format: centre x, centre y, width, height.
            cx, cy, w, h = coords
            x0, y0, x1, y1 = cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2
            points = [{"x": x0, "y": y0}, {"x": x1, "y": y0},
                      {"x": x1, "y": y1}, {"x": x0, "y": y1}]
            shape = "bbox"
        elif len(coords) >= 6 and len(coords) % 2 == 0:
            points = [{"x": coords[i], "y": coords[i + 1]} for i in range(0, len(coords), 2)]
            shape = "polygon"
        else:
            logger.warning("Skipping YOLO line %d: %d coordinates is not a valid shape",
                           lineno, len(coords))
            continue

        out.append({
            "labelName": class_names[idx],
            "points": points,
            # The caller must scale these by the matched task's dimensions.
            "normalized": True,
            "type": shape,
        })
    return out


def denormalize(ann: dict, width: int, height: int) -> dict:
    """Scale a parsed annotation's [0, 1] points into pixels."""
    points = [{"x": round2(p["x"] * width), "y": round2(p["y"] * height)}
              for p in ann["points"]]
    x, y, w, h = bbox_of(points)
    out = {k: v for k, v in ann.items() if k != "normalized"}
    out["points"] = points
    out["x"], out["y"] = round2(x), round2(y)
    out["width"], out["height"] = round2(w), round2(h)
    return out


def parse_archive(entries: Dict[str, bytes]) -> Dict[str, List[dict]]:
    """A YOLO archive's files -> {stem: [annotation, ...]}, still normalized.

    `entries` maps arcname -> content. Keys are **stems**, not filenames: a
    label file is `P1000015.txt` while the task is `P1000015.JPG`, so the
    caller matches on the stem rather than the full name.

    Raises ValueError when classes.txt is absent — a class index is
    meaningless without it, and guessing would mislabel every annotation.
    """
    classes_raw = None
    label_files: Dict[str, bytes] = {}

    for arcname, content in entries.items():
        base = posixpath.basename(arcname.replace("\\", "/"))
        if base.lower() == CLASSES_FILE:
            classes_raw = content
        elif base.lower().endswith(".txt"):
            label_files[base[:-4]] = content

    if classes_raw is None:
        raise ValueError(
            "This looks like a YOLO export but has no classes.txt. "
            "Class indexes cannot be resolved to names without it."
        )
    class_names = parse_classes(classes_raw)
    if not class_names:
        raise ValueError("classes.txt is empty, so no class index can be resolved.")

    out: Dict[str, List[dict]] = {}
    for stem, content in label_files.items():
        anns = parse_label_file(content, class_names)
        if anns:
            out[stem] = anns
    return out


def looks_like_archive(names: Sequence[str]) -> bool:
    """True when an archive's entry names look like a YOLO export attempt.

    Used for container detection, checked before the generic JSON walk so a
    YOLO archive is routed here rather than falling through as "nothing
    recognizable".

    The signal is: at least one .txt label file, and nothing that looks like
    one of the JSON formats. classes.txt is deliberately *not* required — an
    archive of label files with it missing is still a YOLO import, just a
    broken one, and parse_archive raises a message that says so. Requiring it
    here would send that case to the generic error instead.
    """
    bases = [posixpath.basename(n.replace("\\", "/")).lower() for n in names]
    has_txt_labels = any(b.endswith(".txt") and b != CLASSES_FILE for b in bases)
    has_json = any(b.endswith(".json") for b in bases)
    return has_txt_labels and not has_json

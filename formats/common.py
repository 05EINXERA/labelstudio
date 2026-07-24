"""Shared helpers for every import/export format.

Everything here is pure or takes an explicit Session/Task — no FastAPI, no
request state — so each piece is unit-testable without a TestClient.

Covers .devnotes/data-refactor/01_PLAN.md § 1.2-1.5:
  - image_size()          task pixel dimensions, with lazy backfill
  - annotation_type_of()  the shape type the DB never stored (gap G1)
  - value_from_name()     the single interop `value` derivation (gap G5)
  - status maps           our vocabulary <-> the interop format's (gap G4)
"""
import logging
import os
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, UnidentifiedImageError

import models
from config import DATA_DIR

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def round2(v: float) -> float:
    """Coordinates are stored and exported at 2 dp, matching the interop format."""
    return round(v, 2)


def points_of(ann: dict) -> List[dict]:
    """An annotation's vertices as [{x, y}, ...].

    Falls back to the four corners of the x/y/width/height box for annotations
    that carry no explicit points.
    """
    pts = ann.get("points") or []
    if pts and isinstance(pts[0], dict):
        return pts
    x, y = ann.get("x", 0), ann.get("y", 0)
    w, h = ann.get("width", 0), ann.get("height", 0)
    return [{"x": x, "y": y}, {"x": x + w, "y": y}, {"x": x + w, "y": y + h}, {"x": x, "y": y + h}]


def flatten_points(points: Sequence[dict]) -> List[float]:
    """[{x, y}, ...] -> [x1, y1, x2, y2, ...], the interop wire format."""
    return [round2(c) for p in points for c in (p["x"], p["y"])]


def unflatten_points(flat: Sequence[float]) -> List[dict]:
    """[x1, y1, x2, y2, ...] -> [{x, y}, ...]. Trailing odd value is dropped."""
    return [{"x": round2(flat[i]), "y": round2(flat[i + 1])} for i in range(0, len(flat) - 1, 2)]


def bbox_of(points: Sequence[dict]) -> Tuple[float, float, float, float]:
    """Axis-aligned (x, y, width, height) enclosing `points`."""
    xs = [p["x"] for p in points]
    ys = [p["y"] for p in points]
    return min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)


def polygon_area(points: Sequence[dict]) -> float:
    """True polygon area by the shoelace formula.

    COCO's `area` is the area of the *shape*, not of its bounding box. We
    previously emitted `bbox_w * bbox_h`, which overstates any non-rectangular
    polygon — for the concave shapes this app produces that is off by a lot,
    and `area` is what downstream tools use to filter small instances.
    """
    n = len(points)
    if n < 3:
        return 0.0
    total = 0.0
    for i in range(n):
        a, b = points[i], points[(i + 1) % n]
        total += a["x"] * b["y"] - b["x"] * a["y"]
    return abs(total) / 2.0


# ---------------------------------------------------------------------------
# Annotation shape type (gap G1)
# ---------------------------------------------------------------------------

# Tolerance for deciding a 4-point ring is an axis-aligned rectangle. Stored
# coordinates are rounded to 2 dp, so exact equality would misclassify boxes
# that survived a round trip.
_RECT_TOLERANCE = 0.5


def _cluster(values: Sequence[float]) -> List[float]:
    """Group near-equal coordinates, returning one representative each.

    Exact set membership would split 10.0 and 10.01 — which are the same edge
    of a box that has been through a 2 dp round trip — into two values, so the
    clustering has to be tolerance-based rather than a set of rounded numbers.
    """
    out: List[float] = []
    for v in sorted(values):
        if not out or abs(v - out[-1]) > _RECT_TOLERANCE:
            out.append(v)
    return out


def _is_axis_aligned_rect(points: Sequence[dict]) -> bool:
    if len(points) != 4:
        return False
    xs = _cluster([p["x"] for p in points])
    ys = _cluster([p["y"] for p in points])
    # A rectangle collapses to exactly two distinct x edges and two y edges...
    if len(xs) != 2 or len(ys) != 2:
        return False
    # ...and its four corners cover all four (x, y) combinations, which rules
    # out shapes that touch only some of them (e.g. a degenerate "Z").
    expected = {(x, y) for x in xs for y in ys}
    covered = set()
    for p in points:
        for ex, ey in expected:
            if abs(p["x"] - ex) <= _RECT_TOLERANCE and abs(p["y"] - ey) <= _RECT_TOLERANCE:
                covered.add((ex, ey))
                break
    return len(covered) == 4


def annotation_type_of(ann: dict) -> str:
    """'bbox' or 'polygon'.

    The canvas historically stored both shapes as the same object — points plus
    an x/y/width/height bound — with nothing recording which tool drew it, so
    every box exported as a polygon and the distinction was lost permanently on
    a round trip (gap G1).

    New annotations carry an explicit `type`. For everything already in the
    database we infer: a 4-point axis-aligned rectangle is a box, anything else
    is a polygon. That misreads a polygon a user happened to draw as a perfect
    rectangle, which is the rare and harmless direction of the error.
    """
    explicit = ann.get("type")
    if explicit in ("bbox", "polygon"):
        return explicit
    # "box" is a legacy spelling written by the auto-detect path before the
    # vocabulary was unified; it is still present in saved annotations.
    if explicit == "box":
        return "bbox"
    return "bbox" if _is_axis_aligned_rect(points_of(ann)) else "polygon"


def is_annotation(ann) -> bool:
    """Real annotations only — comments live in the same array but are not shapes."""
    return isinstance(ann, dict) and ann.get("type") != "comment"


# ---------------------------------------------------------------------------
# Label `value` derivation (gap G5)
# ---------------------------------------------------------------------------

# Characters the interop format strips when deriving `value` from a display name.
# Verified against .devnotes/data-examples/imports/classes.json: "Dirt 2 (Light
# Rust Stains, Water Stains, etc.)" -> "Dirt2LightRustStainsWaterStainsetc."
# Note the trailing "." survives — do not extend this set without re-checking
# that file, or round trips with existing interop exports break.
_VALUE_STRIP = (" ", "/", "(", ")", ",")


def value_from_name(name: str) -> str:
    """A label's interop `value` (identifier) from its display name.

    Single source of truth: this was duplicated in exports.py and labels.py
    with the same literal strip list, so the two could drift silently.
    """
    out = name or ""
    for ch in _VALUE_STRIP:
        out = out.replace(ch, "")
    return out


def values_for_labels(labels: Sequence[models.Label]) -> Dict[str, str]:
    """{label_id: value}, guaranteed collision-free.

    Stripping punctuation can map two distinct classes onto one value
    ("A/B" and "AB" both become "AB"). That silently merges classes on import,
    and corrupts the class index in YOLO's classes.txt, where the value *is*
    the identity. Collisions get a numeric suffix and a warning rather than
    being allowed through.
    """
    out: Dict[str, str] = {}
    used: Dict[str, str] = {}  # value -> the label id that claimed it
    for label in labels:
        base = value_from_name(label.name)
        value = base
        n = 2
        while value in used:
            value = f"{base}-{n}"
            n += 1
        if value != base:
            logger.warning(
                "Label %r derives the same interop value %r as label %r; "
                "exporting it as %r to keep classes distinct.",
                label.name, base, used[base], value,
            )
        used[value] = label.name
        out[label.id] = value
    return out


# ---------------------------------------------------------------------------
# Status vocabulary (gap G4)
# ---------------------------------------------------------------------------

# Ours (schemas.TASK_STATUSES) -> (interop status, the interop format externalStatus).
# the interop format splits what we keep in one column: "Approved" is a completed task
# that additionally carries externalStatus "approved".
TO_EXTERNAL_STATUS: Dict[str, Tuple[str, str]] = {
    "New": ("registered", ""),
    "In Progress": ("in_progress", ""),
    "Completed": ("completed", ""),
    "Approved": ("completed", "approved"),
}

# The inverse. externalStatus wins when it says "approved", because that is the
# only way the interop format distinguishes an approved task from a merely completed one.
FROM_EXTERNAL_STATUS: Dict[str, str] = {
    "registered": "New",
    "in_progress": "In Progress",
    "completed": "Completed",
    "approved": "Approved",
}


def to_external_status(status: Optional[str]) -> Tuple[str, str]:
    """Our status -> (status, externalStatus) in the interop vocabulary."""
    if status in TO_EXTERNAL_STATUS:
        return TO_EXTERNAL_STATUS[status]
    logger.warning("Unknown task status %r; exporting it unchanged.", status)
    return (status or "", "")


def from_external_status(status: Optional[str], external_status: Optional[str] = None) -> str:
    """the interop (status, externalStatus) -> our status."""
    if (external_status or "").lower() == "approved":
        return "Approved"
    key = (status or "").lower()
    if key in FROM_EXTERNAL_STATUS:
        return FROM_EXTERNAL_STATUS[key]
    # A status we don't recognise must not silently become "Completed".
    if status:
        logger.warning("Unknown interop status %r; importing the task as New.", status)
    return "New"


# ---------------------------------------------------------------------------
# Image dimensions (gap G2)
# ---------------------------------------------------------------------------

def image_size(task: models.Task, db=None, persist: bool = False) -> Tuple[int, int]:
    """Pixel dimensions of a task's image, or (0, 0) if unknown.

    Prefers the stored columns; falls back to reading the file header with
    Pillow (`.size` parses the header only, not the pixels).

    `persist=True` writes a value recovered from disk back to the Task, so the
    read happens once per image rather than once per export. The caller must
    own a writable Session and commit. It defaults to False because CLAUDE.md
    rule 4 forbids GET handlers writing to the database — only the export
    background job, which is POST-initiated and holds its own session, passes
    True.

    Returns (0, 0) rather than raising: a missing or corrupt image must never
    fail a whole export. Callers that divide by these (YOLO normalization, mask
    rasterization) check for zero and skip the task with a reported reason.
    """
    if task.image_width and task.image_height:
        return task.image_width, task.image_height

    if not task.image_path:
        return 0, 0

    # image_path is stored relative to DATA_DIR as "uploads/<name>" with a
    # forward slash (see projects._save_upload).
    path = os.path.join(DATA_DIR, *task.image_path.split("/"))
    try:
        with Image.open(path) as im:
            width, height = im.size
    except (OSError, UnidentifiedImageError) as exc:
        logger.warning("Could not read image size for task %s (%s): %s", task.id, path, exc)
        return 0, 0

    if persist and db is not None:
        task.image_width, task.image_height = width, height
        db.add(task)

    return width, height


def measure_image(path: str) -> Tuple[Optional[int], Optional[int]]:
    """Dimensions of a file on disk, or (None, None) if unreadable.

    Used at upload time. Returns None rather than 0 so an unreadable image
    leaves the columns NULL and stays eligible for a later backfill, instead of
    being recorded as a genuine 0x0.
    """
    try:
        with Image.open(path) as im:
            return im.size
    except (OSError, UnidentifiedImageError) as exc:
        logger.warning("Could not measure uploaded image %s: %s", path, exc)
        return None, None


# ---------------------------------------------------------------------------
# Archive naming
# ---------------------------------------------------------------------------

def safe_stem(task: models.Task) -> str:
    """Archive-safe base name for a task, without extension.

    `task.description` is the raw client-supplied filename, so it can carry
    directory components (from either OS) or traversal segments. Anything that
    is not a plain name is discarded rather than sanitised piecemeal.
    """
    raw = task.description or ""
    # Strip both separators: a Windows-uploaded name can reach a POSIX server.
    base = os.path.basename(raw.replace("\\", "/").rstrip("/"))
    stem = os.path.splitext(base)[0].strip()
    if not stem or stem in (".", ".."):
        return f"task-{task.id}"
    return stem

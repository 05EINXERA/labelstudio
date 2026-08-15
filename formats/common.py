"""Shared helpers for every import/export format.

Everything here is pure or takes an explicit Session/Task — no FastAPI, no
request state — so each piece is unit-testable without a TestClient.

Covers .devnotes/data-refactor/01_PLAN.md § 1.2-1.5:
  - image_size()          task pixel dimensions, with lazy backfill
  - annotation_type_of()  the shape type the DB never stored (gap G1)
  - value_from_name()     the single interop `value` derivation (gap G5)
  - status maps           our vocabulary <-> the interop format's (gap G4)
"""
import json
import logging
import os
import secrets
import re
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, UnidentifiedImageError

import models
from config import DATA_DIR
from schemas import APPROVED_STATUSES

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
# Self-intersection
# ---------------------------------------------------------------------------
#
# Mirror of the detection half of `frontend/js/canvas/untangle.js`. The canvas
# resolves crossings as the annotator edits, so stored geometry should already
# be simple; this exists because "should" is not "is" — rows predate the
# feature, imports arrive from other tools, and model output is deliberately
# never untangled.
#
# Detection only. Nothing here rewrites a user's saved geometry: a
# self-intersecting polygon is a quality problem, not a correctness or security
# one, and silently deleting a lobe of someone's label server-side would be far
# worse than exporting an awkward shape. Exports warn; imports of foreign data
# are the one place normalisation is appropriate, and that happens at the
# import call site, not here.

_INTERSECT_EPS = 1e-9


def _cross_sign(ox: float, oy: float, ax: float, ay: float, bx: float, by: float) -> float:
    return (ax - ox) * (by - oy) - (ay - oy) * (bx - ox)


def segments_properly_intersect(p1: dict, p2: dict, p3: dict, p4: dict) -> bool:
    """True when p1-p2 and p3-p4 cross in their interiors.

    Shared endpoints and collinear overlap are not crossings — adjacent edges of
    any ring share a vertex, so counting those would call every polygon
    self-intersecting. Matches `segmentsIntersect` in untangle.js.
    """
    d1 = _cross_sign(p3["x"], p3["y"], p4["x"], p4["y"], p1["x"], p1["y"])
    d2 = _cross_sign(p3["x"], p3["y"], p4["x"], p4["y"], p2["x"], p2["y"])
    d3 = _cross_sign(p1["x"], p1["y"], p2["x"], p2["y"], p3["x"], p3["y"])
    d4 = _cross_sign(p1["x"], p1["y"], p2["x"], p2["y"], p4["x"], p4["y"])
    if min(abs(d1), abs(d2), abs(d3), abs(d4)) < _INTERSECT_EPS:
        return False
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


def _find_first_self_intersection(points: Sequence[dict]) -> Optional[Tuple[int, int, dict]]:
    n = len(points)
    if n < 4:
        return None
    for i in range(n):
        a1, a2 = points[i], points[(i + 1) % n]
        for j in range(i + 1, n):
            if j == i + 1:
                continue
            if i == 0 and j == n - 1:
                continue
            b1, b2 = points[j], points[(j + 1) % n]
            if segments_properly_intersect(a1, a2, b1, b2):
                point = _segment_intersection_point(a1, a2, b1, b2)
                if point is not None:
                    return i, j, point
    return None


def _segment_intersection_point(p1: dict, p2: dict, p3: dict, p4: dict) -> Optional[dict]:
    denom = (p2["x"] - p1["x"]) * (p4["y"] - p3["y"]) - (p2["y"] - p1["y"]) * (p4["x"] - p3["x"])
    if abs(denom) < _INTERSECT_EPS:
        return None
    t = (
        (p3["x"] - p1["x"]) * (p4["y"] - p3["y"]) - (p3["y"] - p1["y"]) * (p4["x"] - p3["x"])
    ) / denom
    return {"x": p1["x"] + t * (p2["x"] - p1["x"]), "y": p1["y"] + t * (p2["y"] - p1["y"])}


def is_simple_polygon(points: Sequence[dict]) -> bool:
    """True when the ring has no proper self-intersection.

    Rings of fewer than 4 points cannot cross and are always simple.
    """
    return _find_first_self_intersection(points) is None


def untangle_polygon(points: Sequence[dict]) -> Tuple[List[dict], bool]:
    """Python port of `untangleRing` in frontend/js/canvas/untangle.js.

    Used ONLY on import (see formats/coco.py `parse`): normalising a
    self-intersecting ring arriving from an external tool is safe because it is
    foreign data being brought in, not a live rewrite of something a user is
    mid-edit on. Never call this against a stored task's annotations directly —
    that rewrite belongs to the canvas, where the user sees it happen and it
    counts as one undo step; a background/import job silently editing a saved
    annotation's geometry is exactly the kind of surprise rule 1c-adjacent
    guarantees in this codebase exist to prevent.

    No anchor/tie-break here (see the JS docstring for what that means): there
    is no "vertex the user just placed" for an already-authored external file,
    so an exact-tie split falls back to keeping the first loop, deterministically.

    Returns (points, changed).
    """
    original = list(points) if points else []
    if len(original) < 4:
        return original, False

    # Capture the original first vertex before any rewriting so we can rotate
    # the winner back to it at the end.  See the JS counterpart in
    # interactions.js (untangleIfPolygon) and the bug note at
    # .devnotes/bugs/polygon-untangle-startpoint-drift.md.
    original_first = original[0]

    pts = original
    changed = False
    for _ in range(len(original)):
        hit = _find_first_self_intersection(pts)
        if not hit:
            break
        i, j, point = hit
        loop_a = [point] + pts[i + 1:j + 1]
        loop_b = [point] + pts[j + 1:] + pts[:i + 1]
        area_a = polygon_area(loop_a) if len(loop_a) >= 3 else 0.0
        area_b = polygon_area(loop_b) if len(loop_b) >= 3 else 0.0
        if area_a < 0.5 and area_b < 0.5:
            break
        winner = loop_a if area_a >= area_b else loop_b
        if len(winner) < 3:
            break
        pts = winner
        changed = True

    if changed:
        # Rotate the winner so the original first vertex is at index 0, when it
        # survived into the winning loop.  A closed ring is rotationally
        # invariant, so this doesn't change the geometry — it just makes the JS
        # and Python outputs identical for the same input (important for
        # round-trip tests).  If the vertex was in the dropped loop, StopIteration
        # is caught and pts is left as-is.
        _EPS = 1e-9
        try:
            idx = next(
                k for k, p in enumerate(pts)
                if abs(p["x"] - original_first["x"]) < _EPS
                and abs(p["y"] - original_first["y"]) < _EPS
            )
            if idx > 0:
                pts = pts[idx:] + pts[:idx]
        except StopIteration:
            pass  # original first vertex was in the dropped loop — no rotation possible

    return pts, changed


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


def hex_to_rgb(value: Optional[str]) -> Tuple[int, int, int]:
    """'#RRGGBB' -> (r, g, b). Unparseable colours fall back to mid-grey.

    A label colour comes from the UI or an import and is not guaranteed to be
    well-formed; a bad value must not fail the export.
    """
    text = (value or "").strip().lstrip("#")
    if len(text) == 3:  # shorthand #abc
        text = "".join(c * 2 for c in text)
    if len(text) != 6:
        logger.warning("Unparseable label colour %r; using grey.", value)
        return (128, 128, 128)
    try:
        return (int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16))
    except ValueError:
        logger.warning("Unparseable label colour %r; using grey.", value)
        return (128, 128, 128)


def ordered_annotations(task: models.Task) -> List[dict]:
    """A task's real annotations in paint order.

    Later shapes paint over earlier ones, so the order decides what a pixel in
    an overlap reports. Explicit `order` wins where present; otherwise the
    stored sequence is the order the user drew them in.
    """
    try:
        anns = json.loads(task.annotations) if task.annotations else []
    except (ValueError, TypeError) as exc:
        logger.warning("Task %s has unparseable annotations, skipping: %s", task.id, exc)
        return []
    if not isinstance(anns, list):
        return []
    real = [a for a in anns if is_annotation(a)]
    ordered = sorted(enumerate(real), key=lambda pair: (pair[1].get("order", pair[0]), pair[0]))
    return [ann for _, ann in ordered]


def polygon_points(ann: dict) -> Optional[List[Tuple[float, float]]]:
    """Vertices as (x, y) tuples, or None if too few to fill a region.

    A line or single point encloses no area; PIL's ImageDraw would raise.
    """
    points = points_of(ann)
    if len(points) < 3:
        return None
    return [(p["x"], p["y"]) for p in points]


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
    # Every approved-group status (Approved, Verified, Checked, Passed …) is the
    # same fact to an outside consumer: reviewed and signed off. The batch
    # synonym only means something inside this app — it is which export the task
    # belongs to — so it is flattened to "approved" on the way out rather than
    # leaking a vocabulary no interop consumer knows. Generated from the group so
    # a new batch status is exported correctly the day it is added, instead of
    # falling through to the unknown-status warning and being re-imported as New.
    **{s: ("completed", "approved") for s in APPROVED_STATUSES},
    # "Rejected" means "reviewed and sent back for rework", so the base status
    # is in_progress (there *is* more work to do) with the review outcome in
    # externalStatus. Exporting it as "completed" would tell a consumer the work
    # is finished, which is the opposite of what a rejection means.
    "Rejected": ("in_progress", "rejected"),
}

# The inverse. externalStatus wins when it names a review outcome, because that
# is the only way the interop format distinguishes a reviewed task from an
# unreviewed one at the same base status.
#
# Deliberately lossy in one direction: every approved-group status exports as
# "approved" and comes back as "Approved", so a re-imported task loses the batch
# it belonged to. That is correct — the batch is a marker for *our* export
# bookkeeping, and a task arriving from outside was never in one of our batches.
# It keeps the round trip closed (approved stays approved) without inventing a
# batch membership that would then be re-exported as new work.
FROM_EXTERNAL_STATUS: Dict[str, str] = {
    "registered": "New",
    "in_progress": "In Progress",
    "completed": "Completed",
    "approved": "Approved",
    "rejected": "Rejected",
}


def to_external_status(status: Optional[str]) -> Tuple[str, str]:
    """Our status -> (status, externalStatus) in the interop vocabulary."""
    if status in TO_EXTERNAL_STATUS:
        return TO_EXTERNAL_STATUS[status]
    logger.warning("Unknown task status %r; exporting it unchanged.", status)
    return (status or "", "")


def from_external_status(status: Optional[str], external_status: Optional[str] = None) -> str:
    """the interop (status, externalStatus) -> our status."""
    # externalStatus wins over the base status: it is the only field carrying
    # the review outcome, and both review states share their base status with an
    # unreviewed task ("completed" for approved, "in_progress" for rejected).
    # Consulting only the base status would silently discard the review.
    external_key = (external_status or "").lower()
    if external_key in ("approved", "rejected"):
        return FROM_EXTERNAL_STATUS[external_key]
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


# Filesystem-hostile characters in a project name, collapsed to a hyphen so the
# download name is safe on every OS. Kept deliberately narrow: letters, digits,
# and a few separators survive; everything else (slashes, colons, quotes) goes.
_ARCHIVE_NAME_STRIP = re.compile(r"[^A-Za-z0-9._-]+")


def archive_name(project: models.Project, ext: str = "zip") -> str:
    """`<project-slug>-<short-random>.<ext>` for a downloadable bundle.

    The random suffix keeps repeated exports of one project from overwriting
    each other in a downloads folder, and never leaks a database id. Falls back
    to the project id when the name sanitises to nothing (e.g. a name of only
    punctuation).
    """
    raw = (project.name or "").strip()
    base = _ARCHIVE_NAME_STRIP.sub("-", raw).strip("-.")
    if not base:
        base = f"project-{project.id}"
    suffix = secrets.token_hex(3)  # 6 hex chars
    return f"{base}-{suffix}.{ext}"

"""Structural comparison of two annotation blobs.

Pure: no DB, no FastAPI, no `state` — everything here takes JSON text and
returns a verdict, so it is testable without a server (docs/ARCHITECTURE.md
§ 2.1).

The question this module answers is narrow and load-bearing:

    "Can this save possibly have destroyed anything?"

`task_annotation_history` exists to preserve what a write is about to replace.
When a write is *purely additive* — every object still there, every field
unchanged, every polygon vertex still present, possibly with new ones — the
value being replaced is fully contained in the value replacing it. Nothing is
lost, so there is nothing to preserve, and a snapshot of it is dead weight.

Why this matters here: a freehand polygon is drawn vertex by vertex while the
autosave timer fires every few seconds. The object *count* never changes, so
these look like no-op saves in the history counters, but the blobs genuinely
differ — one polygon gained a point. Observed in production: a 126 kB blob
rewritten in full to append ~25 bytes of new vertex, thousands of times over a
shift. That is what drove `task_annotation_history` to 1.2 GB.

The bias throughout is deliberate and one-directional:

    **When in doubt, return False.**

A wrongly-skipped history row is annotation work that cannot be recovered. A
wrongly-kept row costs some disk. Those are not symmetric, so every parse
failure, every unexpected shape, and every ambiguity resolves to "keep the
row".
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

# Fields compared for exact equality. `points` is handled separately because
# growth there is the whole point of this module; everything else must match
# byte-for-byte or the save is not purely additive.
_POINTS_FIELD = "points"

# Bounding-box fields. For an object that carries `points`, these are not
# independent data: the canvas recomputes all four from the vertices on every
# edit (`updateAnnotationBounds` in frontend/js/canvas/geometry.js — x/y are the
# min, width/height the extent, each rounded to 2dp by `round` in utils.js).
#
# That matters here because extending a polygon *outward* moves its bounds, so a
# genuine vertex append arrives with `width` changed too. Comparing those fields
# as ordinary data therefore rejects exactly the case this module exists to
# recognise — measured on production history, that is most of them.
#
# They are only treated as derived when the object actually has points to derive
# them from, and only when the observed value matches what the points imply. A
# box (no `points`) keeps them as plain fields, where a changed width really is
# the user resizing something and must be preserved.
_BBOX_FIELDS = ("x", "y", "width", "height")

# Matches `round()` in frontend/js/utils.js: Math.round(v * 100) / 100.
_BBOX_DECIMALS = 2

# Slack for the bbox comparison, a little looser than the 2dp the client rounds
# to. The client rounds points and bounds independently, so recomputing from
# rounded points can land one ulp off the stored value; a stricter check would
# reject an append over floating-point noise. Still far tighter than any real
# edit — a user cannot resize a shape by 0.01px.
_BBOX_TOLERANCE = 0.011


def _parse(blob: Optional[str]) -> Optional[List[Any]]:
    """JSON text -> list of objects, or None when it is not that.

    None means "unusable", and every caller treats unusable as "not an append".
    """
    if blob is None:
        return None
    if not isinstance(blob, str):
        return None
    stripped = blob.strip()
    if not stripped:
        return None
    try:
        parsed = json.loads(stripped)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, list):
        return None
    return parsed


def _object_key(obj: Any) -> Optional[str]:
    """Stable identity for an annotation object.

    Objects carry a uuid `id`. Anything without one cannot be matched across
    two blobs, so it makes the whole comparison indeterminate.
    """
    if not isinstance(obj, dict):
        return None
    raw = obj.get("id")
    if raw is None:
        return None
    return str(raw)


def _index_by_id(objects: List[Any]) -> Optional[Dict[str, Dict[str, Any]]]:
    """Map id -> object, or None if any object lacks a usable unique id.

    Duplicate ids are treated as unusable rather than last-one-wins: if two
    objects share an id we cannot reason about which one survived, and guessing
    is exactly the kind of ambiguity that must fall back to keeping the row.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for obj in objects:
        key = _object_key(obj)
        if key is None:
            return None
        if key in out:
            return None
        out[key] = obj  # type: ignore[assignment]
    return out


def _points_of(obj: Dict[str, Any]) -> Tuple[bool, List[Any]]:
    """(has_points, points). `has_points` is False when the field is absent.

    A `points` value that exists but is not a list is reported as present with
    an empty list, which forces the caller into the not-equal path.
    """
    if _POINTS_FIELD not in obj:
        return False, []
    value = obj.get(_POINTS_FIELD)
    if not isinstance(value, list):
        return True, []
    return True, value


def _is_subsequence(old: List[Any], new: List[Any]) -> bool:
    """True when `old` appears inside `new` in order, with nothing removed.

    Deliberately a subsequence test rather than a prefix test. The observed
    production case inserted a vertex at index 877 of 1233 — the canvas adds
    points where the cursor is, not only at the tail — so a `new[:len(old)] ==
    old` check would miss the common case and defeat the whole optimisation.

    Order is still required: a reordered polygon is not an append, because
    reordering changes the shape and the original ordering is then lost.
    """
    if len(old) > len(new):
        return False
    if not old:
        return True

    i = 0
    for candidate in new:
        if candidate == old[i]:
            i += 1
            if i == len(old):
                return True
    return False


def _derived_bbox(points: List[Any]) -> Optional[Dict[str, float]]:
    """The bounding box `points` implies, or None when it cannot be computed.

    Mirrors `updateAnnotationBounds` (frontend/js/canvas/geometry.js): x/y are
    the minima, width/height the extents. Rounded to the same 2dp the client
    uses so the two can be compared at all.
    """
    if not points:
        return None

    xs: List[float] = []
    ys: List[float] = []
    for point in points:
        if not isinstance(point, dict):
            return None
        try:
            xs.append(float(point.get("x", 0) or 0))
            ys.append(float(point.get("y", 0) or 0))
        except (TypeError, ValueError):
            return None

    if not xs or not ys:
        return None

    min_x, min_y = min(xs), min(ys)
    return {
        "x": round(min_x, _BBOX_DECIMALS),
        "y": round(min_y, _BBOX_DECIMALS),
        "width": round(max(xs) - min_x, _BBOX_DECIMALS),
        "height": round(max(ys) - min_y, _BBOX_DECIMALS),
    }


def _bbox_is_consistent_with_points(obj: Dict[str, Any], points: List[Any]) -> bool:
    """True when the object's stored bbox is what its points imply.

    The gate on treating the bbox as derived. If an object's stored bounds do
    not match its own vertices, something other than `updateAnnotationBounds`
    set them, and this module has no business deciding they are redundant — so
    the caller falls back to comparing them as ordinary fields.

    Absent bbox fields are consistent by definition: there is nothing that could
    disagree, and nothing to lose.
    """
    derived = _derived_bbox(points)
    if derived is None:
        return False

    for field in _BBOX_FIELDS:
        if field not in obj:
            continue
        stored = obj.get(field)
        if isinstance(stored, bool) or not isinstance(stored, (int, float)):
            return False
        if abs(float(stored) - derived[field]) > _BBOX_TOLERANCE:
            return False
    return True


def _object_is_pure_append(old: Any, new: Any) -> bool:
    """True when `new` contains everything `old` had, plus possibly more."""
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False

    # A field present before must still be present. A field *added* is fine
    # only when it carries no prior value to lose, which is true by definition
    # for a key that did not exist.
    for field in old:
        if field not in new:
            return False

    old_has_points, old_points = _points_of(old)
    new_has_points, new_points = _points_of(new)

    # The bbox may be skipped as derived only when *both* sides are genuinely
    # point-backed and each side's stored bounds agree with its own vertices.
    # Anything else — a box, a hand-set bbox, a shape whose bounds were written
    # by something other than the canvas — compares them as ordinary fields,
    # because then a changed width is real data and not a recomputation.
    bbox_is_derived = (
        old_has_points
        and new_has_points
        and _bbox_is_consistent_with_points(old, old_points)
        and _bbox_is_consistent_with_points(new, new_points)
    )

    for field, old_value in old.items():
        if field == _POINTS_FIELD:
            continue
        if bbox_is_derived and field in _BBOX_FIELDS:
            continue
        if new.get(field) != old_value:
            return False

    if not old_has_points:
        # Nothing to lose on this axis. A brand new `points` list is additive.
        return True
    if not new_has_points:
        return False

    if old_points == new_points:
        return True
    return _is_subsequence(old_points, new_points)


def is_pure_append(old_blob: Optional[str], new_blob: Optional[str]) -> bool:
    """True when replacing `old_blob` with `new_blob` cannot lose annotation work.

    Requires all of:

    * both blobs parse to JSON lists
    * every object in `old` carries a unique id, and still exists in `new`
    * for each surviving object, no field was removed or changed, except that
      `points` may grow while preserving every existing vertex in order

    New objects in `new` are unconstrained — adding work is the case this is
    built to recognise.

    Returns False on anything unparseable, unmatched, ambiguous or shrinking.
    That conservatism is the contract: callers use this to decide whether to
    *skip* preserving history, so a False costs a little disk and a wrong True
    costs somebody's annotations.
    """
    old_objects = _parse(old_blob)
    new_objects = _parse(new_blob)

    if old_objects is None or new_objects is None:
        return False

    # An empty prior blob has nothing to preserve, but that case is already
    # handled by the caller's own empty check; answering True here keeps the
    # function total and consistent with "old is contained in new".
    if not old_objects:
        return True

    if len(new_objects) < len(old_objects):
        return False

    old_by_id = _index_by_id(old_objects)
    new_by_id = _index_by_id(new_objects)
    if old_by_id is None or new_by_id is None:
        return False

    for key, old_obj in old_by_id.items():
        new_obj = new_by_id.get(key)
        if new_obj is None:
            return False
        if not _object_is_pure_append(old_obj, new_obj):
            return False

    return True


def total_point_count(blob: Optional[str]) -> int:
    """Total vertices across every object, or -1 when the blob is unusable.

    The object count alone cannot see vertex-level loss: a polygon truncated
    from 1233 points to 3 leaves the object count identical. Recording this
    alongside the object count is what makes that visible in the history table
    and in the diagnosis queries (.devnotes/query/manual.md).
    """
    objects = _parse(blob)
    if objects is None:
        return -1

    total = 0
    for obj in objects:
        if not isinstance(obj, dict):
            continue
        has_points, points = _points_of(obj)
        if has_points:
            total += len(points)
    return total

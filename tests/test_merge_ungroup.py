"""Ungroup must undo a merge, not just unlink a visual group.

Group does two different things depending on whether the selected shapes touch:

  - touching shapes are merged into one polygon (the union), the shapes that
    went into it are deleted, and the survivor is flagged `mergedFromGroup`;
  - shapes that never touch are only linked, via a shared `groupId`.

Ungroup used to look at `groupId` alone, so after the common case -- select two
overlapping shapes, hit Group -- there was no `groupId` anywhere and the button
sat permanently disabled with no way back except Undo.

The merge survivor now records the shapes it was built from in `mergedParts`,
and Ungroup restores them. This ports that restore to Python the same way
test_polygon_union.py ports the union geometry, so it is covered without a JS
test runner, plus source assertions for the wiring that cannot be ported.
"""
import os
import re

import pytest

FRONTEND_JS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "frontend", "js"
)


def _round(value):
    """Mirrors `round` in frontend/js/utils.js (2 decimal places)."""
    return round(value, 2)


def merged_parts_of(annotation):
    """Port of `mergedPartsOf`: accepts the flat and the `extra`-nested spelling."""
    parts = annotation.get("mergedParts")
    if parts is None:
        parts = (annotation.get("extra") or {}).get("mergedParts")
    if isinstance(parts, list) and len(parts) > 1:
        return parts
    return None


def merged_parts_origin(parts):
    """Port of `mergedPartsOrigin`: top-left corner of the recorded shapes."""
    xs = [p["x"] for part in parts for p in part.get("points", [])]
    ys = [p["y"] for part in parts for p in part.get("points", [])]
    return {"x": min(xs) if xs else 0, "y": min(ys) if ys else 0}


def _bounds(shape):
    """Port of `updateAnnotationBounds`."""
    xs = [p["x"] for p in shape["points"]]
    ys = [p["y"] for p in shape["points"]]
    shape["x"] = _round(min(xs))
    shape["y"] = _round(min(ys))
    shape["width"] = _round(max(xs) - shape["x"])
    shape["height"] = _round(max(ys) - shape["y"])
    return shape


def ungroup_selected(annotations, selected_ids, new_id=None):
    """Port of `ungroupSelectedAnnotations`.

    Mutates `annotations`/`selected_ids` in place and returns (changed, status).
    """
    counter = iter(range(1000))
    if new_id is None:
        new_id = lambda: "new-%d" % next(counter)  # noqa: E731

    restored_parts = 0
    unlinked = False

    for survivor in [a for a in annotations if a["id"] in selected_ids]:
        parts = merged_parts_of(survivor)
        if not parts:
            if survivor.get("groupId"):
                del survivor["groupId"]
                unlinked = True
            continue

        origin = merged_parts_origin(parts)
        dx = _round((survivor.get("x") or 0) - origin["x"])
        dy = _round((survivor.get("y") or 0) - origin["y"])

        restored = []
        for index, part in enumerate(parts):
            shape = dict(part)
            shape["id"] = survivor["id"] if index == 0 else new_id()
            shape["points"] = [
                {"x": _round(p["x"] + dx), "y": _round(p["y"] + dy)}
                for p in part.get("points", [])
            ]
            restored.append(_bounds(shape))

        at = annotations.index(survivor)
        annotations[at:at + 1] = restored

        selected_ids.discard(survivor["id"])
        for shape in restored:
            selected_ids.add(shape["id"])
        restored_parts += len(restored)

    if restored_parts:
        return True, "Merge undone — %d shapes restored" % restored_parts
    if unlinked:
        return True, "Ungrouped"
    return False, None


def _square(x, y, size=10):
    return [
        {"x": x, "y": y},
        {"x": x + size, "y": y},
        {"x": x + size, "y": y + size},
        {"x": x, "y": y + size},
    ]


def _merged(parts, points=None, **overrides):
    """A merge survivor: the union outline plus the shapes it came from."""
    shape = {
        "id": "survivor",
        "type": "polygon",
        "labelId": "cat",
        "points": points or _square(0, 0, 15),
        "mergedFromGroup": True,
        "mergedParts": parts,
    }
    shape.update(overrides)
    return _bounds(shape)


# ---------------------------------------------------------------------------
# Restoring a merge
# ---------------------------------------------------------------------------

def test_ungroup_restores_the_shapes_a_merge_consumed():
    """The regression: Group merged, and there was no way back."""
    parts = [
        {"id": "a", "type": "polygon", "labelId": "cat", "points": _square(0, 0)},
        {"id": "b", "type": "polygon", "labelId": "cat", "points": _square(5, 5)},
    ]
    annotations = [_merged(parts)]
    selected = {"survivor"}

    changed, status = ungroup_selected(annotations, selected)

    assert changed is True
    assert len(annotations) == 2, "both pre-merge shapes must come back"
    assert [a["points"] for a in annotations] == [_square(0, 0), _square(5, 5)]
    assert "restored" in status


def test_restored_shapes_are_independent_objects():
    """No groupId and no merge flags -- they select and move on their own."""
    parts = [
        {"id": "a", "type": "polygon", "labelId": "cat", "points": _square(0, 0)},
        {"id": "b", "type": "polygon", "labelId": "cat", "points": _square(5, 5)},
    ]
    annotations = [_merged(parts)]
    ungroup_selected(annotations, {"survivor"})

    for shape in annotations:
        assert "groupId" not in shape
        assert not shape.get("mergedFromGroup")
        assert not shape.get("mergedParts")


def test_restored_shapes_get_distinct_ids_and_the_survivor_keeps_its_own():
    """Duplicate ids would collide on save -- the server regenerates them."""
    parts = [
        {"id": "a", "type": "polygon", "labelId": "cat", "points": _square(0, 0)},
        {"id": "b", "type": "polygon", "labelId": "cat", "points": _square(5, 5)},
        {"id": "c", "type": "polygon", "labelId": "cat", "points": _square(8, 8)},
    ]
    annotations = [_merged(parts)]
    ungroup_selected(annotations, {"survivor"})

    ids = [a["id"] for a in annotations]
    assert ids[0] == "survivor", "the survivor keeps its id so references resolve"
    assert len(set(ids)) == len(ids), "restored shapes must not share an id"


def test_all_restored_shapes_end_up_selected():
    """The selection must not keep naming the id of a shape that is now one of several."""
    parts = [
        {"id": "a", "type": "polygon", "labelId": "cat", "points": _square(0, 0)},
        {"id": "b", "type": "polygon", "labelId": "cat", "points": _square(5, 5)},
    ]
    annotations = [_merged(parts)]
    selected = {"survivor"}
    ungroup_selected(annotations, selected)

    assert selected == {a["id"] for a in annotations}


def test_restored_shapes_keep_their_position_in_the_z_order():
    """Splicing in place -- restoring must not shove the shapes to the front."""
    parts = [
        {"id": "a", "type": "polygon", "labelId": "cat", "points": _square(0, 0)},
        {"id": "b", "type": "polygon", "labelId": "cat", "points": _square(5, 5)},
    ]
    below = {"id": "below", "type": "polygon", "points": _square(50, 50)}
    above = {"id": "above", "type": "polygon", "points": _square(70, 70)}
    annotations = [below, _merged(parts), above]

    ungroup_selected(annotations, {"survivor"})

    assert annotations[0]["id"] == "below"
    assert annotations[-1]["id"] == "above"
    assert len(annotations) == 4


def test_bounds_are_recomputed_for_each_restored_shape():
    """Stale bounds from the merged outline would break hit-testing."""
    parts = [
        {"id": "a", "type": "polygon", "labelId": "cat", "points": _square(0, 0),
         "x": 0, "y": 0, "width": 99, "height": 99},
        {"id": "b", "type": "polygon", "labelId": "cat", "points": _square(5, 5)},
    ]
    annotations = [_merged(parts)]
    ungroup_selected(annotations, {"survivor"})

    first = annotations[0]
    assert (first["width"], first["height"]) == (10, 10)


# ---------------------------------------------------------------------------
# A merged shape that has been moved since
# ---------------------------------------------------------------------------

def test_restore_follows_a_merged_shape_that_was_dragged():
    """Moves translate every point by one delta; the restore replays it.

    Otherwise Ungroup would drop the shapes back at the coordinates they were
    merged at, far from the outline the annotator is looking at.
    """
    parts = [
        {"id": "a", "type": "polygon", "labelId": "cat", "points": _square(0, 0)},
        {"id": "b", "type": "polygon", "labelId": "cat", "points": _square(5, 5)},
    ]
    # The merged union originally spanned (0,0)-(15,15); it has since been
    # dragged 100 right and 40 down.
    survivor = _merged(parts, points=_square(100, 40, 15))
    annotations = [survivor]

    ungroup_selected(annotations, {"survivor"})

    assert [a["points"] for a in annotations] == [
        _square(100, 40),
        _square(105, 45),
    ], "restored shapes must land under the moved outline"


def test_an_unmoved_merge_restores_exactly_where_it_was():
    """The delta must be zero when nothing moved -- no drift from rounding."""
    parts = [
        {"id": "a", "type": "polygon", "labelId": "cat", "points": _square(3, 7)},
        {"id": "b", "type": "polygon", "labelId": "cat", "points": _square(8, 12)},
    ]
    annotations = [_merged(parts, points=_square(3, 7, 15))]
    ungroup_selected(annotations, {"survivor"})

    assert [a["points"] for a in annotations] == [_square(3, 7), _square(8, 12)]


# ---------------------------------------------------------------------------
# The plain visual group still works
# ---------------------------------------------------------------------------

def test_ungroup_still_unlinks_a_plain_visual_group():
    """Shapes that never touched are only linked; Ungroup must still unlink them."""
    annotations = [
        {"id": "a", "type": "polygon", "points": _square(0, 0), "groupId": "g1"},
        {"id": "b", "type": "polygon", "points": _square(90, 90), "groupId": "g1"},
    ]
    changed, status = ungroup_selected(annotations, {"a", "b"})

    assert changed is True
    assert status == "Ungrouped"
    assert all("groupId" not in a for a in annotations)
    assert len(annotations) == 2, "unlinking must not duplicate anything"


def test_a_selection_can_mix_a_merge_and_a_plain_group():
    annotations = [
        _merged([
            {"id": "a", "type": "polygon", "points": _square(0, 0)},
            {"id": "b", "type": "polygon", "points": _square(5, 5)},
        ]),
        {"id": "c", "type": "polygon", "points": _square(90, 90), "groupId": "g1"},
        {"id": "d", "type": "polygon", "points": _square(200, 200), "groupId": "g1"},
    ]
    changed, _ = ungroup_selected(annotations, {"survivor", "c", "d"})

    assert changed is True
    assert len(annotations) == 4, "the merge restored two shapes"
    assert all("groupId" not in a for a in annotations)


def test_ungroup_reports_no_change_when_nothing_is_grouped_or_merged():
    """The caller pops its undo snapshot on false -- a no-op must not cost an undo step."""
    annotations = [{"id": "a", "type": "polygon", "points": _square(0, 0)}]
    changed, status = ungroup_selected(annotations, {"a"})

    assert changed is False
    assert status is None


def test_unselected_shapes_are_left_alone():
    annotations = [
        _merged([
            {"id": "a", "type": "polygon", "points": _square(0, 0)},
            {"id": "b", "type": "polygon", "points": _square(5, 5)},
        ]),
        {"id": "other", "type": "polygon", "points": _square(90, 90), "groupId": "g1"},
    ]
    ungroup_selected(annotations, {"other"})

    assert merged_parts_of(annotations[0]) is not None, "an unselected merge stays merged"


# ---------------------------------------------------------------------------
# Round-tripping through the server
# ---------------------------------------------------------------------------

def test_merged_parts_are_read_from_the_extra_blob_after_a_reload():
    """Unmodeled fields come back nested under `extra` (api/routers/tasks.py).

    A reloaded merge must still be reversible, so both spellings are accepted --
    same as `mergedFromGroup` in draw.js.
    """
    parts = [
        {"id": "a", "type": "polygon", "points": _square(0, 0)},
        {"id": "b", "type": "polygon", "points": _square(5, 5)},
    ]
    reloaded = _bounds({
        "id": "survivor",
        "type": "polygon",
        "points": _square(0, 0, 15),
        "extra": {"mergedFromGroup": True, "mergedParts": parts},
    })
    annotations = [reloaded]

    changed, _ = ungroup_selected(annotations, {"survivor"})

    assert changed is True
    assert len(annotations) == 2


def test_a_single_recorded_part_is_not_treated_as_a_merge():
    """A merge always consumes at least two shapes; one is malformed data."""
    assert merged_parts_of({"mergedParts": [{"id": "a", "points": _square(0, 0)}]}) is None
    assert merged_parts_of({"mergedParts": []}) is None
    assert merged_parts_of({}) is None


def test_merged_parts_is_not_in_the_servers_known_keys():
    """It must fall through to `extra` rather than be silently dropped on save."""
    tasks_router = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "api", "routers", "tasks.py"
    )
    with open(tasks_router, "r", encoding="utf-8") as f:
        source = f.read()

    known_keys = re.search(r"known_keys = \{([^}]+)\}", source)
    assert known_keys, "known_keys set not found in tasks.py"
    assert "mergedParts" not in known_keys.group(1)
    # The catch-all is what carries it: everything not in known_keys goes to extra.
    assert "k not in known_keys" in source


# ---------------------------------------------------------------------------
# Wiring that cannot be ported
# ---------------------------------------------------------------------------

def _read(*parts):
    with open(os.path.join(FRONTEND_JS_DIR, *parts), "r", encoding="utf-8") as f:
        return f.read()


def test_the_merge_records_the_shapes_it_consumed():
    source = _read("canvas", "interactions.js")

    assert "survivor.mergedParts = preMergeParts" in source, (
        "the merge must record its source shapes or Ungroup has nothing to restore"
    )
    # Flattening keeps one Ungroup enough: a member that was itself a merge
    # contributes its own parts rather than an intermediate union.
    assert "mergedPartsOf(a) || [snapshotAnnotation(a)]" in source, (
        "nested merges must flatten to the original shapes"
    )
    # The points must be cloned, or editing the merged shape would mutate the
    # record through the shared array and corrupt the restore.
    assert "points: annotationPoints(annotation)" in source, (
        "snapshotAnnotation must clone points rather than share the array"
    )


def test_ungroup_button_is_enabled_for_a_merged_shape():
    """The bug: the button checked groupId only, which a merge never leaves."""
    source = _read("components", "workspace.js")

    enable_rule = re.search(
        r"ungroupButton\.disabled = ([^;]+);", source, re.S
    )
    assert enable_rule, "ungroupButton enable rule not found"
    rule = enable_rule.group(1)

    assert "mergedParts" in rule, (
        "a merged shape carries no groupId, so checking groupId alone leaves "
        "Ungroup permanently disabled after a merge"
    )
    assert "groupId" in rule, "a plain visual group must still enable the button"
    assert "extra?.mergedParts" in rule, (
        "after a reload the field arrives nested under extra"
    )


def test_ungroup_handler_delegates_and_guards_the_undo_snapshot():
    source = _read("canvas", "interactions.js")

    assert "export function ungroupSelectedAnnotations" in source
    assert "if (ungroupSelectedAnnotations()) {" in source, (
        "the click handler must act on the return value"
    )
    # A no-op must pop the snapshot it took, or Ungroup on nothing costs an undo.
    handler = source.split('ungroupButton.addEventListener("click"')[1][:400]
    assert "state.history.pop()" in handler
    assert "save()" in handler, "a restore changes the annotations and must persist"


def test_a_split_clears_the_stale_merge_record():
    """Splitting a merged polygon discards the union -- the record must go with it."""
    source = _read("canvas", "interactions.js")

    split_block = source.split("splitClosedPolygonAtIntersections(annotation.points)")[1][:1600]
    assert "delete annotation.mergedParts" in split_block, (
        "a split must drop the pre-merge record or Ungroup would resurrect "
        "geometry the split discarded"
    )
    assert "delete annotation.extra.mergedParts" in split_block, (
        "the nested spelling must be cleared too"
    )


def test_changed_modules_are_version_bumped():
    """Module imports are version-pinned; clients need a new pin to pick this up."""
    for importer in ("init.js",):
        source = _read(importer)
        assert "interactions.js?v=12" in source or "workspace.js?v=10" in source

    # No stale pins left behind anywhere.
    for root, _, files in os.walk(FRONTEND_JS_DIR):
        for name in files:
            if not name.endswith(".js"):
                continue
            with open(os.path.join(root, name), "r", encoding="utf-8") as f:
                content = f.read()
            assert "interactions.js?v=11" not in content, f"stale pin in {name}"
            assert "workspace.js?v=9" not in content, f"stale pin in {name}"

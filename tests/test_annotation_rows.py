"""Blob-dict <-> Annotation-row conversion.

The normalisation's correctness rests entirely on this mapping being lossless:
every annotation in the database passes through it, and anything it drops is
data destroyed by the migration. The cases below are the ones the real-data
survey turned up (.devnotes/performance-fixes/06_PROGRESS.md D1), not
hypotheticals.
"""

import json

import pytest

from formats.annotation_rows import (
    dict_to_row_kwargs,
    row_to_dict,
    rows_to_dicts,
)


class FakeRow:
    """An Annotation row without a database behind it."""

    def __init__(self, kwargs):
        self.__dict__.update(kwargs)


def roundtrip(ann: dict, task_id: int = 1) -> dict:
    return row_to_dict(FakeRow(dict_to_row_kwargs(ann, task_id)))


# ---------------------------------------------------------------------------
# The shapes that actually occur in the data
# ---------------------------------------------------------------------------

def test_polygon_with_dict_points_roundtrips():
    ann = {
        "id": "a1",
        "type": "polygon",
        "labelId": "L1",
        "points": [{"x": 1.5, "y": 2.5}, {"x": 3.0, "y": 4.0}],
        "x": 1.5, "y": 2.5, "width": 1.5, "height": 1.5,
    }
    assert roundtrip(ann) == ann


def test_polygon_with_list_points_roundtrips():
    """3,001 real annotations store points as [[x, y], ...], not [{x, y}, ...].

    Both forms must survive verbatim: `formats.common.points_of` copes with
    each, and canonicalising one into the other would be a behaviour change
    hidden inside a storage migration.
    """
    ann = {"id": "a1", "type": "polygon", "points": [[0.0, 0], [0.5, 1]]}
    assert roundtrip(ann) == ann


def test_annotation_without_type_keeps_absent_type():
    """4,059 real annotations carry no `type`.

    `formats.common.is_annotation` reads an absent type as a real shape, so
    defaulting it to 'polygon' would silently reclassify a quarter of the
    dataset.
    """
    ann = {"id": "a1", "labelId": "L1", "points": [{"x": 1, "y": 2}]}
    out = roundtrip(ann)
    assert "type" not in out
    assert out == ann


def test_comment_roundtrips():
    ann = {"id": "c1", "type": "comment", "text": "look here", "x": 10.0, "y": 20.0}
    assert roundtrip(ann) == ann


# ---------------------------------------------------------------------------
# Unmodelled fields — the `extra` catch-all
# ---------------------------------------------------------------------------

def test_unmodelled_fields_survive_via_extra():
    """`label`, `visible`, `promptPoints`, `source`, `author`, `w`, `h` are all
    real fields no column models. Losing them is data loss."""
    ann = {
        "id": "a1", "type": "rect", "label": "car", "visible": True,
        "x": 0.0, "y": 0.0, "w": 5, "h": 5,
        "promptPoints": [[1, 2]], "promptLabels": [1], "source": "sam",
        "author": "alice",
    }
    assert roundtrip(ann) == ann


def test_extra_is_stored_as_json_not_columns():
    kwargs = dict_to_row_kwargs({"id": "a1", "label": "car", "visible": False}, 1)
    assert json.loads(kwargs["extra"]) == {"label": "car", "visible": False}


def test_no_extra_column_when_everything_is_modelled():
    kwargs = dict_to_row_kwargs({"id": "a1", "type": "polygon"}, 1)
    assert kwargs["extra"] is None


def test_nested_extra_is_merged_not_renested():
    """A dict that has already been through storage carries its unmodelled
    fields at the top level; re-wrapping them would deepen on every save."""
    ann = {"id": "a1", "extra": {"label": "car"}, "visible": True}
    out = roundtrip(ann)
    assert out["label"] == "car"
    assert out["visible"] is True
    assert "extra" not in out


# ---------------------------------------------------------------------------
# Coercion and edge cases
# ---------------------------------------------------------------------------

def test_numeric_strings_are_coerced_to_floats():
    """The real data stores coordinates as strings on older rows."""
    kwargs = dict_to_row_kwargs({"id": "a1", "x": "981.75", "y": "541.08"}, 1)
    assert kwargs["x"] == pytest.approx(981.75)
    assert kwargs["y"] == pytest.approx(541.08)


def test_unparseable_coordinate_becomes_null_not_an_error():
    kwargs = dict_to_row_kwargs({"id": "a1", "x": "not-a-number"}, 1)
    assert kwargs["x"] is None


def test_missing_id_is_minted():
    """Four real annotations have no id; the save path already mints one."""
    kwargs = dict_to_row_kwargs({"type": "polygon"}, 1)
    assert kwargs["id"]
    assert len(kwargs["id"]) >= 32


def test_task_id_comes_from_the_argument_not_the_payload():
    """A payload must not be able to write itself onto another task."""
    kwargs = dict_to_row_kwargs({"id": "a1", "task_id": 999}, 7)
    assert kwargs["task_id"] == 7


def test_sparse_output_omits_absent_fields():
    """Emitting explicit nulls would change what every export sees."""
    out = roundtrip({"id": "a1", "type": "polygon"})
    assert out == {"id": "a1", "type": "polygon"}
    for absent in ("text", "order", "groupId", "color", "x"):
        assert absent not in out


def test_zero_and_false_are_preserved_not_dropped():
    """0 and False are falsy but meaningful — a truthiness test would lose them."""
    ann = {"id": "a1", "x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0,
           "order": 0, "visible": False}
    assert roundtrip(ann) == ann


def test_unserialisable_field_is_dropped_not_raised():
    """One bad field must not fail an otherwise good save."""
    kwargs = dict_to_row_kwargs({"id": "a1", "weird": {1, 2, 3}}, 1)
    assert kwargs["extra"] is None


def test_rows_to_dicts_preserves_order():
    rows = [FakeRow(dict_to_row_kwargs({"id": f"a{i}", "order": i}, 1)) for i in range(3)]
    assert [d["id"] for d in rows_to_dicts(rows)] == ["a0", "a1", "a2"]


def test_unparseable_points_in_storage_degrade_to_absent():
    """A corrupt row must not break the whole task's read."""
    row = FakeRow(dict_to_row_kwargs({"id": "a1", "type": "polygon"}, 1))
    row.points = "{not json"
    out = row_to_dict(row)
    assert out["id"] == "a1"
    assert "points" not in out

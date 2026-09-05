"""The `_parsed` variants of the annotation-diff helpers.

`is_pure_append` and `total_point_count` each parsed their blobs internally.
On the save path that meant the stored blob was parsed twice and the incoming
blob three times, at ~60 ms of GIL-held CPU per parse on a 5 MB blob — work
that stalls every other request in the process. The `_parsed` variants let the
caller parse once and share.

See .devnotes/server-issue-diagnosis/evidence/07_REMAINING_COSTS.md.

These tests pin the property the refactor depends on: the `_parsed` form must
answer exactly what the blob-taking form answers, including for the "unusable"
inputs where the sentinel matters. The behavioural cases for the comparison
itself live in test_annotation_diff.py and are unchanged.
"""

import json

import pytest

from formats.annotation_diff import (
    _parse,
    is_pure_append,
    is_pure_append_parsed,
    total_point_count,
    total_point_count_parsed,
)


def _blob(objects):
    return json.dumps(objects)


def _box(oid="a", x=10):
    return {"id": oid, "type": "rect", "x": x, "y": 20,
            "width": 30, "height": 40, "labelId": 1}


def _poly(oid="p", points=None):
    return {"id": oid, "type": "polygon", "labelId": 2,
            "points": points if points is not None else [{"x": 1, "y": 1}]}


# Inputs that are not a usable JSON array. Each must reach the same answer
# through both forms — this is where a sloppy refactor silently changes the
# contract, and for is_pure_append a wrong True destroys annotations.
UNUSABLE = [None, "", "   ", '{"not": "a list"}', '"a string"', "42",
            "true", "[1,2", "not json at all"]


@pytest.mark.parametrize("bad", UNUSABLE)
def test_unusable_blobs_agree_across_both_forms(bad):
    good = _blob([_box("a")])
    assert is_pure_append(bad, good) == is_pure_append_parsed(_parse(bad), _parse(good))
    assert is_pure_append(good, bad) == is_pure_append_parsed(_parse(good), _parse(bad))
    assert total_point_count(bad) == total_point_count_parsed(_parse(bad))


@pytest.mark.parametrize("bad", UNUSABLE)
def test_unusable_blobs_are_never_a_pure_append(bad):
    """The direction that matters: unusable must never answer True."""
    good = _blob([_box("a")])
    assert is_pure_append_parsed(_parse(bad), _parse(good)) is False
    assert is_pure_append_parsed(_parse(good), _parse(bad)) is False
    assert total_point_count_parsed(_parse(bad)) == -1


def test_appending_an_object_agrees_across_both_forms():
    old = _blob([_box("a")])
    new = _blob([_box("a"), _box("b", x=99)])
    assert is_pure_append(old, new) is True
    assert is_pure_append_parsed(_parse(old), _parse(new)) is True


def test_removing_an_object_agrees_across_both_forms():
    old = _blob([_box("a"), _box("b")])
    new = _blob([_box("a")])
    assert is_pure_append(old, new) is False
    assert is_pure_append_parsed(_parse(old), _parse(new)) is False


def test_growing_a_polygon_agrees_across_both_forms():
    old_points = [{"x": i, "y": i} for i in range(20)]
    new_points = list(old_points)
    new_points.insert(7, {"x": 99.5, "y": 12.25})
    old = _blob([_poly("p", old_points)])
    new = _blob([_poly("p", new_points)])
    assert is_pure_append(old, new) is True
    assert is_pure_append_parsed(_parse(old), _parse(new)) is True


def test_truncating_a_polygon_agrees_across_both_forms():
    old = _blob([_poly("p", [{"x": i, "y": i} for i in range(50)])])
    new = _blob([_poly("p", [{"x": 0, "y": 0}])])
    assert is_pure_append(old, new) is False
    assert is_pure_append_parsed(_parse(old), _parse(new)) is False


def test_empty_prior_blob_agrees_across_both_forms():
    new = _blob([_box("a")])
    assert is_pure_append("[]", new) is True
    assert is_pure_append_parsed(_parse("[]"), _parse(new)) is True


def test_total_point_count_agrees_across_both_forms():
    blob = _blob([_poly("p", [{"x": 1, "y": 1}, {"x": 2, "y": 2}]),
                  _poly("q", [{"x": 3, "y": 3}]),
                  _box("b")])
    assert total_point_count(blob) == 3
    assert total_point_count_parsed(_parse(blob)) == 3


def test_parsed_form_does_not_mutate_its_input():
    """The caller shares one parsed list between several consumers.

    If any of them mutated it, the next consumer would see altered data — so
    this pins that they do not.
    """
    old_objects = _parse(_blob([_poly("p", [{"x": 1, "y": 1}])]))
    new_objects = _parse(_blob([_poly("p", [{"x": 1, "y": 1}, {"x": 2, "y": 2}])]))
    before_old = json.dumps(old_objects)
    before_new = json.dumps(new_objects)

    is_pure_append_parsed(old_objects, new_objects)
    total_point_count_parsed(old_objects)
    total_point_count_parsed(new_objects)

    assert json.dumps(old_objects) == before_old
    assert json.dumps(new_objects) == before_new

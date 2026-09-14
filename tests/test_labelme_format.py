"""LabelMe format parse/build — the pure module, no server.

The fixtures are the real files in .devnotes/task-imports-new/sample/: genuine
LabelMe 5.5.0 output with entirely Japanese class names, which is what exposed
the bug (they parsed to {} and surfaced as "no recognizable annotations").

The negative `looks_like` cases are the load-bearing ones. The importer's
per-document dispatch is a chain of duck-typed predicates, so a predicate that
is too loose does not fail visibly — it silently steals another format's files
and imports them wrong. See .devnotes/task-imports-new/fix/01_ANALYSIS.md § 1.2.
"""
import json
import os

import pytest

from formats import labelme
from formats.common import clean_label_name, normalize_label_name

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                       ".devnotes", "task-imports-new", "sample")

# The samples live under .devnotes/, which is gitignored — present in a working
# checkout, absent in a clean clone or CI. Skip rather than fail: the synthetic
# cases below cover the logic, and these add real-world fidelity when available.
_HAVE_SAMPLES = os.path.isdir(SAMPLES)
requires_samples = pytest.mark.skipif(not _HAVE_SAMPLES,
                                      reason="sample LabelMe files not present")


def _sample(name):
    with open(os.path.join(SAMPLES, f"{name}.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _doc(shapes, image_path="i.jpg", **extra):
    doc = {"version": "5.5.0", "flags": {}, "shapes": shapes,
           "imagePath": image_path, "imageData": None,
           "imageHeight": 100, "imageWidth": 200}
    doc.update(extra)
    return doc


def _shape(shape_type, points, label="L"):
    return {"label": label, "points": points, "group_id": None,
            "description": "", "shape_type": shape_type, "flags": {}, "mask": None}


# --- detection --------------------------------------------------------------

def test_detects_a_labelme_document():
    assert labelme.looks_like(_doc([])) is True


@pytest.mark.parametrize("data", [
    {"images": [], "categories": [], "annotations": []},   # COCO
    [{"name": "a.jpg", "annotations": []}],                # task JSON, array
    {"name": "a.jpg", "annotations": []},                  # task JSON, per-task
    {"tasks": []},                                         # task JSON, wrapped
    [{"title": "Rust", "value": "Rust"}],                  # class set
    {}, None, "text", 7, [],
    {"shapes": []},                                        # shapes but no image key
])
def test_does_not_claim_another_formats_document(data):
    """The regression guard: this predicate runs before the task-JSON
    fall-through, so anything it wrongly claims is a file imported wrong."""
    assert labelme.looks_like(data) is False


def test_detection_does_not_require_a_version():
    """Forks (AnyLabeling and friends) emit this schema with another or no
    version string; gating on it would reject files we read perfectly."""
    doc = _doc([])
    del doc["version"]
    assert labelme.looks_like(doc) is True


def test_detection_accepts_either_image_key():
    assert labelme.looks_like({"shapes": [], "imagePath": "a.jpg"}) is True
    assert labelme.looks_like({"shapes": [], "imageHeight": 10}) is True


# --- the real samples -------------------------------------------------------

@requires_samples
@pytest.mark.parametrize("name,count", [("P1000676", 4), ("P1000677", 19)])
def test_real_sample_parses(name, count):
    parsed, notes = labelme.parse(_sample(name))
    assert list(parsed) == [f"{name}.JPG"]
    assert len(parsed[f"{name}.JPG"]) == count
    assert notes == []
    assert {a["type"] for a in parsed[f"{name}.JPG"]} == {"polygon"}


@requires_samples
def test_japanese_labels_survive_parse_and_normalisation():
    """A silent mangling here would corrupt every class in the dataset, and
    `normalize_label_name` is what the importer matches labels on."""
    parsed, _ = labelme.parse(_sample("P1000677"))
    names = {a["labelName"] for a in parsed["P1000677.JPG"]}
    assert names == {"健全箇所", "発錆箇所", "AC塗料の露出箇所"}
    for name in names:
        assert clean_label_name(name) == name
        assert normalize_label_name(name) == normalize_label_name(name)
        assert normalize_label_name(name)  # non-empty: not stripped to nothing


@requires_samples
def test_sample_coordinates_are_absolute_pixels():
    """No `normalized` flag — the apply step must not try to scale these, and
    a task with unknown dimensions must still import."""
    parsed, _ = labelme.parse(_sample("P1000676"))
    anns = parsed["P1000676.JPG"]
    assert not any(a.get("normalized") for a in anns)
    assert max(a["x"] + a["width"] for a in anns) > 1.0


# --- shape_type mapping -----------------------------------------------------

def test_polygon_keeps_its_vertices():
    parsed, _ = labelme.parse(_doc([_shape("polygon", [[0, 0], [4, 0], [4, 4]])]))
    ann = parsed["i.jpg"][0]
    assert ann["type"] == "polygon"
    assert ann["points"] == [{"x": 0, "y": 0}, {"x": 4, "y": 0}, {"x": 4, "y": 4}]


def test_rectangle_expands_two_corners_to_four():
    parsed, _ = labelme.parse(_doc([_shape("rectangle", [[10, 10], [50, 40]])]))
    ann = parsed["i.jpg"][0]
    assert ann["type"] == "bbox"
    assert len(ann["points"]) == 4
    assert (ann["x"], ann["y"], ann["width"], ann["height"]) == (10, 10, 40, 30)


def test_rectangle_given_bottom_right_first_is_normalised():
    parsed, _ = labelme.parse(_doc([_shape("rectangle", [[50, 40], [10, 10]])]))
    ann = parsed["i.jpg"][0]
    assert (ann["x"], ann["y"], ann["width"], ann["height"]) == (10, 10, 40, 30)


def test_circle_uses_its_radius_not_its_two_points():
    """A circle is centre + a point on the circumference. Treating those as
    opposite corners gives a degenerate box — zero height for a horizontal
    radius — which is how this was first written."""
    parsed, _ = labelme.parse(_doc([_shape("circle", [[50, 50], [60, 50]])]))
    ann = parsed["i.jpg"][0]
    assert ann["type"] == "bbox"
    assert (ann["x"], ann["y"], ann["width"], ann["height"]) == (40, 40, 20, 20)


@pytest.mark.parametrize("shape_type", ["line", "linestrip"])
def test_open_paths_become_polygons(shape_type):
    parsed, _ = labelme.parse(_doc([_shape(shape_type, [[0, 0], [5, 5], [9, 1]])]))
    assert parsed["i.jpg"][0]["type"] == "polygon"


@pytest.mark.parametrize("shape_type", ["point", "mask"])
def test_unsupported_shapes_are_skipped_and_counted(shape_type):
    parsed, notes = labelme.parse(_doc([_shape(shape_type, [[5, 5], [6, 6]])]))
    assert parsed == {}
    assert len(notes) == 1
    assert shape_type in notes[0]


def test_unknown_shape_type_falls_back_to_polygon():
    parsed, _ = labelme.parse(_doc([_shape("hexagram", [[0, 0], [4, 0], [4, 4]])]))
    assert parsed["i.jpg"][0]["type"] == "polygon"


def test_missing_shape_type_falls_back_to_polygon():
    parsed, _ = labelme.parse(_doc([{"label": "L", "points": [[0, 0], [4, 0], [4, 4]]}]))
    assert parsed["i.jpg"][0]["type"] == "polygon"


# --- malformed input --------------------------------------------------------

@pytest.mark.parametrize("points", [
    [[0, 0]],                      # too few
    [[0, 0], ["a", 2], [3, 3]],    # non-numeric
    [[0, 0], [1], [3, 3]],         # not a pair
    "nonsense",
    None,
])
def test_malformed_shapes_are_skipped_not_raised(points):
    parsed, notes = labelme.parse(_doc([_shape("polygon", points)]))
    assert parsed == {}
    assert any("malformed" in n for n in notes)


def test_one_bad_shape_does_not_cost_the_good_ones():
    parsed, notes = labelme.parse(_doc([
        _shape("polygon", [[0, 0], [4, 0], [4, 4]]),
        _shape("polygon", [[0, 0]]),
        _shape("polygon", [[1, 1], [5, 1], [5, 5]]),
    ]))
    assert len(parsed["i.jpg"]) == 2
    assert any("malformed" in n for n in notes)


def test_empty_shapes_is_recognised_but_contributes_nothing():
    """A legitimate LabelMe file for an unannotated image — recognisable, but
    with nothing to import."""
    assert labelme.parse(_doc([])) == ({}, [])


def test_blank_label_falls_back_to_object():
    parsed, _ = labelme.parse(_doc([_shape("polygon", [[0, 0], [4, 0], [4, 4]], label="  ")]))
    assert parsed["i.jpg"][0]["labelName"] == "object"


# --- the image key ----------------------------------------------------------

def test_image_path_is_basenamed():
    parsed, _ = labelme.parse(_doc([_shape("polygon", [[0, 0], [4, 0], [4, 4]])],
                                   image_path="../images/sub/P1.JPG"))
    assert list(parsed) == ["P1.JPG"]


def test_windows_image_path_is_basenamed():
    parsed, _ = labelme.parse(_doc([_shape("polygon", [[0, 0], [4, 0], [4, 4]])],
                                   image_path=r"C:\data\imgs\P1.JPG"))
    assert list(parsed) == ["P1.JPG"]


def test_falls_back_to_the_source_filename():
    """Inside a ZIP walk the entry name identifies the image well enough — the
    importer's stem matching resolves P1.json to a P1.JPG task."""
    doc = _doc([_shape("polygon", [[0, 0], [4, 0], [4, 4]])], image_path="")
    parsed, _ = labelme.parse(doc, source_name="labels/P1.json")
    assert list(parsed) == ["P1.json"]


def test_no_image_path_and_no_source_name_yields_nothing():
    doc = _doc([_shape("polygon", [[0, 0], [4, 0], [4, 4]])], image_path="")
    assert labelme.parse(doc) == ({}, [])


# --- build ------------------------------------------------------------------

class _Label:
    def __init__(self, lid, name, color="#ef4444"):
        self.id, self.name, self.color = lid, name, color


class _Task:
    def __init__(self, tid, description, annotations, width=0, height=0):
        self.id, self.description = tid, description
        self.annotations = json.dumps(annotations)
        self.image_width, self.image_height = width, height
        self.annotation_rows, self.image_path = [], None


_POLY = [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 6, "y": 9}]
_BOX = [{"x": 1, "y": 2}, {"x": 11, "y": 2}, {"x": 11, "y": 8}, {"x": 1, "y": 8}]


def _built(tasks, labels):
    entries, skipped = labelme.build(tasks, labels)
    return {name: json.loads(body.decode("utf-8")) for name, body in entries}, skipped


def test_build_writes_one_document_per_task():
    labels = [_Label("l1", "Rust Area")]
    tasks = [_Task(1, "P1.JPG", [{"id": "a", "labelId": "l1", "type": "polygon", "points": _POLY}]),
             _Task(2, "P2.JPG", [])]
    docs, skipped = _built(tasks, labels)
    assert sorted(docs) == ["P1.json", "P2.json"]
    assert skipped == []
    assert docs["P1.json"]["imagePath"] == "P1.JPG"
    assert docs["P1.json"]["imageData"] is None


def test_build_emits_a_document_its_own_parser_accepts():
    labels = [_Label("l1", "Rust Area")]
    tasks = [_Task(1, "P1.JPG", [{"id": "a", "labelId": "l1", "type": "polygon", "points": _POLY}])]
    docs, _ = _built(tasks, labels)
    assert labelme.looks_like(docs["P1.json"]) is True


def test_build_maps_a_box_to_a_two_corner_rectangle():
    labels = [_Label("l1", "Rust Area")]
    tasks = [_Task(1, "P1.JPG", [{"id": "a", "labelId": "l1", "type": "bbox", "points": _BOX}])]
    docs, _ = _built(tasks, labels)
    shape = docs["P1.json"]["shapes"][0]
    assert shape["shape_type"] == "rectangle"
    assert shape["points"] == [[1, 2], [11, 8]]


def test_build_skips_annotations_whose_label_was_deleted():
    labels = [_Label("l1", "Rust Area")]
    tasks = [_Task(1, "P1.JPG", [
        {"id": "a", "labelId": "l1", "type": "polygon", "points": _POLY},
        {"id": "b", "labelId": "gone", "type": "polygon", "points": _POLY},
    ])]
    docs, _ = _built(tasks, labels)
    assert len(docs["P1.json"]["shapes"]) == 1


def test_build_tolerates_unknown_image_dimensions():
    """The point of difference from YOLO: absolute coordinates mean a task with
    no known size still exports correctly, so nothing is skipped."""
    labels = [_Label("l1", "Rust Area")]
    tasks = [_Task(1, "P1.JPG", [{"id": "a", "labelId": "l1", "type": "polygon", "points": _POLY}])]
    docs, skipped = _built(tasks, labels)
    assert skipped == []
    assert docs["P1.json"]["imageHeight"] is None
    assert docs["P1.json"]["imageWidth"] is None


def test_build_writes_literal_utf8_not_escapes():
    """Real label sets are not ASCII. \\uXXXX escapes are valid JSON that no
    human can read and that diverge from what LabelMe itself writes."""
    labels = [_Label("l1", "健全箇所")]
    tasks = [_Task(1, "P1.JPG", [{"id": "a", "labelId": "l1", "type": "polygon", "points": _POLY}])]
    entries, _ = labelme.build(tasks, labels)
    raw = entries[0][1]
    assert "健全箇所".encode("utf-8") in raw
    assert b"\\u" not in raw


def test_build_uses_the_display_name_so_reimport_matches():
    labels = [_Label("l1", "Rust Area")]
    tasks = [_Task(1, "P1.JPG", [{"id": "a", "labelId": "l1", "type": "polygon", "points": _POLY}])]
    docs, _ = _built(tasks, labels)
    assert docs["P1.json"]["shapes"][0]["label"] == "Rust Area"


def test_round_trip_preserves_geometry_and_labels():
    labels = [_Label("l1", "健全箇所"), _Label("l2", "Rust Area")]
    tasks = [_Task(1, "P1.JPG", [
        {"id": "a", "labelId": "l1", "type": "polygon", "points": _POLY},
        {"id": "b", "labelId": "l2", "type": "bbox", "points": _BOX},
    ])]
    docs, _ = _built(tasks, labels)
    parsed, notes = labelme.parse(docs["P1.json"])
    assert notes == []
    anns = parsed["P1.JPG"]
    assert [(a["labelName"], a["type"]) for a in anns] == \
           [("健全箇所", "polygon"), ("Rust Area", "bbox")]
    assert anns[0]["points"] == _POLY
    assert (anns[1]["x"], anns[1]["y"], anns[1]["width"], anns[1]["height"]) == (1, 2, 10, 6)

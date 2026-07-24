"""Interop task JSON export (data-refactor plan Phase 3.1-3.2).

Two containers over one object shape:
  annotations_json     a JSON array of task objects, one file
  annotations_pertask  a ZIP of one object per file

The array element and the per-task root are byte-identical in the reference
export, so both are built from the same task_object() — the tests below pin
that equivalence so the two cannot drift.
"""
import io
import json
import os
import zipfile

import pytest

from conftest import unique_label_id
from formats import annotations_json

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "interop")


def _new_project(client, auth, name="aj"):
    res = client.post("/api/projects", json={"name": name, "slug": name, "creator": "x"}, headers=auth)
    return res.json()["id"]


def _new_label(client, auth, pid, lid, name, color="#ef4444"):
    """Create a label under a globally unique id (see conftest.unique_label_id)."""
    unique = unique_label_id(lid)
    client.post("/api/labels", json={"id": unique, "name": name, "color": color, "projectId": pid}, headers=auth)
    return unique


def _new_task(client, auth, pid, description, annotations=None, status="New"):
    payload = {"description": description, "status": status}
    if annotations is not None:
        payload["annotations"] = json.dumps(annotations)
    res = client.post(f"/api/tasks?projectId={pid}", json=payload, headers=auth)
    assert res.status_code in (200, 201), res.text
    return res.json().get("id")


def _export(client, auth, pid, fmt):
    res = client.post("/api/exports", json={"projectId": pid, "format": fmt}, headers=auth)
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    status = client.get(f"/api/exports/{job_id}", headers=auth).json()
    assert status["status"] == "completed", status
    return client.get(f"/api/exports/{job_id}/download", headers=auth)


def _pertask_entries(client, auth, pid):
    res = _export(client, auth, pid, "annotations_pertask")
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    return {n: json.loads(zf.read(n)) for n in zf.namelist()}


# ---------------------------------------------------------------------------
# Single-file array
# ---------------------------------------------------------------------------

def test_annotations_json_is_a_json_array(client, alice):
    """The reference single-file export is an array, not a wrapper object."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "a.png")
    _new_task(client, alice, pid, "b.png")

    body = _export(client, alice, pid, "annotations_json").json()
    assert isinstance(body, list)
    assert {t["name"] for t in body} == {"a.png", "b.png"}


def test_array_element_matches_reference_key_set(client, alice):
    """Compared against the real per-task reference file."""
    with open(os.path.join(FIXTURES, "pertask_P1000066.json"), encoding="utf-8") as fh:
        reference = json.load(fh)

    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "x.png")

    body = _export(client, alice, pid, "annotations_json").json()
    assert set(body[0]) == set(reference)


def test_array_element_and_pertask_entry_are_identical(client, alice):
    """One serializer, two containers — they must not drift."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "lbl-1", "Rust Area", "#D95319")
    _new_task(client, alice, pid, "same.png", annotations=[
        {"id": "ann1", "labelId": lid, "type": "polygon",
         "points": [{"x": 1, "y": 2}, {"x": 3, "y": 4}, {"x": 5, "y": 6}]},
    ])

    from_array = _export(client, alice, pid, "annotations_json").json()[0]
    from_zip = _pertask_entries(client, alice, pid)["jsons/same.json"]
    assert from_array == from_zip


def test_empty_project_exports_an_empty_array(client, alice):
    pid = _new_project(client, alice)
    assert _export(client, alice, pid, "annotations_json").json() == []


# ---------------------------------------------------------------------------
# Fields completed in this phase
# ---------------------------------------------------------------------------

def test_created_at_is_emitted(client, alice):
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "c.png")
    task = _export(client, alice, pid, "annotations_json").json()[0]
    assert task["createdAt"], "created_at is a real column and should be emitted"


def test_url_is_empty_rather_than_a_dead_link(client, alice):
    """The reference carries a presigned URL to a hosted image. We have no
    equivalent, and a relative path would not resolve for any other consumer."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "u.png")
    assert _export(client, alice, pid, "annotations_json").json()[0]["url"] == ""


@pytest.mark.parametrize("ours,expected_status,expected_external", [
    ("New", "registered", ""),
    ("In Progress", "in_progress", ""),
    ("Completed", "completed", ""),
    ("Approved", "completed", "approved"),
])
def test_status_is_mapped_to_the_interop_vocabulary(client, alice, ours,
                                                    expected_status, expected_external):
    """Approval rides in a separate externalStatus field there, not in status."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "s.png", status=ours)

    task = _export(client, alice, pid, "annotations_json").json()[0]
    assert task["status"] == expected_status
    assert task["externalStatus"] == expected_external


# ---------------------------------------------------------------------------
# Annotation shape (gap G1)
# ---------------------------------------------------------------------------

def test_annotation_type_reflects_the_shape_drawn(client, alice):
    """Previously hard-coded to "polygon", which turned every box into one."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "lbl-t", "Thing")
    _new_task(client, alice, pid, "t.png", annotations=[
        {"id": "a1", "labelId": lid, "type": "bbox",
         "points": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}, {"x": 0, "y": 10}]},
        {"id": "a2", "labelId": lid, "type": "polygon",
         "points": [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 5, "y": 10}]},
    ])

    task = _export(client, alice, pid, "annotations_json").json()[0]
    assert [a["type"] for a in task["annotations"]] == ["bbox", "polygon"]


def test_annotation_order_is_one_based_and_sequential(client, alice):
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "lbl-o", "Thing")
    _new_task(client, alice, pid, "o.png", annotations=[
        {"id": f"a{i}", "labelId": lid, "points": [{"x": i, "y": i}, {"x": i + 5, "y": i + 5}]}
        for i in range(3)
    ])

    task = _export(client, alice, pid, "annotations_json").json()[0]
    assert [a["order"] for a in task["annotations"]] == [1, 2, 3]


def test_points_are_flattened(client, alice):
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "lbl-p", "Thing")
    _new_task(client, alice, pid, "p.png", annotations=[
        {"id": "a1", "labelId": lid, "points": [{"x": 10.5, "y": 20.3}, {"x": 30.7, "y": 40.1}]},
    ])

    ann = _export(client, alice, pid, "annotations_json").json()[0]["annotations"][0]
    assert ann["points"] == [10.5, 20.3, 30.7, 40.1]


def test_value_is_shared_across_the_whole_export(client, alice):
    """Colliding class names are disambiguated once for the project, so every
    task in the export agrees on the identifiers."""
    pid = _new_project(client, alice)
    lid_a = _new_label(client, alice, pid, "lbl-a", "A/B")
    lid_b = _new_label(client, alice, pid, "lbl-b", "AB")
    _new_task(client, alice, pid, "v1.png", annotations=[
        {"id": "a1", "labelId": lid_a, "points": [{"x": 0, "y": 0}, {"x": 5, "y": 5}]}])
    _new_task(client, alice, pid, "v2.png", annotations=[
        {"id": "a2", "labelId": lid_b, "points": [{"x": 0, "y": 0}, {"x": 5, "y": 5}]}])

    body = _export(client, alice, pid, "annotations_json").json()
    values = {t["name"]: t["annotations"][0]["value"] for t in body}
    assert len(set(values.values())) == 2, values


def test_annotation_for_deleted_label_is_skipped(client, alice):
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "d.png", annotations=[
        {"id": "a1", "labelId": "does-not-exist", "points": [{"x": 0, "y": 0}, {"x": 5, "y": 5}]},
    ])
    assert _export(client, alice, pid, "annotations_json").json()[0]["annotations"] == []


def test_comments_are_not_exported_as_annotations(client, alice):
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "lbl-c", "Thing")
    _new_task(client, alice, pid, "cm.png", annotations=[
        {"id": "c1", "type": "comment", "text": "check this"},
        {"id": "a1", "labelId": lid, "points": [{"x": 0, "y": 0}, {"x": 5, "y": 5}]},
    ])
    task = _export(client, alice, pid, "annotations_json").json()[0]
    assert len(task["annotations"]) == 1


# ---------------------------------------------------------------------------
# Format codes and deprecated aliases
# ---------------------------------------------------------------------------

def test_json_alias_still_yields_coco(client, alice):
    """Bookmarked UI state and existing clients keep working."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "alias.png")

    body = _export(client, alice, pid, "json").json()
    assert isinstance(body, dict)
    assert {"images", "categories", "annotations"} <= set(body)


def test_pertask_alias_still_yields_the_zip(client, alice):
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "alias2.png")

    res = _export(client, alice, pid, "pertask")
    assert res.headers["content-type"] == "application/zip"
    assert zipfile.ZipFile(io.BytesIO(res.content)).namelist() == ["jsons/alias2.json"]


def test_status_endpoint_reports_the_canonical_format(client, alice):
    """A deprecated code in, the canonical code out — the UI labels off this."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "canon.png")

    res = client.post("/api/exports", json={"projectId": pid, "format": "json"}, headers=alice)
    job_id = res.json()["job_id"]
    assert client.get(f"/api/exports/{job_id}", headers=alice).json()["format"] == "coco"


def test_unknown_format_is_rejected(client, alice):
    pid = _new_project(client, alice)
    res = client.post("/api/exports", json={"projectId": pid, "format": "nonsense"}, headers=alice)
    assert res.status_code == 422


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def test_parse_accepts_an_array_of_tasks():
    data = [{"name": "a.png", "annotations": [
        {"title": "X", "value": "X", "points": [0, 0, 5, 0, 5, 5]}]}]
    assert list(annotations_json.parse(data)) == ["a.png"]


def test_parse_accepts_a_single_object():
    data = {"name": "a.png", "annotations": [
        {"title": "X", "value": "X", "points": [0, 0, 5, 0, 5, 5]}]}
    assert list(annotations_json.parse(data)) == ["a.png"]


def test_parse_accepts_a_tasks_wrapper():
    data = {"tasks": [{"name": "a.png", "annotations": [
        {"title": "X", "value": "X", "points": [0, 0, 5, 0, 5, 5]}]}]}
    assert list(annotations_json.parse(data)) == ["a.png"]


def test_parse_keeps_shape_type():
    data = {"name": "a.png", "annotations": [
        {"title": "X", "type": "bbox", "points": [0, 0, 10, 0, 10, 10, 0, 10]}]}
    assert annotations_json.parse(data)["a.png"][0]["type"] == "bbox"


def test_parse_reports_value_for_label_matching():
    data = {"name": "a.png", "annotations": [
        {"title": "Rust Area", "value": "RustArea", "points": [0, 0, 5, 0, 5, 5]}]}
    ann = annotations_json.parse(data)["a.png"][0]
    assert ann["labelName"] == "Rust Area"
    assert ann["labelValue"] == "RustArea"


def test_parse_ignores_malformed_entries():
    """A hand-edited file must not take the whole import down."""
    data = [
        "not a dict",
        {"no_name": True, "annotations": []},
        {"name": "ok.png", "annotations": [
            "not a dict",
            {"title": "X", "points": []},          # no points
            {"title": "X", "points": [1, 2]},      # single point
            {"title": "X", "points": [0, 0, 5, 0, 5, 5]},
        ]},
    ]
    parsed = annotations_json.parse(data)
    assert list(parsed) == ["ok.png"]
    assert len(parsed["ok.png"]) == 1


def test_parse_round_trips_an_export(client, alice):
    """Export from one project, import into another, geometry preserved."""
    pid = _new_project(client, alice, "src")
    lid = _new_label(client, alice, pid, "lbl-rt", "Rust Area", "#D95319")
    _new_task(client, alice, pid, "rt.png", annotations=[
        {"id": "a1", "labelId": lid, "type": "polygon",
         "points": [{"x": 1, "y": 2}, {"x": 30, "y": 4}, {"x": 5, "y": 60}]},
    ])
    exported = _export(client, alice, pid, "annotations_json").json()

    target = _new_project(client, alice, "dst")
    _new_task(client, alice, target, "rt.png")
    res = client.post(
        f"/api/imports/annotations?projectId={target}&mode=replace",
        files=[("file", ("a.json", json.dumps(exported).encode(), "application/json"))],
        headers=alice,
    )
    assert res.status_code == 200, res.text

    tasks = client.get(f"/api/tasks?projectId={target}", headers=alice).json()
    anns = next(t for t in tasks if t["description"] == "rt.png")["annotations"]
    assert len(anns) == 1
    assert anns[0]["type"] == "polygon"
    assert anns[0]["points"] == [{"x": 1, "y": 2}, {"x": 30, "y": 4}, {"x": 5, "y": 60}]

    labels = client.get(f"/api/labels?projectId={target}", headers=alice).json()
    assert labels[0]["name"] == "Rust Area"
    assert labels[0]["color"] == "#D95319"

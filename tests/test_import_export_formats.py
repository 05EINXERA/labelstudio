"""Import/Export format enhancements (IMPORT_EXPORT_FIX_PLAN.md)."""
import json
import io
import uuid


def _new_project(client, auth, name="fmt"):
    return client.post("/api/projects", json={"name": name, "slug": name, "creator": "ignored"}, headers=auth).json()["id"]


def _new_label(client, auth, pid, name, color="#111"):
    lid = uuid.uuid4().hex
    client.post("/api/labels", json={"id": lid, "name": name, "color": color, "projectId": pid}, headers=auth)
    return lid


def _new_task(client, auth, pid, description, annotations=None):
    tid = client.post("/api/tasks", json={"description": description}, params={"projectId": pid}, headers=auth).json()["id"]
    if annotations is not None:
        client.patch(f"/api/tasks/{tid}", json={"annotations": json.dumps(annotations)}, headers=auth)
    return tid


# ============================================================================
# T1: COCO Export Enhancements
# ============================================================================

def test_coco_categories_include_color_and_keypoints(client, alice):
    """T1.1-1.3: COCO categories now include color, skeleton, keypoints, and use label name for supercategory."""
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "Cat", "#ef4444")
    _new_label(client, alice, pid, "Dog", "#3b82f6")
    _new_task(client, alice, pid, "test.jpg", annotations=[
        {"id": "ann1", "labelId": "lbl-Cat", "points": [{"x": 10, "y": 10}, {"x": 20, "y": 20}]}
    ])

    res = client.post("/api/exports", json={"projectId": pid, "format": "json"}, headers=alice)
    job_id = res.json()["job_id"]
    client.get(f"/api/exports/{job_id}", headers=alice)  # wait for completion
    
    export = client.get(f"/api/exports/{job_id}/download", headers=alice).json()
    
    categories = export["categories"]
    assert len(categories) == 2
    
    cat_category = next(c for c in categories if c["name"] == "Cat")
    assert cat_category["color"] == "#ef4444"  # T1.1
    assert cat_category["skeleton"] == []  # T1.2
    assert cat_category["keypoints"] == []
    assert cat_category["keypoint_colors"] == []
    assert cat_category["supercategory"] == "Cat"  # T1.3 (not "none")


# ============================================================================
# T2: Classes Import/Export (FastLabel Format)
# ============================================================================

def test_export_classes_fastlabel_format(client, alice):
    """T2.1-2.2: Export classes in FastLabel format with all config fields."""
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "Rust Area", "#D95319")
    _new_label(client, alice, pid, "AF Paint", "#FF13A6")

    res = client.get(f"/api/labels/export?projectId={pid}&format=fastlabel", headers=alice)
    assert res.status_code == 200
    
    # FastLabel format returns PlainTextResponse with JSON content
    classes = json.loads(res.text)
    assert len(classes) == 2
    
    # Find by title (order may vary)
    af_paint = next((c for c in classes if c["title"] == "AF Paint"), None)
    assert af_paint is not None
    assert af_paint["type"] == "polygon"
    assert af_paint["value"] == "AFPaint"  # stripped spaces
    assert af_paint["color"] == "#FF13A6"
    assert af_paint["order"] in [1, 2]  # order is based on list position
    assert af_paint["useBBox"] is False
    assert af_paint["useRotation"] is False
    assert af_paint["attributes"] == []
    assert af_paint["keypoints"] == []
    # All config fields should be present with defaults
    assert "minWidth" in af_paint
    assert "maxHeight" in af_paint
    assert "verticalRatio" in af_paint


def test_import_fastlabel_classes(client, alice):
    """T2.3-2.4: Import FastLabel class format, extract title→name and color."""
    pid = _new_project(client, alice)
    
    fastlabel_data = json.dumps([
        {
            "type": "polygon",
            "title": "Exposed Steel Plate Area",
            "value": "ExposedSteelPlateArea",
            "color": "#1E5AFF",
            "order": 1,
            "useBBox": False,
            "minWidth": 0,
            "maxHeight": 0,
            "attributes": [],
            "keypoints": []
        },
        {
            "type": "polygon",
            "title": "Dirt 1 (Heavy Stains)",
            "value": "Dirt1",
            "color": "#01d158",
            "order": 2,
            "useBBox": False,
            "attributes": [],
            "keypoints": []
        }
    ]).encode()
    
    res = client.post(
        f"/api/labels/import?projectId={pid}",
        files={"file": ("classes.json", fastlabel_data, "application/json")},
        headers=alice
    )
    assert res.status_code == 200
    body = res.json()
    assert body["created"] == 2
    
    labels = body["labels"]
    assert len(labels) == 2
    assert labels[0]["name"] == "Dirt 1 (Heavy Stains)"  # title used, not value
    assert labels[0]["color"] == "#01d158"
    assert labels[1]["name"] == "Exposed Steel Plate Area"
    assert labels[1]["color"] == "#1E5AFF"


def test_fastlabel_classes_round_trip(client, alice):
    """T2.7: Import FastLabel classes, export as FastLabel, verify structure preserved."""
    pid = _new_project(client, alice)
    
    original = [
        {"type": "polygon", "title": "Cat", "value": "Cat", "color": "#111", "order": 1,
         "useBBox": False, "attributes": [], "keypoints": []}
    ]
    
    client.post(
        f"/api/labels/import?projectId={pid}",
        files={"file": ("in.json", json.dumps(original).encode(), "application/json")},
        headers=alice
    )
    
    export_res = client.get(f"/api/labels/export?projectId={pid}&format=fastlabel", headers=alice)
    exported = json.loads(export_res.text)
    
    assert len(exported) == 1
    assert exported[0]["title"] == "Cat"
    assert exported[0]["type"] == "polygon"
    assert exported[0]["order"] == 1
    assert "useBBox" in exported[0]
    assert "attributes" in exported[0]


# ============================================================================
# T3: Per-Task Annotation Export
# ============================================================================

def test_export_pertask_format(client, alice):
    """T3.1-3.2: Export annotations in per-task FastLabel format."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "Rust Area", "#D95319")
    _new_task(client, alice, pid, "P1000066.JPG", annotations=[
        {
            "id": "ann1",
            "labelId": lid,
            "points": [{"x": 10.5, "y": 20.3}, {"x": 30.7, "y": 40.1}],
        }
    ])

    res = client.post("/api/exports", json={"projectId": pid, "format": "pertask"}, headers=alice)
    job_id = res.json()["job_id"]
    client.get(f"/api/exports/{job_id}", headers=alice)
    
    export = client.get(f"/api/exports/{job_id}/download", headers=alice).json()
    
    assert len(export) == 1
    task = export[0]
    assert task["name"] == "P1000066.JPG"
    assert task["status"] == "New"
    assert "secondsToAnnotate" in task
    
    anns = task["annotations"]
    assert len(anns) == 1
    ann = anns[0]
    assert ann["type"] == "polygon"
    assert ann["title"] == "Rust Area"  # T3.6
    assert ann["value"] == "RustArea"   # T3.6 (stripped spaces)
    assert ann["color"] == "#D95319"
    assert ann["order"] == 1
    assert ann["rotation"] == 0
    assert ann["keypoints"] == []
    assert ann["confidenceScore"] == -1
    assert ann["attributes"] == []


def test_export_pertask_flat_points_array(client, alice):
    """T3.5: Per-task export flattens points to [x1,y1,x2,y2,...]."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "Box", "#111")
    _new_task(client, alice, pid, "test.jpg", annotations=[
        {"id": "ann1", "labelId": lid, "points": [{"x": 10, "y": 20}, {"x": 30, "y": 40}, {"x": 50, "y": 60}]}
    ])

    res = client.post("/api/exports", json={"projectId": pid, "format": "pertask"}, headers=alice)
    job_id = res.json()["job_id"]
    client.get(f"/api/exports/{job_id}", headers=alice)
    export = client.get(f"/api/exports/{job_id}/download", headers=alice).json()
    
    points = export[0]["annotations"][0]["points"]
    assert points == [10, 20, 30, 40, 50, 60]  # Flat array


def test_export_pertask_multiple_annotations_ordered(client, alice):
    """T3.6: Multiple annotations get sequential order numbers."""
    pid = _new_project(client, alice)
    lid1 = _new_label(client, alice, pid, "Cat", "#111")
    lid2 = _new_label(client, alice, pid, "Dog", "#222")
    _new_task(client, alice, pid, "test.jpg", annotations=[
        {"id": "ann1", "labelId": lid1, "points": [{"x": 1, "y": 1}, {"x": 2, "y": 2}]},
        {"id": "ann2", "labelId": lid2, "points": [{"x": 3, "y": 3}, {"x": 4, "y": 4}]},
        {"id": "ann3", "labelId": lid1, "points": [{"x": 5, "y": 5}, {"x": 6, "y": 6}]},
    ])

    res = client.post("/api/exports", json={"projectId": pid, "format": "pertask"}, headers=alice)
    job_id = res.json()["job_id"]
    client.get(f"/api/exports/{job_id}", headers=alice)
    export = client.get(f"/api/exports/{job_id}/download", headers=alice).json()
    
    anns = export[0]["annotations"]
    assert len(anns) == 3
    assert anns[0]["order"] == 1
    assert anns[1]["order"] == 2
    assert anns[2]["order"] == 3


# ============================================================================
# T4: Annotation Import Enhancement
# ============================================================================

def test_import_pertask_with_title_and_value(client, alice):
    """T4.2: Import per-task format with title and value fields."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "test.jpg")
    
    # Import will create the label automatically
    pertask_data = json.dumps([
        {
            "name": "test.jpg",
            "annotations": [
                {
                    "id": "ann1",
                    "type": "polygon",
                    "title": "AC Paint / Exposed Steel",
                    "value": "ACPaintExposedSteel",
                    "color": "#0FFFFF",
                    "points": [100, 200, 150, 250, 200, 200]
                }
            ]
        }
    ]).encode()
    
    res = client.post(
        f"/api/imports/annotations?projectId={pid}",
        files={"file": ("export.json", pertask_data, "application/json")},
        headers=alice
    )
    assert res.status_code == 200
    body = res.json()
    assert body["tasks_updated"] == 1
    assert body["annotations_imported"] == 1
    
    # Verify label was created with the title
    labels = client.get(f"/api/labels?projectId={pid}", headers=alice).json()
    assert any(l["name"] == "ACPaintExposedSteel" for l in labels)  # matched by value


def test_import_annotation_matches_by_value_then_title(client, alice):
    """T4.3: Label matching prioritizes 'value' over 'title'."""
    pid = _new_project(client, alice)
    # Create label with a specific name
    _new_label(client, alice, pid, "ExactMatch", "#111")
    _new_task(client, alice, pid, "test.jpg")
    
    # Import annotation where value matches existing label name
    pertask_data = json.dumps([
        {
            "name": "test.jpg",
            "annotations": [
                {
                    "title": "Different Title",
                    "value": "ExactMatch",  # This should match
                    "points": [10, 10, 20, 20]
                }
            ]
        }
    ]).encode()
    
    res = client.post(
        f"/api/imports/annotations?projectId={pid}",
        files={"file": ("export.json", pertask_data, "application/json")},
        headers=alice
    )
    assert res.status_code == 200
    body = res.json()
    assert body["tasks_updated"] == 1
    
    # Should have matched existing label, not created a new one
    labels = client.get(f"/api/labels?projectId={pid}", headers=alice).json()
    assert len(labels) == 1
    assert labels[0]["name"] == "ExactMatch"

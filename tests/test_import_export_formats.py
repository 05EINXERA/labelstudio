"""Import/Export format enhancements (IMPORT_EXPORT_FIX_PLAN.md,
.devnotes/imports-exports/PER_TASK_ZIP_EXPORT_PLAN.md)."""
import json
import io
import uuid
import zipfile

from PIL import Image


def _png_bytes(width, height):
    """A real PNG of the given size, for exercising image-dimension reads."""
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "red").save(buf, format="PNG")
    return buf.getvalue()


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

def _download_pertask_zip(client, auth, pid):
    """Run a per-task export to completion and return the raw download response."""
    res = client.post("/api/exports", json={"projectId": pid, "format": "pertask"}, headers=auth)
    job_id = res.json()["job_id"]
    client.get(f"/api/exports/{job_id}", headers=auth)  # wait for completion
    return client.get(f"/api/exports/{job_id}/download", headers=auth)


def _pertask_entries(client, auth, pid):
    """Per-task export as {arcname: parsed json}."""
    res = _download_pertask_zip(client, auth, pid)
    assert res.status_code == 200, res.text
    zf = zipfile.ZipFile(io.BytesIO(res.content))
    return {name: json.loads(zf.read(name)) for name in zf.namelist()}


def test_pertask_export_returns_zip(client, alice):
    """Per-task export downloads as a real ZIP, not a JSON blob."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "test.jpg")

    res = _download_pertask_zip(client, alice, pid)
    assert res.headers["content-type"] == "application/zip"
    assert res.content[:2] == b"PK"  # ZIP magic
    assert "export-pertask-" in res.headers["content-disposition"]
    assert ".zip" in res.headers["content-disposition"]


def test_pertask_zip_has_json_folder_per_task(client, alice):
    """One .json file per task, inside a jsons/ folder, named after the image."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "P1000066.JPG")
    _new_task(client, alice, pid, "P1000067.JPG")

    entries = _pertask_entries(client, alice, pid)
    assert set(entries) == {"jsons/P1000066.json", "jsons/P1000067.json"}


def test_pertask_zip_entry_is_single_object(client, alice):
    """Each entry is one task object, not a list wrapping every task."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "a.jpg")
    _new_task(client, alice, pid, "b.jpg")

    entries = _pertask_entries(client, alice, pid)
    assert len(entries) == 2
    for body in entries.values():
        assert isinstance(body, dict)


def test_pertask_zip_entry_matches_reference_shape(client, alice):
    """T3.1-3.2: entry matches the FastLabel per-task reference structure."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "Rust Area", "#D95319")
    _new_task(client, alice, pid, "P1000066.JPG", annotations=[
        {"id": "ann1", "labelId": lid, "points": [{"x": 10.5, "y": 20.3}, {"x": 30.7, "y": 40.1}]}
    ])

    task = _pertask_entries(client, alice, pid)["jsons/P1000066.json"]
    assert set(task) == {
        "id", "name", "status", "externalStatus", "url",
        "width", "height", "secondsToAnnotate",
        "assignee", "reviewer", "approver",
        "externalAssignee", "externalReviewer", "externalApprover",
        "tags", "metadatas", "relations", "createdAt", "updatedAt", "annotations",
    }
    assert task["name"] == "P1000066.JPG"
    # Statuses are emitted in the interop vocabulary, not ours: "New" is
    # "registered" there, and approval rides in the separate externalStatus.
    assert task["status"] == "registered"
    assert task["externalStatus"] == ""
    # We host no public image URL, so this is empty rather than a dead link.
    assert task["url"] == ""

    anns = task["annotations"]
    assert len(anns) == 1
    ann = anns[0]
    assert set(ann) == {
        "id", "type", "title", "value", "color", "order",
        "attributes", "points", "rotation", "keypoints", "confidenceScore",
    }
    assert ann["type"] == "polygon"
    assert ann["title"] == "Rust Area"  # T3.6
    assert ann["value"] == "RustArea"   # T3.6 (stripped spaces)
    assert ann["color"] == "#D95319"
    assert ann["order"] == 1
    assert ann["rotation"] == 0
    assert ann["keypoints"] == []
    assert ann["confidenceScore"] == -1
    assert ann["attributes"] == []
    assert all(isinstance(c, (int, float)) for c in ann["points"])


def test_pertask_entry_carries_assignee_and_updated_at(client, alice):
    """Fields backed by real columns must carry data, not placeholder blanks."""
    pid = _new_project(client, alice)
    tid = _new_task(client, alice, pid, "shot.jpg")
    client.patch(f"/api/tasks/{tid}", json={"assignee": "bijay hamal"}, headers=alice)

    task = _pertask_entries(client, alice, pid)["jsons/shot.json"]
    assert task["assignee"] == "bijay hamal"
    assert task["updatedAt"], "updated_at is a real column and should be emitted"
    # Not workflow state we track — emitted empty for shape compatibility only.
    assert task["reviewer"] == ""
    assert task["tags"] == []


def test_export_pertask_flat_points_array(client, alice):
    """T3.5: Per-task export flattens points to [x1,y1,x2,y2,...]."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "Box", "#111")
    _new_task(client, alice, pid, "test.jpg", annotations=[
        {"id": "ann1", "labelId": lid, "points": [{"x": 10, "y": 20}, {"x": 30, "y": 40}, {"x": 50, "y": 60}]}
    ])

    task = _pertask_entries(client, alice, pid)["jsons/test.json"]
    assert task["annotations"][0]["points"] == [10, 20, 30, 40, 50, 60]  # Flat array


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

    anns = _pertask_entries(client, alice, pid)["jsons/test.json"]["annotations"]
    assert [a["order"] for a in anns] == [1, 2, 3]


def test_pertask_zip_duplicate_names_disambiguated(client, alice):
    """Image names are not unique in a project; neither task may be lost."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "a.jpg")
    _new_task(client, alice, pid, "a.jpg")

    entries = _pertask_entries(client, alice, pid)
    assert len(entries) == 2, "a duplicate image name silently overwrote a task"
    assert "jsons/a.json" in entries
    assert any(n != "jsons/a.json" and n.startswith("jsons/a-") for n in entries)


def test_pertask_zip_respects_status_filter(client, alice):
    """Filtered-out tasks contribute no entry at all."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "kept.jpg")
    tid = _new_task(client, alice, pid, "dropped.jpg")
    client.patch(f"/api/tasks/{tid}", json={"status": "Completed"}, headers=alice)

    res = client.post(
        "/api/exports",
        json={"projectId": pid, "format": "pertask", "statusFilter": ["New"]},
        headers=alice,
    )
    job_id = res.json()["job_id"]
    client.get(f"/api/exports/{job_id}", headers=alice)
    dl = client.get(f"/api/exports/{job_id}/download", headers=alice)
    names = zipfile.ZipFile(io.BytesIO(dl.content)).namelist()
    assert names == ["jsons/kept.json"]


def test_pertask_zip_skips_comment_annotations(client, alice):
    """type=comment is excluded, consistent with the COCO and CSV builders."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "Box", "#111")
    _new_task(client, alice, pid, "test.jpg", annotations=[
        {"id": "c1", "type": "comment", "labelId": lid, "points": [{"x": 1, "y": 1}, {"x": 2, "y": 2}]},
        {"id": "a1", "labelId": lid, "points": [{"x": 3, "y": 3}, {"x": 4, "y": 4}]},
    ])

    anns = _pertask_entries(client, alice, pid)["jsons/test.json"]["annotations"]
    assert len(anns) == 1
    assert anns[0]["id"] == "a1"


def test_pertask_missing_image_falls_back_to_zero_dims(client, alice):
    """An unreadable image degrades to 0x0 rather than failing the export."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "no-such-file.jpg")

    task = _pertask_entries(client, alice, pid)["jsons/no-such-file.json"]
    assert task["width"] == 0
    assert task["height"] == 0


def test_pertask_uploaded_image_reports_real_dimensions(client, alice):
    """width/height come from the actual image, not a hardcoded 0."""
    png = _png_bytes(7, 3)
    res = client.post(
        f"/api/projects/{_new_project(client, alice)}/upload",
        files={"file": ("dims.png", png, "image/png")}, headers=alice,
    )
    assert res.status_code == 200, res.text
    pid = client.get("/api/projects", headers=alice).json()[-1]["id"]

    task = _pertask_entries(client, alice, pid)["jsons/dims.json"]
    assert (task["width"], task["height"]) == (7, 3)


def test_pertask_unreadable_task_name_falls_back(client, alice):
    """Traversal-ish or empty names never escape jsons/."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "../evil.jpg")
    _new_task(client, alice, pid, "")

    entries = _pertask_entries(client, alice, pid)
    assert len(entries) == 2
    for name in entries:
        assert name.startswith("jsons/")
        assert ".." not in name
    assert "jsons/evil.json" in entries


# ---------------------------------------------------------------------------
# Archive seam: these fail if ZIP construction moves back into a format builder
# ---------------------------------------------------------------------------

def test_zip_builder_prefixes_are_applied():
    """The folder prefix comes from the registry, not from the builder."""
    from api.routers import exports

    original = dict(exports.ZIP_BUILDERS)
    exports.ZIP_BUILDERS["stub"] = ("stub/", lambda tasks, labels: [("x.json", b"{}")])
    try:
        data = exports._build_zip(["stub"], [], {})
    finally:
        exports.ZIP_BUILDERS.clear()
        exports.ZIP_BUILDERS.update(original)

    assert zipfile.ZipFile(io.BytesIO(data)).namelist() == ["stub/x.json"]


def test_zip_cross_builder_names_do_not_collide():
    """Same base name under two prefixes must produce two distinct entries."""
    from api.routers import exports

    original = dict(exports.ZIP_BUILDERS)
    exports.ZIP_BUILDERS["one"] = ("one/", lambda tasks, labels: [("a.json", b"{}")])
    exports.ZIP_BUILDERS["two"] = ("two/", lambda tasks, labels: [("a.json", b"{}")])
    try:
        data = exports._build_zip(["one", "two"], [], {})
    finally:
        exports.ZIP_BUILDERS.clear()
        exports.ZIP_BUILDERS.update(original)

    assert sorted(zipfile.ZipFile(io.BytesIO(data)).namelist()) == ["one/a.json", "two/a.json"]


def test_zip_rejects_unnamespaced_entry():
    """A builder writing to the archive root without permission fails loudly."""
    import pytest

    from api.routers import exports

    original = dict(exports.ZIP_BUILDERS)
    exports.ZIP_BUILDERS["rogue"] = ("", lambda tasks, labels: [("loose.json", b"{}")])
    try:
        with pytest.raises(ValueError, match="unnamespaced"):
            exports._build_zip(["rogue"], [], {})
    finally:
        exports.ZIP_BUILDERS.clear()
        exports.ZIP_BUILDERS.update(original)


# ============================================================================
# T4: Annotation Import Enhancement
# ============================================================================

def test_import_pertask_with_title_and_value(client, alice):
    """T4.2: Import per-task format uses 'title' (display name) for label matching."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "test.jpg")
    
    # Import will create the label automatically using title (not value)
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
    
    # Verify label was created with the title (not value)
    labels = client.get(f"/api/labels?projectId={pid}", headers=alice).json()
    assert any(l["name"] == "AC Paint / Exposed Steel" for l in labels)  # matched by title
    
    # Verify color was preserved from annotation
    ac_label = next(l for l in labels if l["name"] == "AC Paint / Exposed Steel")
    assert ac_label["color"] == "#0FFFFF"


def test_import_annotation_matches_by_title_then_value(client, alice):
    """T4.3: Label matching prioritizes 'title' (display name) over 'value' (identifier)."""
    pid = _new_project(client, alice)
    # Create label with a specific display name
    _new_label(client, alice, pid, "Display Name", "#111")
    _new_task(client, alice, pid, "test.jpg")
    
    # Import annotation where title matches existing label name
    pertask_data = json.dumps([
        {
            "name": "test.jpg",
            "annotations": [
                {
                    "title": "Display Name",  # This should match
                    "value": "identifier_value",  # This is ignored for matching
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
    
    # Should have matched existing label by title, not created a new one
    labels = client.get(f"/api/labels?projectId={pid}", headers=alice).json()
    assert len(labels) == 1
    assert labels[0]["name"] == "Display Name"



def test_import_single_pertask_object(client, alice):
    """Import a single per-task JSON object (FastLabel format) not wrapped in array."""
    pid = _new_project(client, alice)
    lid = _new_label(client, alice, pid, "Cat", "#ef4444")
    tid = _new_task(client, alice, pid, "P1000066.JPG", annotations=[])

    # Single task object (not in array or "tasks" key) - FastLabel per-task format
    payload = {
        "name": "P1000066.JPG",
        "annotations": [
            {
                "points": [100, 100, 200, 100, 200, 200, 100, 200],
                "title": "Cat",
                "value": "Cat",
            }
        ],
    }

    # Preview first
    response = client.post(
        f"/api/imports/annotations/preview?projectId={pid}",
        files={"file": ("P1000066.json", json.dumps(payload).encode(), "application/json")},
        headers=alice,
    )
    assert response.status_code == 200
    body = response.json()
    assert len(body["matched"]) == 1
    assert body["matched"][0]["filename"] == "P1000066.JPG"
    assert body["matched"][0]["annotation_count"] == 1
    
    # Then import
    response = client.post(
        f"/api/imports/annotations?projectId={pid}&mode=replace",
        files={"file": ("P1000066.json", json.dumps(payload).encode(), "application/json")},
        headers=alice,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["tasks_updated"] == 1
    assert body["annotations_imported"] == 1


# ============================================================================
# T7: COCO import preserves category colors (PER_TASK_ZIP_EXPORT_PLAN.md §8)
# ============================================================================

def _import_coco(client, auth, pid, payload, mode="replace"):
    return client.post(
        f"/api/imports/annotations?projectId={pid}&mode={mode}",
        files={"file": ("coco.json", json.dumps(payload).encode(), "application/json")},
        headers=auth,
    )


def _coco_payload(categories):
    return {
        "images": [{"id": 1, "file_name": "shot.jpg", "width": 100, "height": 100}],
        "categories": categories,
        "annotations": [
            {"id": 1, "image_id": 1, "category_id": c["id"],
             "bbox": [10, 10, 20, 20], "area": 400, "iscrowd": 0}
            for c in categories
        ],
    }


def _labels_by_name(client, auth, pid):
    labels = client.get("/api/labels", params={"projectId": pid}, headers=auth).json()
    return {l["name"]: l for l in labels}


def test_import_coco_preserves_category_colors(client, alice):
    """§8.2: category color must survive import instead of being palette-assigned."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "shot.jpg")

    payload = _coco_payload([
        {"id": 1, "name": "RustArea", "supercategory": "RustArea", "color": "#D95319"},
        {"id": 2, "name": "Crack", "supercategory": "Crack", "color": "#0FFFFF"},
    ])
    assert _import_coco(client, alice, pid, payload).status_code == 200

    labels = _labels_by_name(client, alice, pid)
    assert labels["RustArea"]["color"] == "#D95319"
    assert labels["Crack"]["color"] == "#0FFFFF"


def test_import_coco_without_color_falls_back_to_palette(client, alice):
    """Standard COCO has no `color` key; import must still work."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "shot.jpg")

    payload = _coco_payload([{"id": 1, "name": "Person", "supercategory": "none"}])
    assert _import_coco(client, alice, pid, payload).status_code == 200

    label = _labels_by_name(client, alice, pid)["Person"]
    assert label["color"], "a label with no source color still needs some color"


def test_import_coco_does_not_recolor_existing_labels(client, alice):
    """§8.4: import must never repaint classes the project already has."""
    pid = _new_project(client, alice)
    _new_label(client, alice, pid, "RustArea", "#123456")
    _new_task(client, alice, pid, "shot.jpg")

    payload = _coco_payload([{"id": 1, "name": "RustArea", "color": "#D95319"}])
    assert _import_coco(client, alice, pid, payload).status_code == 200

    assert _labels_by_name(client, alice, pid)["RustArea"]["color"] == "#123456"


def test_coco_color_round_trip(client, alice):
    """Export COCO from one project, import into another: colors match."""
    src = _new_project(client, alice, name="src")
    lid = _new_label(client, alice, src, "Rust Area", "#D95319")
    _new_task(client, alice, src, "shot.jpg", annotations=[
        {"id": "a1", "labelId": lid, "points": [{"x": 10, "y": 10}, {"x": 30, "y": 30}]}
    ])

    res = client.post("/api/exports", json={"projectId": src, "format": "json"}, headers=alice)
    job_id = res.json()["job_id"]
    client.get(f"/api/exports/{job_id}", headers=alice)
    coco = client.get(f"/api/exports/{job_id}/download", headers=alice).json()

    dst = _new_project(client, alice, name="dst")
    _new_task(client, alice, dst, "shot.jpg")
    assert _import_coco(client, alice, dst, coco).status_code == 200

    assert _labels_by_name(client, alice, dst)["Rust Area"]["color"] == "#D95319"


# ============================================================================
# T8: ZIP import (PER_TASK_ZIP_EXPORT_PLAN.md 10)
# ============================================================================

def _zip_bytes(entries):
    """Build an in-memory ZIP from {arcname: bytes|str}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return buf.getvalue()


def _pertask_doc(name, title="Rust Area", color="#D95319", points=None):
    return {
        "id": "1", "name": name, "status": "New",
        "width": 100, "height": 100, "secondsToAnnotate": 0,
        "annotations": [{
            "id": uuid.uuid4().hex, "type": "polygon", "title": title,
            "value": title.replace(" ", ""), "color": color, "order": 1,
            "attributes": [], "points": points or [10, 10, 30, 10, 30, 30],
            "rotation": 0, "keypoints": [], "confidenceScore": -1,
        }],
    }


def _import_file(client, auth, pid, filename, raw, mode="replace"):
    return client.post(
        f"/api/imports/annotations?projectId={pid}&mode={mode}",
        files={"file": (filename, raw, "application/octet-stream")},
        headers=auth,
    )


def _task_annotations(client, auth, pid, description):
    """GET /api/tasks already returns annotations parsed."""
    tasks = client.get("/api/tasks", params={"projectId": pid}, headers=auth).json()
    return next(t for t in tasks if t["description"] == description)["annotations"]


def test_import_zip_of_pertask_files(client, alice):
    """10.2: every entry in the archive is imported."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "a.jpg")
    _new_task(client, alice, pid, "b.jpg")

    raw = _zip_bytes({
        "jsons/a.json": json.dumps(_pertask_doc("a.jpg")),
        "jsons/b.json": json.dumps(_pertask_doc("b.jpg")),
    })
    res = _import_file(client, alice, pid, "export.zip", raw)
    assert res.status_code == 200, res.text
    assert res.json()["tasks_updated"] == 2
    assert len(_task_annotations(client, alice, pid, "a.jpg")) == 1


def test_import_zip_detects_by_magic_not_extension(client, alice):
    """10.2: a ZIP named .json still imports."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "a.jpg")

    raw = _zip_bytes({"jsons/a.json": json.dumps(_pertask_doc("a.jpg"))})
    res = _import_file(client, alice, pid, "misnamed.json", raw)
    assert res.status_code == 200, res.text
    assert res.json()["tasks_updated"] == 1


def test_import_zip_reads_any_folder_depth(client, alice):
    """Not bound to jsons/ - a future coco/ folder must import too."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "a.jpg")

    raw = _zip_bytes({"some/nested/dir/a.json": json.dumps(_pertask_doc("a.jpg"))})
    assert _import_file(client, alice, pid, "e.zip", raw).json()["tasks_updated"] == 1


def test_import_zip_ignores_non_json_entries(client, alice):
    """Bundled images are skipped, not treated as errors."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "a.jpg")

    raw = _zip_bytes({
        "jsons/a.json": json.dumps(_pertask_doc("a.jpg")),
        "images/a.jpg": _png_bytes(4, 4),
        "README.txt": "not annotations",
    })
    res = _import_file(client, alice, pid, "e.zip", raw)
    assert res.status_code == 200, res.text
    assert res.json()["tasks_updated"] == 1


def test_import_zip_skips_corrupt_entry(client, alice):
    """10.2: one bad file must not lose the rest of the archive."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "a.jpg")
    _new_task(client, alice, pid, "b.jpg")

    raw = _zip_bytes({
        "jsons/a.json": json.dumps(_pertask_doc("a.jpg")),
        "jsons/broken.json": "{ this is not json",
        "jsons/b.json": json.dumps(_pertask_doc("b.jpg")),
    })
    res = _import_file(client, alice, pid, "e.zip", raw)
    assert res.status_code == 200, res.text
    assert res.json()["tasks_updated"] == 2


def test_import_zip_merges_duplicate_filenames(client, alice):
    """Two entries for one image concatenate rather than one winning."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "a.jpg")

    raw = _zip_bytes({
        "jsons/a.json": json.dumps(_pertask_doc("a.jpg", title="Rust Area")),
        "coco/a.json": json.dumps(_pertask_doc("a.jpg", title="Crack")),
    })
    assert _import_file(client, alice, pid, "e.zip", raw).status_code == 200
    assert len(_task_annotations(client, alice, pid, "a.jpg")) == 2


def test_import_zip_rejects_oversized_entry(client, alice):
    """10.3: per-entry cap is enforced from the header."""
    from api.routers import imports as imp

    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "a.jpg")

    original = imp._ZIP_MAX_ENTRY_BYTES
    imp._ZIP_MAX_ENTRY_BYTES = 10  # bytes
    try:
        raw = _zip_bytes({"jsons/a.json": json.dumps(_pertask_doc("a.jpg"))})
        res = _import_file(client, alice, pid, "e.zip", raw)
    finally:
        imp._ZIP_MAX_ENTRY_BYTES = original

    assert res.status_code == 422
    assert "limit" in res.json()["detail"].lower()


def test_import_zip_rejects_too_many_entries(client, alice):
    """10.3: entry-count cap is enforced."""
    from api.routers import imports as imp

    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "a.jpg")

    original = imp._ZIP_MAX_ENTRIES
    imp._ZIP_MAX_ENTRIES = 2
    try:
        raw = _zip_bytes({f"jsons/f{i}.json": "{}" for i in range(5)})
        res = _import_file(client, alice, pid, "e.zip", raw)
    finally:
        imp._ZIP_MAX_ENTRIES = original

    assert res.status_code == 422
    assert "limit" in res.json()["detail"].lower()


def test_import_zip_with_nothing_usable_is_422(client, alice):
    """An archive with no annotations reuses the existing empty-import path."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "a.jpg")

    raw = _zip_bytes({"images/a.jpg": _png_bytes(2, 2)})
    res = _import_file(client, alice, pid, "e.zip", raw)
    assert res.status_code == 422


def test_import_zip_preview_reports_all_entries(client, alice):
    """/preview counts the whole archive before anything is written."""
    pid = _new_project(client, alice)
    _new_task(client, alice, pid, "a.jpg")
    _new_task(client, alice, pid, "b.jpg")

    raw = _zip_bytes({
        "jsons/a.json": json.dumps(_pertask_doc("a.jpg")),
        "jsons/b.json": json.dumps(_pertask_doc("b.jpg")),
    })
    res = client.post(
        f"/api/imports/annotations/preview?projectId={pid}",
        files={"file": ("e.zip", raw, "application/zip")}, headers=alice,
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert len(body["matched"]) == 2
    assert body["total_annotations"] == 2
    # Nothing written yet.
    assert _task_annotations(client, alice, pid, "a.jpg") == []


def test_zip_round_trip_export_then_import(client, alice):
    """The headline: export a project as ZIP, import it into an empty one."""
    src = _new_project(client, alice, name="rt-src")
    lid = _new_label(client, alice, src, "Rust Area", "#D95319")
    _new_task(client, alice, src, "P1000066.JPG", annotations=[
        {"id": "a1", "labelId": lid,
         "points": [{"x": 10, "y": 10}, {"x": 30, "y": 10}, {"x": 30, "y": 30}]}
    ])

    archive = _download_pertask_zip(client, alice, src).content

    dst = _new_project(client, alice, name="rt-dst")
    _new_task(client, alice, dst, "P1000066.JPG")
    res = _import_file(client, alice, dst, "export-pertask.zip", archive)
    assert res.status_code == 200, res.text
    assert res.json()["tasks_updated"] == 1

    anns = _task_annotations(client, alice, dst, "P1000066.JPG")
    assert len(anns) == 1
    labels = {l["name"]: l for l in client.get("/api/labels", params={"projectId": dst}, headers=alice).json()}
    assert "Rust Area" in labels
    assert labels["Rust Area"]["color"] == "#D95319"  # color survives the trip
    assert anns[0]["labelId"] == labels["Rust Area"]["id"]
    assert anns[0]["points"][0] == {"x": 10, "y": 10}

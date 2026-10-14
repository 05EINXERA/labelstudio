"""Hull-condition semantic segmentation: mask -> polygons, and the endpoint gate.

The model itself is not loaded here (no weights, no GPU in CI); the polygon
extraction is pure numpy/OpenCV and is what decides what reaches the canvas.
"""
import numpy as np
import pytest

import config
from ml import semseg

CLASSES = [{"name": "background_sound"}, {"name": "af_paint_deterioration"}, {"name": "rust"}]
IMAGE = "data:image/png;base64,iVBORw0KGgo="


def _maps(height=100, width=200):
    return np.zeros((height, width), np.int64), np.full((height, width), 0.9, np.float32)


def test_regions_become_polygons_in_source_pixels():
    class_map, confidence = _maps()
    class_map[10:50, 20:80] = 1  # 60 x 40 at model resolution
    predictions = semseg.mask_to_predictions(class_map, confidence, CLASSES, scale=0.5,
                                             min_area=0, max_polygons=10)
    assert len(predictions) == 1
    region = predictions[0]
    assert region["class"] == "af_paint_deterioration"
    assert region["label_prefix"] == "af paint"
    # scale 0.5 means source coordinates are twice the model's: the region
    # covers x 40-160, y 20-100. Outlines run through boundary pixel centres
    # and their corners are rounded, so allow a couple of pixels.
    assert region["bbox"] == pytest.approx([40.0, 20.0, 120.0, 80.0], abs=3)
    xs = [p["x"] for p in region["points"]]
    ys = [p["y"] for p in region["points"]]
    assert min(xs) == pytest.approx(41, abs=1.5) and max(xs) == pytest.approx(159, abs=1.5)
    assert min(ys) == pytest.approx(21, abs=1.5) and max(ys) == pytest.approx(99, abs=1.5)
    assert region["score"] == 0.9


def test_background_class_yields_nothing():
    class_map, confidence = _maps()
    assert semseg.mask_to_predictions(class_map, confidence, CLASSES, 0.5, 0, 10) == []


def test_small_regions_are_dropped_by_source_area():
    class_map, confidence = _maps()
    class_map[0:5, 0:5] = 2      # ~16 px2 at model scale -> ~64 px2 in the source
    class_map[20:60, 20:60] = 2  # ~1521 px2 -> ~6084 px2
    predictions = semseg.mask_to_predictions(class_map, confidence, CLASSES, 0.5,
                                             min_area=600, max_polygons=10)
    assert [p["class"] for p in predictions] == ["rust"]
    assert predictions[0]["bbox"][0] == pytest.approx(40.0, abs=2)


def test_largest_regions_win_the_cap():
    class_map, confidence = _maps()
    class_map[0:10, 0:10] = 1
    class_map[20:60, 20:80] = 2
    class_map[70:90, 100:130] = 1
    predictions = semseg.mask_to_predictions(class_map, confidence, CLASSES, 1.0,
                                             min_area=0, max_polygons=2)
    assert [p["class"] for p in predictions] == ["rust", "af_paint_deterioration"]
    assert predictions[1]["bbox"][0] == pytest.approx(100.0, abs=2)


def test_low_confidence_pixels_are_left_as_background():
    """The cut-off drops unsure regions and trims unsure edges of sure ones."""
    class_map, confidence = _maps()
    class_map[10:50, 20:80] = 1
    confidence[10:50, 20:80] = 0.9
    confidence[10:50, 60:80] = 0.4   # the right-hand third of that region is unsure
    class_map[60:90, 100:160] = 2
    confidence[60:90, 100:160] = 0.5  # a whole region below the cut-off

    everything = semseg.mask_to_predictions(class_map, confidence, CLASSES, 1.0, 0, 10)
    assert sorted(p["class"] for p in everything) == ["af_paint_deterioration", "rust"]

    sure = semseg.mask_to_predictions(class_map, confidence, CLASSES, 1.0, 0, 10, min_confidence=0.6)
    assert [p["class"] for p in sure] == ["af_paint_deterioration"]
    # 60 wide before, 40 after trimming
    assert sure[0]["bbox"] == pytest.approx([20.0, 10.0, 40.0, 40.0], abs=2)
    assert sure[0]["score"] == 0.9


def test_unavailable_without_flag_or_weights(monkeypatch, tmp_path):
    weights = tmp_path / "hull.pt"
    monkeypatch.setattr(config, "SEMSEG_MODEL", str(weights))
    monkeypatch.setattr(config, "SEMSEG_ENABLED", True)
    assert semseg.semseg_available() is False  # flag on, file missing
    weights.write_bytes(b"x")
    assert semseg.semseg_available() is True
    monkeypatch.setattr(config, "SEMSEG_ENABLED", False)
    assert semseg.semseg_available() is False  # file present, flag off


def test_endpoint_refuses_when_not_enabled(client, alice, monkeypatch):
    monkeypatch.setattr(config, "AI_FEATURES_ENABLED", True)
    monkeypatch.setattr(config, "SEMSEG_ENABLED", False)
    res = client.post("/api/detect/semantic", json={"image": IMAGE}, headers=alice)
    assert res.status_code == 503, res.text


def test_endpoint_refused_by_ai_kill_switch(client, alice, monkeypatch, tmp_path):
    weights = tmp_path / "hull.pt"
    weights.write_bytes(b"x")
    monkeypatch.setattr(config, "SEMSEG_MODEL", str(weights))
    monkeypatch.setattr(config, "SEMSEG_ENABLED", True)
    monkeypatch.setattr(config, "AI_FEATURES_ENABLED", False)
    res = client.post("/api/detect/semantic", json={"image": IMAGE}, headers=alice)
    assert res.status_code == 503, res.text
    assert client.get("/api/detect/availability", headers=alice).json() == {"enabled": False, "semantic": False}


def test_endpoint_requires_auth(client):
    res = client.post("/api/detect/semantic", json={"image": IMAGE})
    assert res.status_code in (401, 403)


def test_enabled_endpoint_queues_a_job(client, alice, monkeypatch, tmp_path):
    """With flag and weights present the request is queued and the job runs
    the (stubbed) model, returning its predictions through the status poll."""
    from api.routers import detect

    weights = tmp_path / "hull.pt"
    weights.write_bytes(b"x")
    monkeypatch.setattr(config, "SEMSEG_MODEL", str(weights))
    monkeypatch.setattr(config, "SEMSEG_ENABLED", True)
    monkeypatch.setattr(config, "AI_FEATURES_ENABLED", True)
    stub = {"predictions": [{"class": "rust", "label_prefix": "rust", "score": 0.8,
                             "bbox": [0, 0, 4, 4], "points": [{"x": 0, "y": 0}, {"x": 4, "y": 0}, {"x": 4, "y": 4}]}]}
    monkeypatch.setattr(detect, "segment_semantic", lambda image: stub)

    assert client.get("/api/detect/availability", headers=alice).json() == {"enabled": True, "semantic": True}
    res = client.post("/api/detect/semantic", json={"image": IMAGE}, headers=alice)
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    status = client.get(f"/api/detect/status/{job_id}", headers=alice).json()
    assert status == {"status": "completed", "result": stub}


def test_architectures_return_logits_at_input_size():
    """Both checkpoint architectures rebuild and keep the {"out": logits} contract."""
    import torch
    from ml.semseg_arch import STRIDE, build_semseg_net

    for arch in ("unet_resnet34", "deeplabv3_mobilenet_v3_large"):
        model = build_semseg_net(arch, num_classes=8).eval()
        with torch.no_grad():
            out = model(torch.zeros(1, 3, STRIDE * 2, STRIDE * 3))["out"]
        assert out.shape == (1, 8, STRIDE * 2, STRIDE * 3), arch


def test_unknown_architecture_is_rejected():
    import pytest
    from ml.semseg_arch import build_semseg_net

    with pytest.raises(ValueError):
        build_semseg_net("not-a-model", 8)


def test_merged_dirt_class_maps_to_the_dirt_1_label():
    class_map, confidence = _maps()
    class_map[10:50, 20:80] = 1
    classes = [{"name": "background_sound"}, {"name": "dirt"}]
    region = semseg.mask_to_predictions(class_map, confidence, classes, 1.0, 0, 10)[0]
    assert (region["class"], region["label_prefix"]) == ("dirt", "dirt 1")


def _turn_degrees(points):
    """Absolute turning angle at each vertex of a closed polygon."""
    p = np.array([[q["x"], q["y"]] for q in points])
    a, b = p - np.roll(p, 1, axis=0), np.roll(p, -1, axis=0) - p
    cos = (a * b).sum(1) / (np.linalg.norm(a, axis=1) * np.linalg.norm(b, axis=1))
    return np.degrees(np.arccos(np.clip(cos, -1, 1)))


def test_staircase_edge_becomes_smooth_and_evenly_spaced():
    """A diagonal edge of a pixel mask is a staircase of 90-degree steps; the
    outline must not reproduce those steps as sharp vertices."""
    class_map, confidence = _maps(200, 200)
    for row in range(20, 180):
        class_map[row, 20:20 + (row - 20)] = 1  # right triangle with a stepped hypotenuse
    region = semseg.mask_to_predictions(class_map, confidence, CLASSES, 1.0, 0, 10, vertex_spacing=10)[0]
    turns = _turn_degrees(region["points"])
    # Only the three real corners may turn sharply (each is rounded over a
    # couple of vertices); along the stepped edge the outline runs straight.
    assert (turns > 45).sum() <= 6, sorted(turns)[-8:]
    assert np.median(turns) < 2, np.median(turns)
    p = np.array([[q["x"], q["y"]] for q in region["points"]])
    gaps = np.linalg.norm(p - np.roll(p, -1, axis=0), axis=1)
    # Even spacing along the outline; the straight-line gap only shrinks where
    # it cuts across a rounded corner.
    assert np.median(gaps) == pytest.approx(10, abs=0.5) and gaps.max() < 11, (np.median(gaps), gaps.max())
    assert gaps.min() > 5, gaps.min()


def test_vertex_spacing_sets_the_vertex_count():
    class_map, confidence = _maps(300, 300)
    class_map[50:250, 50:250] = 1  # perimeter about 800 source pixels
    for spacing, expected in ((10, 80), (20, 40)):
        region = semseg.mask_to_predictions(class_map, confidence, CLASSES, 1.0, 0, 10, vertex_spacing=spacing)[0]
        assert abs(len(region["points"]) - expected) <= 4, (spacing, len(region["points"]))


def test_smoothing_keeps_the_region_where_it_was():
    """Area and position survive smoothing: no shrinking or drifting."""
    class_map, confidence = _maps(300, 300)
    class_map[50:250, 80:200] = 1
    region = semseg.mask_to_predictions(class_map, confidence, CLASSES, 1.0, 0, 10)[0]
    p = np.array([[q["x"], q["y"]] for q in region["points"]])
    area = 0.5 * abs(np.dot(p[:, 0], np.roll(p[:, 1], 1)) - np.dot(p[:, 1], np.roll(p[:, 0], 1)))
    assert area == pytest.approx(200 * 120, rel=0.03)
    assert p.mean(axis=0) == pytest.approx([140, 150], abs=1.5)


def test_tiny_regions_still_get_a_usable_polygon():
    outline = semseg.smooth_outline(np.array([[0, 0], [1, 0], [1, 1], [0, 1]], float) * 4, spacing=10)
    assert len(outline) == semseg._MIN_VERTICES

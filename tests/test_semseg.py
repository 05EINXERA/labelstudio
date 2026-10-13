"""Hull-condition semantic segmentation: mask -> polygons, and the endpoint gate.

The model itself is not loaded here (no weights, no GPU in CI); the polygon
extraction is pure numpy/OpenCV and is what decides what reaches the canvas.
"""
import numpy as np

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
    # scale 0.5 means source coordinates are twice the model's.
    assert region["bbox"] == [40.0, 20.0, 120.0, 80.0]
    xs = [p["x"] for p in region["points"]]
    ys = [p["y"] for p in region["points"]]
    assert min(xs) == 40.0 and max(xs) == 158.0
    assert min(ys) == 20.0 and max(ys) == 98.0
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
    assert predictions[0]["bbox"][0] == 40.0


def test_largest_regions_win_the_cap():
    class_map, confidence = _maps()
    class_map[0:10, 0:10] = 1
    class_map[20:60, 20:80] = 2
    class_map[70:90, 100:130] = 1
    predictions = semseg.mask_to_predictions(class_map, confidence, CLASSES, 1.0,
                                             min_area=0, max_polygons=2)
    assert [p["class"] for p in predictions] == ["rust", "af_paint_deterioration"]
    assert predictions[1]["bbox"][0] == 100.0


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
    assert sure[0]["bbox"] == [20.0, 10.0, 40.0, 40.0]  # 60 wide before, 40 after trimming
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

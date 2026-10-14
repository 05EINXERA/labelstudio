"""Hull-condition semantic segmentation: mask -> polygons, and the endpoint gate.

The model itself is not loaded here (no weights, no GPU in CI); the polygon
extraction is pure numpy/OpenCV and is what decides what reaches the canvas.
"""
import os

import numpy as np
import pytest
import torch

import config
from ml import semseg

CLASSES = [{"name": "background_sound"}, {"name": "af_paint_deterioration"}, {"name": "rust"}]
IMAGE = "data:image/png;base64,iVBORw0KGgo="


def _checkpoint(path, **meta):
    """A weights file carrying only the metadata the app reads without loading the model."""
    torch.save({"model": {}, "classes": CLASSES, **meta}, path)
    return path


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


def test_model_info_names_the_training_run(monkeypatch, tmp_path):
    weights = _checkpoint(tmp_path / "hull_semseg.pt", name="hull_v5_own", epoch=126)
    monkeypatch.setattr(config, "SEMSEG_MODEL", str(weights))
    monkeypatch.setattr(config, "SEMSEG_ENABLED", True)
    assert semseg.semseg_model_info() == {"name": "hull_v5_own", "epoch": 126}


def test_model_info_falls_back_to_file_name_and_rereads_a_replaced_file(monkeypatch, tmp_path):
    """Checkpoints from before the `name` key show their file name; a new
    file at the same path is picked up without a restart."""
    weights = _checkpoint(tmp_path / "hull_semseg.pt", epoch=3)
    monkeypatch.setattr(config, "SEMSEG_MODEL", str(weights))
    monkeypatch.setattr(config, "SEMSEG_ENABLED", True)
    assert semseg.semseg_model_info() == {"name": "hull_semseg", "epoch": 3}
    _checkpoint(weights, name="hull_next", epoch=9)
    stat = os.stat(weights)
    os.utime(weights, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))  # coarse clocks: force a new mtime
    assert semseg.semseg_model_info() == {"name": "hull_next", "epoch": 9}


def test_model_info_reports_the_loaded_model(monkeypatch, tmp_path):
    """Once loaded, the model in memory is what runs, whatever the file now holds."""
    weights = _checkpoint(tmp_path / "hull_semseg.pt", name="on_disk", epoch=1)
    monkeypatch.setattr(config, "SEMSEG_MODEL", str(weights))
    monkeypatch.setattr(config, "SEMSEG_ENABLED", True)
    monkeypatch.setattr(semseg, "_info", {"name": "in_memory", "epoch": 2})
    assert semseg.semseg_model_info() == {"name": "in_memory", "epoch": 2}


def test_model_info_is_none_for_unreadable_or_missing_weights(monkeypatch, tmp_path):
    weights = tmp_path / "hull.pt"
    monkeypatch.setattr(config, "SEMSEG_MODEL", str(weights))
    monkeypatch.setattr(config, "SEMSEG_ENABLED", True)
    assert semseg.semseg_model_info() is None  # no file
    weights.write_bytes(b"not a checkpoint")
    assert semseg.semseg_model_info() is None


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
    assert client.get("/api/detect/availability", headers=alice).json() == {
        "enabled": False, "semantic": False, "semantic_model": None}


def test_endpoint_requires_auth(client):
    res = client.post("/api/detect/semantic", json={"image": IMAGE})
    assert res.status_code in (401, 403)


def test_enabled_endpoint_queues_a_job(client, alice, monkeypatch, tmp_path):
    """With flag and weights present the request is queued and the job runs
    the (stubbed) model, returning its predictions through the status poll."""
    from api.routers import detect

    weights = _checkpoint(tmp_path / "hull.pt", name="hull_test", epoch=7)
    monkeypatch.setattr(config, "SEMSEG_MODEL", str(weights))
    monkeypatch.setattr(config, "SEMSEG_ENABLED", True)
    monkeypatch.setattr(config, "AI_FEATURES_ENABLED", True)
    stub = {"predictions": [{"class": "rust", "label_prefix": "rust", "score": 0.8,
                             "bbox": [0, 0, 4, 4], "points": [{"x": 0, "y": 0}, {"x": 4, "y": 0}, {"x": 4, "y": 4}]}],
            "model": {"name": "hull_test", "epoch": 7}}
    calls = []
    monkeypatch.setattr(detect, "segment_semantic", lambda image, **kwargs: calls.append(kwargs) or stub)

    assert client.get("/api/detect/availability", headers=alice).json() == {
        "enabled": True, "semantic": True, "semantic_model": {"name": "hull_test", "epoch": 7}}
    res = client.post("/api/detect/semantic", json={"image": IMAGE}, headers=alice)
    assert res.status_code == 200, res.text
    job_id = res.json()["job_id"]
    status = client.get(f"/api/detect/status/{job_id}", headers=alice).json()
    assert status == {"status": "completed", "result": stub}
    assert calls == [{"keep": [], "refine": [], "repeat": False}]  # a first press


def test_repeat_request_passes_regions_to_the_model(client, alice, monkeypatch, tmp_path):
    from api.routers import detect

    weights = _checkpoint(tmp_path / "hull.pt", name="hull_test", epoch=7)
    monkeypatch.setattr(config, "SEMSEG_MODEL", str(weights))
    monkeypatch.setattr(config, "SEMSEG_ENABLED", True)
    monkeypatch.setattr(config, "AI_FEATURES_ENABLED", True)
    calls = []
    monkeypatch.setattr(detect, "segment_semantic",
                        lambda image, **kwargs: calls.append(kwargs) or {"predictions": [], "model": None})
    square = [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}]
    res = client.post("/api/detect/semantic", headers=alice,
                      json={"image": IMAGE, "keep": [square], "refine": [square, square], "repeat": True})
    assert res.status_code == 200, res.text
    client.get(f"/api/detect/status/{res.json()['job_id']}", headers=alice)
    corners = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0)]
    assert calls == [{"keep": [corners], "refine": [corners, corners], "repeat": True}]


def test_oversized_region_list_is_rejected(client, alice, monkeypatch):
    from app import schemas

    monkeypatch.setattr(config, "AI_FEATURES_ENABLED", True)
    monkeypatch.setattr(schemas, "MAX_SEMANTIC_POLYGON_POINTS", 5)
    square = [{"x": 0, "y": 0}, {"x": 10, "y": 0}, {"x": 10, "y": 10}]
    res = client.post("/api/detect/semantic", headers=alice, json={"image": IMAGE, "keep": [square, square]})
    assert res.status_code == 422, res.text


# --- Hull pressed again (compose_repeat) -------------------------------------

def _probs(height=40, width=60, classes=3):
    """Probabilities that say "sound hull" everywhere, to paint regions into."""
    p = np.zeros((classes, height, width), np.float32)
    p[0] = 1.0
    return p


def _paint(p, rows, cols, cls, probability):
    """Damage class `cls` at `probability`, the rest on sound hull."""
    p[:, rows, cols] = 0.0
    p[0, rows, cols] = 1.0 - probability
    p[cls, rows, cols] = probability


def test_polygons_mask_scales_source_pixels_to_the_model_grid():
    mask = semseg.polygons_mask([[(20, 20), (60, 20), (60, 60), (20, 60)]], 0.5, (40, 60))
    ys, xs = np.nonzero(mask)
    assert (xs.min(), xs.max(), ys.min(), ys.max()) == (10, 30, 10, 30)
    grown = semseg.polygons_mask([[(20, 20), (60, 20), (60, 60), (20, 60)]], 0.5, (40, 60), margin=2)
    assert grown.sum() > mask.sum() and grown[10, 8] and not mask[10, 8]
    assert not semseg.polygons_mask([[(1, 1), (5, 5)]], 1.0, (10, 10)).any()  # not an area


def test_repeat_redraws_untouched_keeps_edited_and_leaves_deleted_empty():
    rows_a, cols_a = slice(5, 15), slice(5, 15)     # untouched earlier region (AF paint)
    rows_c, cols_c = slice(5, 15), slice(30, 40)    # earlier region the annotator deleted (rust)
    rows_k, cols_k = slice(25, 35), slice(5, 15)    # the annotator's own region
    rows_g, cols_g = slice(25, 35), slice(40, 55)   # never flagged: a gap
    standard, refined = _probs(), _probs()
    _paint(standard, rows_a, cols_a, 1, 0.9)
    _paint(standard, rows_c, cols_c, 2, 0.9)
    _paint(refined, slice(4, 16), slice(4, 16), 1, 0.8)  # the sharper pass grows A a little
    _paint(refined, rows_c, cols_c, 2, 0.8)
    _paint(refined, rows_k, cols_k, 1, 0.8)
    _paint(refined, rows_g, cols_g, 2, 0.35)              # unsure: sound still the likeliest
    _paint(refined, slice(36, 40), slice(40, 55), 2, 0.1)  # too unsure even for a gap
    kept = np.zeros((40, 60), bool)
    kept[rows_k, cols_k] = True
    redraw = np.zeros((40, 60), bool)
    redraw[2:18, 2:18] = True  # A plus REDRAW_MARGIN

    class_map, confidence = semseg.compose_repeat(standard, refined, kept, redraw, gap_min_probability=0.3)

    assert (class_map[4:16, 4:16] == 1).all()   # A redrawn, with the sharper outline
    assert (class_map[rows_c, cols_c] == 0).all()  # deleted stays deleted
    assert (class_map[rows_k, cols_k] == 0).all()  # nothing over the annotator's region
    assert (class_map[rows_g, cols_g] == 2).all()  # found in the gap below the usual bar
    assert confidence[30, 45] == pytest.approx(0.35)
    assert (class_map[37:40, 40:55] == 0).all()


def test_repeat_applies_the_confidence_cut_off_to_redrawn_regions_only():
    standard, refined = _probs(), _probs()
    _paint(standard, slice(5, 15), slice(5, 15), 1, 0.9)
    _paint(refined, slice(5, 15), slice(5, 15), 1, 0.5)
    _paint(refined, slice(25, 35), slice(40, 55), 2, 0.35)
    redraw = np.zeros((40, 60), bool)
    redraw[2:18, 2:18] = True
    class_map, _ = semseg.compose_repeat(standard, refined, np.zeros((40, 60), bool), redraw,
                                         min_confidence=0.6, gap_min_probability=0.3)
    assert (class_map[5:15, 5:15] == 0).all()     # redrawn region below the cut-off
    assert (class_map[25:35, 40:55] == 2).all()   # gaps use their own bar


def test_gap_filling_is_off_above_one():
    refined = _probs()
    _paint(refined, slice(25, 35), slice(40, 55), 2, 0.99)
    nothing = np.zeros((40, 60), bool)
    class_map, _ = semseg.compose_repeat(_probs(), refined, nothing, nothing, gap_min_probability=1.01)
    assert not class_map.any()


def _stub_model(monkeypatch, standard, refined=None):
    """segment_semantic with fixed probabilities instead of the network."""
    import base64
    import io
    from PIL import Image

    calls = []

    def fake(model, image, scale, device, flips=""):
        calls.append(flips)
        return (refined if flips and refined is not None else standard).copy()

    monkeypatch.setattr(config, "SEMSEG_ENABLED", True)
    monkeypatch.setattr(config, "SEMSEG_MIN_AREA_PX", 0)
    monkeypatch.setattr(config, "SEMSEG_MIN_CONFIDENCE", 0)
    monkeypatch.setattr(semseg, "semseg_available", lambda: True)
    monkeypatch.setattr(semseg, "get_semseg_model", lambda: (None, CLASSES, None, 0.5))
    monkeypatch.setattr(semseg, "_probabilities", fake)
    buffer = io.BytesIO()
    Image.new("RGB", (120, 80)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode(), calls


def test_first_press_draws_nothing_over_existing_regions(monkeypatch):
    standard = _probs()
    _paint(standard, slice(5, 35), slice(5, 55), 1, 0.9)  # one big region on the model grid
    image, calls = _stub_model(monkeypatch, standard)
    everything = semseg.segment_semantic(image)["predictions"]
    # The annotator already drew the left half of it (source pixels, scale 0.5).
    left = [(0, 0), (60, 0), (60, 80), (0, 80)]
    rest = semseg.segment_semantic(image, keep=[left])["predictions"]
    assert calls == ["", ""]  # one plain pass each, no flips on a first press
    assert len(everything) == 1 and everything[0]["bbox"][0] < 15
    assert len(rest) == 1 and rest[0]["bbox"][0] >= 60  # starts right of the kept half


def test_repeat_press_runs_the_flipped_passes(monkeypatch):
    standard = _probs()
    _paint(standard, slice(5, 15), slice(5, 15), 1, 0.9)
    image, calls = _stub_model(monkeypatch, standard, refined=standard)
    region = [(10, 10), (30, 10), (30, 30), (10, 30)]
    predictions = semseg.segment_semantic(image, refine=[region], repeat=True)["predictions"]
    assert calls == ["", *semseg.REFINE_FLIPS]
    assert [p["class"] for p in predictions] == ["af_paint_deterioration"]


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

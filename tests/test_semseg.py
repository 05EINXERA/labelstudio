"""Hull-condition semantic segmentation: mask -> polygons, and the endpoint gate.

The model itself is not loaded here (no weights, no GPU in CI); the polygon
extraction is pure numpy/OpenCV and is what decides what reaches the canvas.
"""
import os
from collections import OrderedDict

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


def _touch_later(path, seconds):
    """Give a file a distinct mtime (coarse clocks, and the picker sorts on it)."""
    stat = os.stat(path)
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + seconds * 10**9))


def _model_folder(monkeypatch, tmp_path):
    """A detector folder holding several hull checkpoints; hull_semseg.pt is the default."""
    monkeypatch.setattr(semseg, "DETECTOR_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(config, "SEMSEG_MODEL", "hull_semseg.pt")
    monkeypatch.setattr(config, "SEMSEG_ENABLED", True)
    _checkpoint(tmp_path / "hull_semseg.pt", name="hull_v6_all", epoch=120)
    _checkpoint(tmp_path / "hull_semseg_v6_all.pt", name="hull_v6_all", epoch=120)  # same model, second name
    _touch_later(_checkpoint(tmp_path / "hull_semseg_v5.pt", name="hull_v5", epoch=74), 1)
    _touch_later(_checkpoint(tmp_path / "hull_semseg_v6.pt", name="hull_v6", epoch=110), 2)
    (tmp_path / "hull_semseg_broken.pt").write_bytes(b"not a checkpoint")
    _checkpoint(tmp_path / "other.pt", name="not_hull", epoch=1)  # outside the hull_semseg*.pt pattern


def test_picker_lists_default_first_then_newest_without_duplicates(monkeypatch, tmp_path):
    _model_folder(monkeypatch, tmp_path)
    assert semseg.semseg_models() == [
        {"id": "hull_semseg.pt", "name": "hull_v6_all", "epoch": 120, "default": True},
        {"id": "hull_semseg_v6.pt", "name": "hull_v6", "epoch": 110, "default": False},
        {"id": "hull_semseg_v5.pt", "name": "hull_v5", "epoch": 74, "default": False},
    ]
    monkeypatch.setattr(config, "SEMSEG_ENABLED", False)
    assert semseg.semseg_models() == []


def test_only_listed_models_can_be_requested(monkeypatch, tmp_path):
    _model_folder(monkeypatch, tmp_path)
    assert semseg.semseg_model_path(None) == str(tmp_path / "hull_semseg.pt")
    assert semseg.semseg_model_path("") == str(tmp_path / "hull_semseg.pt")
    assert semseg.semseg_model_path("hull_semseg_v6.pt") == str(tmp_path / "hull_semseg_v6.pt")
    for refused in ("other.pt", "hull_semseg_broken.pt", "../hull_semseg_v6.pt",
                    str(tmp_path / "hull_semseg_v6.pt"), "hull_semseg_v6_all.pt"):
        with pytest.raises(semseg.DetectionClientError):
            semseg.semseg_model_path(refused)


class _TinyNet(torch.nn.Module):
    def __init__(self, classes):
        super().__init__()
        self.conv = torch.nn.Conv2d(3, classes, 1)

    def forward(self, x):
        return {"out": self.conv(x)}


def test_loaded_models_are_reused_capped_and_reloaded_when_replaced(monkeypatch, tmp_path):
    from ml import semseg_arch

    monkeypatch.setattr(semseg_arch, "build_semseg_net", lambda arch, classes: _TinyNet(classes))
    monkeypatch.setattr(semseg, "_loaded", OrderedDict())
    monkeypatch.setattr(semseg, "MAX_LOADED_MODELS", 2)

    def write(name, epoch):
        path = tmp_path / f"{name}.pt"
        torch.save({"model": _TinyNet(len(CLASSES)).state_dict(), "classes": CLASSES,
                    "name": name, "epoch": epoch, "arch": "tiny"}, path)
        return str(path)

    a, b, c = write("a", 1), write("b", 1), write("c", 1)
    first = semseg.get_semseg_model(a)
    assert semseg.get_semseg_model(a)[0] is first[0]  # reused, not reloaded
    assert first[4] == {"name": "a", "epoch": 1}
    semseg.get_semseg_model(b)
    semseg.get_semseg_model(c)
    assert list(semseg._loaded) == [b, c]  # a, least recently used, was dropped

    write("b", 2)
    _touch_later(b, 1)
    assert semseg.get_semseg_model(b)[4] == {"name": "b", "epoch": 2}


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
        "enabled": False, "semantic": False, "semantic_model": None, "semantic_models": []}


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
    monkeypatch.setattr(detect, "segment_semantic", lambda image, model=None: stub)

    assert client.get("/api/detect/availability", headers=alice).json() == {
        "enabled": True, "semantic": True, "semantic_model": {"name": "hull_test", "epoch": 7},
        "semantic_models": [{"id": "hull.pt", "name": "hull_test", "epoch": 7, "default": True}]}
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


def test_flip_passes():
    assert semseg.flip_passes("") == [()]
    assert semseg.flip_passes("h") == [(), (-1,)]
    assert sorted(semseg.flip_passes("hv")) == sorted([(), (-1,), (-2,), (-1, -2)])


class _PixelModel(torch.nn.Module):
    """Logits from each pixel's own colour only, so a mirrored photo gives the
    mirrored answer: flip averaging must then change nothing."""

    def __init__(self):
        super().__init__()
        self.conv = torch.nn.Conv2d(3, 3, 1)

    def forward(self, x):
        return {"out": self.conv(x)}


def test_mirrored_passes_land_back_on_the_photo():
    """The photo is padded to the network stride; a mirrored pass must be
    mirrored back before the pad is cut, or every region shifts by the pad."""
    from PIL import Image

    torch.manual_seed(0)
    model = _PixelModel().eval()
    rng = np.random.default_rng(0)
    image = Image.fromarray(rng.integers(0, 255, (45, 70, 3), dtype=np.uint8))  # not stride-aligned
    plain = semseg.probabilities_for(model, image, 1.0, torch.device("cpu"))
    mirrored = semseg.probabilities_for(model, image, 1.0, torch.device("cpu"), flips="hv")
    assert plain.shape == (3, 45, 70)
    np.testing.assert_allclose(mirrored, plain, atol=1e-5)


def test_large_photos_are_shrunk_before_the_model(monkeypatch):
    """A 20 MP photo must not reach the network at full size (it overflowed
    the GPU into system RAM); polygons still come back in source pixels."""
    from PIL import Image

    monkeypatch.setattr(semseg, "MAX_SIDE_PX", 64)
    model = _PixelModel().eval()
    small = semseg.probabilities_for(model, Image.new("RGB", (60, 40)), 1.0, torch.device("cpu"))
    large = semseg.probabilities_for(model, Image.new("RGB", (128, 96)), 1.0, torch.device("cpu"))
    assert small.shape[1:] == (40, 60)  # under the cap: untouched
    assert large.shape[1:] == (48, 64)  # long side capped, aspect kept

    class_map = np.zeros(large.shape[1:], np.int64)
    class_map[10:30, 20:40] = 1
    ratio = large.shape[2] / 128
    region = semseg.mask_to_predictions(class_map, np.ones(class_map.shape, np.float32), CLASSES,
                                        ratio, 0, 10)[0]
    assert region["bbox"] == pytest.approx([40.0, 20.0, 40.0, 40.0], abs=3)


def test_damage_min_probability_takes_damage_over_likelier_sound():
    probabilities = np.zeros((3, 1, 4), np.float32)
    probabilities[:, 0, 0] = [0.55, 0.40, 0.05]  # sound wins, AF paint at 0.40
    probabilities[:, 0, 1] = [0.75, 0.05, 0.20]  # sound wins, rust only 0.20
    probabilities[:, 0, 2] = [0.20, 0.10, 0.70]  # rust already wins
    probabilities[:, 0, 3] = [0.40, 0.35, 0.25]  # sound wins narrowly

    plain, _ = semseg.pick_classes(probabilities)
    assert plain[0].tolist() == [0, 0, 2, 0]

    eager, confidence = semseg.pick_classes(probabilities, damage_min_probability=0.3)
    assert eager[0].tolist() == [1, 0, 2, 1]
    assert confidence[0].tolist() == pytest.approx([0.40, 0.75, 0.70, 0.35])


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


def test_endpoint_runs_the_picked_model_and_refuses_unknown_ones(client, alice, monkeypatch, tmp_path):
    from api.routers import detect

    _model_folder(monkeypatch, tmp_path)
    monkeypatch.setattr(config, "AI_FEATURES_ENABLED", True)
    used = []
    monkeypatch.setattr(detect, "segment_semantic",
                        lambda image, model=None: used.append(model) or {"predictions": [], "model": None})

    models = client.get("/api/detect/availability", headers=alice).json()["semantic_models"]
    assert [m["id"] for m in models] == ["hull_semseg.pt", "hull_semseg_v6.pt", "hull_semseg_v5.pt"]

    assert client.post("/api/detect/semantic", json={"image": IMAGE, "model": "hull_semseg_v5.pt"},
                       headers=alice).status_code == 200
    assert client.post("/api/detect/semantic", json={"image": IMAGE}, headers=alice).status_code == 200
    assert used == ["hull_semseg_v5.pt", None]

    res = client.post("/api/detect/semantic", json={"image": IMAGE, "model": "../../other.pt"}, headers=alice)
    assert res.status_code == 400, res.text
    assert used == ["hull_semseg_v5.pt", None]  # nothing queued

"""SAM mask -> YOLO label refinement: the accept/reject gate and label I/O."""
import numpy as np

from ml import sam_refine

W, H = 200, 100


def _mask(x1, y1, x2, y2):
    mask = np.zeros((H, W), np.uint8)
    mask[y1:y2, x1:x2] = 1
    return mask


def test_box_tightens_to_mask_extent():
    box, refined = sam_refine.refine_box(_mask(30, 20, 90, 70), (20, 10, 100, 80))
    assert refined
    assert box == (30.0, 20.0, 90.0, 70.0)


def test_mask_far_from_prompt_keeps_original_box():
    prompt = (20, 10, 60, 40)
    box, refined = sam_refine.refine_box(_mask(120, 50, 190, 95), prompt)
    assert not refined
    assert box == prompt


def test_tiny_speck_is_rejected():
    prompt = (20, 10, 100, 80)
    _, refined = sam_refine.refine_box(_mask(50, 40, 53, 43), prompt)
    assert not refined


def test_stray_blob_is_dropped_before_measuring():
    mask = _mask(30, 20, 90, 70)
    mask[2:4, 190:192] = 1  # distant noise would otherwise stretch the box
    box, refined = sam_refine.refine_box(mask, (20, 10, 100, 80))
    assert refined
    assert box == (30.0, 20.0, 90.0, 70.0)


def test_polygon_follows_mask_and_falls_back_to_corners():
    points, refined = sam_refine.refine_polygon(_mask(30, 20, 90, 70), (20, 10, 100, 80))
    assert refined
    assert len(points) >= 3
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    assert (min(xs), min(ys)) == (30.0, 20.0)

    prompt = (20, 10, 60, 40)
    points, refined = sam_refine.refine_polygon(_mask(120, 50, 190, 95), prompt)
    assert not refined
    assert points == [(20, 10), (60, 10), (60, 40), (20, 40)]


def test_parse_box_and_polygon_lines():
    cls, box, kind = sam_refine.parse_label_line("2 0.5 0.5 0.2 0.4", W, H)
    assert (cls, kind) == (2, "box")
    assert box == (80.0, 30.0, 120.0, 70.0)

    cls, box, kind = sam_refine.parse_label_line("1 0.1 0.2 0.5 0.2 0.5 0.8", W, H)
    assert (cls, kind) == (1, "polygon")
    assert box == (20.0, 20.0, 100.0, 80.0)

    assert sam_refine.parse_label_line("", W, H) is None
    assert sam_refine.parse_label_line("1 0.1 0.2 0.3", W, H) is None


def test_label_lines_round_trip_and_clamp():
    line = sam_refine.format_box_line(2, (80, 30, 120, 70), W, H)
    assert sam_refine.parse_label_line(line, W, H)[1] == (80.0, 30.0, 120.0, 70.0)

    poly = sam_refine.format_polygon_line(1, [(-5, 10), (210, 10), (100, 120)], W, H)
    values = [float(v) for v in poly.split()[1:]]
    assert all(0.0 <= v <= 1.0 for v in values)


def test_refine_endpoint_queues_a_job(client, alice, monkeypatch):
    """The endpoint queues a job that runs refine_boxes and returns its result."""
    import config
    from api.routers import detect

    monkeypatch.setattr(config, "AI_FEATURES_ENABLED", True)
    stub = {"boxes": [{"bbox": [30.0, 20.0, 60.0, 50.0], "refined": True}]}
    seen = {}

    def fake(image, boxes, sam_model=None):
        seen["boxes"] = boxes
        return stub

    monkeypatch.setattr(detect, "refine_boxes", fake)
    res = client.post("/api/detect/refine-boxes",
                      json={"image": "data:image/png;base64,iVBORw0KGgo=", "boxes": [[20, 10, 80, 70]]},
                      headers=alice)
    assert res.status_code == 200, res.text
    status = client.get(f"/api/detect/status/{res.json()['job_id']}", headers=alice).json()
    assert status == {"status": "completed", "result": stub}
    assert seen["boxes"] == [[20, 10, 80, 70]]


def test_refine_endpoint_validates_boxes_and_auth(client, alice, monkeypatch):
    import config

    monkeypatch.setattr(config, "AI_FEATURES_ENABLED", True)
    image = "data:image/png;base64,iVBORw0KGgo="
    assert client.post("/api/detect/refine-boxes", json={"image": image, "boxes": [[1, 2, 3]]},
                       headers=alice).status_code == 422
    assert client.post("/api/detect/refine-boxes", json={"image": image, "boxes": [[1, 2, 0, 5]]},
                       headers=alice).status_code == 422
    assert client.post("/api/detect/refine-boxes", json={"image": image, "boxes": []},
                       headers=alice).status_code == 422
    assert client.post("/api/detect/refine-boxes", json={"image": image, "boxes": [[1, 2, 3, 4]]}
                       ).status_code in (401, 403)


def test_refine_endpoint_refused_when_ai_disabled(client, alice, monkeypatch):
    import config

    monkeypatch.setattr(config, "AI_FEATURES_ENABLED", False)
    res = client.post("/api/detect/refine-boxes",
                      json={"image": "data:image/png;base64,iVBORw0KGgo=", "boxes": [[1, 2, 3, 4]]},
                      headers=alice)
    assert res.status_code == 503

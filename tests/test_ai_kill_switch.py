"""AI_FEATURES_ENABLED gates the AI job endpoints.

The switch exists so a deployment can turn off auto-detect / auto-tag /
magic-wand without a code change when the ML side is misbehaving, while
manual annotation keeps working. See app/config.py.
"""
import config


def _set_ai(monkeypatch, enabled):
    # detect.py reads config.AI_FEATURES_ENABLED at call time (not import
    # time) precisely so this is patchable and so the flag is read fresh.
    monkeypatch.setattr(config, "AI_FEATURES_ENABLED", enabled)


def test_availability_reports_enabled(client, alice, monkeypatch):
    _set_ai(monkeypatch, True)
    res = client.get("/api/detect/availability", headers=alice)
    assert res.status_code == 200, res.text
    assert res.json() == {"enabled": True}


def test_availability_reports_disabled(client, alice, monkeypatch):
    _set_ai(monkeypatch, False)
    res = client.get("/api/detect/availability", headers=alice)
    assert res.status_code == 200, res.text
    assert res.json() == {"enabled": False}


def test_ai_endpoints_refused_when_disabled(client, alice, monkeypatch):
    """All four job endpoints refuse with 503 rather than queueing work.

    Payloads must be schema-valid: the gate lives in the handler, so request
    validation runs first and an incomplete body would 422 before the switch
    is ever consulted.
    """
    _set_ai(monkeypatch, False)
    image = "data:image/png;base64,iVBORw0KGgo="
    for path, payload in [
        ("/api/detect", {"image": image}),
        ("/api/detect/classify", {"image": image}),
        ("/api/detect/segment", {"image": image, "points": [{"x": 0, "y": 0}], "labels": [1]}),
        ("/api/detect/embed", {"image": image}),
    ]:
        res = client.post(path, json=payload, headers=alice)
        assert res.status_code == 503, f"{path} returned {res.status_code}: {res.text}"


def test_disabled_switch_creates_no_job(client, alice, monkeypatch):
    """The refusal happens before _create_job, so a refused call leaves no
    orphan AIJob row behind to be polled or cleaned up."""
    import models
    from database import SessionLocal

    db = SessionLocal()
    try:
        before = db.query(models.AIJob).count()
    finally:
        db.close()

    _set_ai(monkeypatch, False)
    res = client.post("/api/detect", json={"image": "data:image/png;base64,iVBORw0KGgo="}, headers=alice)
    assert res.status_code == 503

    db = SessionLocal()
    try:
        after = db.query(models.AIJob).count()
    finally:
        db.close()
    assert after == before


def test_ai_endpoints_reachable_when_enabled(client, alice, monkeypatch):
    """With the switch on, the gate is transparent — the request gets past it.

    Asserts only that it is *not* the kill switch talking; the payload here is
    a 1x1 stub, so whatever the ML stack decides afterwards is out of scope.
    """
    _set_ai(monkeypatch, True)
    res = client.post("/api/detect", json={"image": "data:image/png;base64,iVBORw0KGgo="}, headers=alice)
    assert res.status_code != 503, res.text

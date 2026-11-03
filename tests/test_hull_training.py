"""The Hull "Train" button: admin gating, status reporting, AI pause while training.

The training process itself is never started here; api.hull_training.start is
replaced, and the status files are redirected to a temp folder.
"""
import json
import sys
from pathlib import Path

import pytest

import config
from api import hull_training

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import train_hull_from_app as pipeline  # noqa: E402


@pytest.fixture(autouse=True)
def runs_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(hull_training, "RUNS_DIR", tmp_path)
    return tmp_path


def _make_admin(client, headers, monkeypatch):
    username = client.get("/api/auth/me", headers=headers).json()["username"]
    monkeypatch.setattr(config, "ADMIN_USERNAMES", frozenset({username.lower()}))


def test_status_is_idle_without_a_run(client, alice):
    res = client.get("/api/training/hull", headers=alice)
    assert res.status_code == 200, res.text
    assert res.json()["state"] == "idle"
    assert res.json()["can_train"] is False


def test_non_admin_cannot_start_or_cancel(client, alice, monkeypatch):
    monkeypatch.setattr(config, "ADMIN_USERNAMES", frozenset())
    assert client.post("/api/training/hull", headers=alice).status_code == 403
    assert client.delete("/api/training/hull", headers=alice).status_code == 403


def test_start_refused_when_hull_model_is_off(client, alice, monkeypatch):
    _make_admin(client, alice, monkeypatch)
    monkeypatch.setattr(config, "SEMSEG_ENABLED", False)
    assert client.post("/api/training/hull", headers=alice).status_code == 503


def test_admin_starts_and_second_start_conflicts(client, alice, monkeypatch):
    _make_admin(client, alice, monkeypatch)
    monkeypatch.setattr("api.routers.training.semseg_available", lambda: True)
    calls = []

    def fake_start():
        if calls:
            raise hull_training.TrainingBusy("A training run is already in progress.")
        calls.append(1)
        return {"state": "running"}

    monkeypatch.setattr(hull_training, "start", fake_start)
    first = client.post("/api/training/hull", headers=alice)
    assert first.status_code == 202, first.text
    assert first.json()["can_train"] is True
    assert client.post("/api/training/hull", headers=alice).status_code == 409


def test_ai_jobs_pause_while_training(client, alice, monkeypatch):
    monkeypatch.setattr(config, "AI_FEATURES_ENABLED", True)
    monkeypatch.setattr(hull_training, "is_training", lambda: True)
    res = client.post("/api/detect", json={"image": "data:image/png;base64,iVBORw0KGgo="}, headers=alice)
    assert res.status_code == 503
    assert "trained" in res.json()["detail"]


def test_vanished_process_reports_failed(runs_dir):
    (runs_dir / "status.json").write_text(json.dumps({"state": "running", "stage": "training", "name": "x"}))
    assert hull_training.status()["state"] == "failed"


def test_finished_status_is_passed_through(runs_dir):
    (runs_dir / "status.json").write_text(json.dumps({"state": "done", "name": "x", "best": 0.4}))
    assert hull_training.status()["state"] == "done"


def test_trainer_output_is_parsed():
    match = pipeline.EPOCH_LINE.match("epoch 12/150 loss 0.512 defect-mIoU 0.341 * | any-defect IoU 0.5 P 0.7 R 0.6 | gpu 1.9G | 142s")
    assert match and match.groups()[:3] == ("12", "150", "0.341") and match.group(4)
    assert pipeline.SPLIT_LINE.match("train: 514 images | pixel share % sound=86.9").group(2) == "514"


def test_install_is_whole_or_not_at_all(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    monkeypatch.setattr(pipeline, "DETECTOR_MODEL_DIR", str(tmp_path))
    bad = tmp_path / "bad.pt"
    torch.save({"model": {}}, bad)
    with pytest.raises(RuntimeError):
        pipeline.install(bad, "run1")
    assert not list(tmp_path.glob("hull_semseg*"))

    good = tmp_path / "good.pt"
    torch.save({"model": {}, "classes": [], "name": "run1"}, good)
    assert pipeline.install(good, "run1").name == "hull_semseg_run1.pt"
    assert not list(tmp_path.glob("*.part"))

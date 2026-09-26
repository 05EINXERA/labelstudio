"""Network telemetry: config, the X-Server-Ms header, and the ingest endpoint.

Temporary feature (.devnotes/frontend-telemetry/). Its contract is that it is
invisible when off and harmless when on, so both halves are pinned here.
"""
import importlib
import os

import pytest


def _reload_config(monkeypatch, **env):
    import config
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(config)


@pytest.fixture
def restore_config(monkeypatch):
    yield
    import config
    for key in list(os.environ):
        if key.startswith("TELEMETRY_"):
            monkeypatch.delenv(key, raising=False)
    importlib.reload(config)


# --- config ------------------------------------------------------------------

def test_telemetry_is_off_by_default(monkeypatch, restore_config):
    for key in list(os.environ):
        if key.startswith("TELEMETRY_"):
            monkeypatch.delenv(key, raising=False)
    config = _reload_config(monkeypatch)
    assert config.TELEMETRY_ENABLED is False
    assert config.TELEMETRY_PROBES_ENABLED is False
    assert config.TELEMETRY_FLUSH_SECONDS == 300
    assert config.TELEMETRY_ONLY_IPS == []
    assert config.TELEMETRY_DIR == os.path.join(config.LOG_DIR, "telemetry")


def test_telemetry_settings_parse(monkeypatch, restore_config):
    config = _reload_config(
        monkeypatch,
        TELEMETRY_ENABLED="1",
        TELEMETRY_PROBES_ENABLED="true",
        TELEMETRY_FLUSH_SECONDS="30",
        TELEMETRY_MAX_RECORDS="7",
        TELEMETRY_ONLY_IPS="10.0.0.5, 10.0.0.6",
        TELEMETRY_DIR="X:/tele",
    )
    assert config.TELEMETRY_ENABLED is True
    assert config.TELEMETRY_PROBES_ENABLED is True
    assert config.TELEMETRY_FLUSH_SECONDS == 30
    assert config.TELEMETRY_MAX_RECORDS == 7
    assert config.TELEMETRY_ONLY_IPS == ["10.0.0.5", "10.0.0.6"]
    assert config.TELEMETRY_DIR == "X:/tele"

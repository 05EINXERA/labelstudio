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


# --- X-Server-Ms -------------------------------------------------------------

@pytest.fixture
def telemetry_on(monkeypatch, tmp_path):
    import config
    monkeypatch.setattr(config, "TELEMETRY_ENABLED", True)
    monkeypatch.setattr(config, "TELEMETRY_DIR", str(tmp_path / "telemetry"))
    monkeypatch.setattr(config, "TELEMETRY_ONLY_IPS", [])
    return tmp_path / "telemetry"


def test_server_ms_header_on_api_when_enabled(client, alice, telemetry_on):
    res = client.get("/api/auth/me", headers=alice)
    assert res.status_code == 200
    assert float(res.headers["x-server-ms"]) >= 0


def test_server_ms_header_absent_when_disabled(client, alice, monkeypatch):
    import config
    monkeypatch.setattr(config, "TELEMETRY_ENABLED", False)
    res = client.get("/api/auth/me", headers=alice)
    assert res.status_code == 200
    assert "x-server-ms" not in res.headers


def test_server_ms_header_not_on_static_or_health(client, telemetry_on):
    assert "x-server-ms" not in client.get("/health").headers
    assert "x-server-ms" not in client.get("/index.html").headers


def test_server_ms_does_not_disturb_gzip(client, alice, telemetry_on):
    res = client.get("/api/auth/me", headers={**alice, "Accept-Encoding": "gzip"})
    assert res.headers.get("content-encoding") == "gzip"
    assert "x-server-ms" in res.headers
    assert res.json()  # body still decodes


# --- ingest endpoint -----------------------------------------------------------

import gzip  # noqa: E402
import json  # noqa: E402

from api.auth import CSRF_COOKIE_NAME, CSRF_HEADER_NAME  # noqa: E402

_seq = iter(range(10_000))


def _cookie_login(client):
    """Cookie auth, as a browser has: the bearer header is CSRF-exempt, so it
    cannot exercise the CSRF half of the contract."""
    res = client.post("/api/auth/register", json={
        "username": f"tele-{next(_seq)}", "password": "pw-12345"})
    assert res.status_code == 200, res.text
    return {CSRF_HEADER_NAME: client.cookies.get(CSRF_COOKIE_NAME)}


def _batch(n=2):
    return {
        "v": 1, "client": "c-1", "seat": "A12", "page": "/app.html",
        "records": [{"t": 1.0 + i, "it": "fetch", "cat": "heartbeat", "dur": 12.5}
                    for i in range(n)],
    }


def _lines(folder):
    files = list(folder.rglob("batches.ndjson"))
    if not files:
        return []
    assert len(files) == 1
    return [json.loads(l) for l in files[0].read_text(encoding="utf-8").splitlines()]


def test_endpoints_404_when_disabled(client, monkeypatch):
    import config
    monkeypatch.setattr(config, "TELEMETRY_ENABLED", False)
    csrf = _cookie_login(client)
    assert client.get("/api/telemetry/config").status_code == 404
    assert client.post("/api/telemetry/batch", json=_batch(), headers=csrf).status_code == 404


def test_requires_auth(client, telemetry_on):
    client.cookies.clear()
    assert client.get("/api/telemetry/config").status_code == 401
    assert client.post("/api/telemetry/batch", json=_batch()).status_code == 401


def test_post_requires_csrf(client, telemetry_on):
    _cookie_login(client)
    res = client.post("/api/telemetry/batch", json=_batch())
    assert res.status_code == 403
    assert _lines(telemetry_on) == []


def test_config_when_enabled(client, alice, telemetry_on, monkeypatch):
    import config
    monkeypatch.setattr(config, "TELEMETRY_FLUSH_SECONDS", 42)
    body = client.get("/api/telemetry/config", headers=alice).json()
    assert body["enabled"] is True
    assert body["flush_seconds"] == 42
    assert body["probes"] is False and body["probe_bytes"] == 0
    assert body["server_now"] > 1_700_000_000_000


def test_batch_is_appended_as_one_stamped_line(client, telemetry_on):
    csrf = _cookie_login(client)
    for _ in range(2):
        res = client.post("/api/telemetry/batch", json=_batch(3), headers=csrf)
        assert res.status_code == 204, res.text
    lines = _lines(telemetry_on)
    assert len(lines) == 2
    first = lines[0]
    assert first["ip"] == "testclient"
    assert first["user"].startswith("tele-")
    assert first["received_at"].endswith("+00:00")
    assert first["batch"]["seat"] == "A12"
    assert len(first["batch"]["records"]) == 3


def test_gzipped_batch_is_accepted(client, telemetry_on):
    csrf = _cookie_login(client)
    body = gzip.compress(json.dumps(_batch(5)).encode())
    res = client.post("/api/telemetry/batch", content=body, headers={
        **csrf, "Content-Encoding": "gzip", "Content-Type": "application/json"})
    assert res.status_code == 204, res.text
    assert len(_lines(telemetry_on)[0]["batch"]["records"]) == 5


def test_beacon_form_with_query_csrf_is_accepted(client, telemetry_on):
    """sendBeacon cannot set headers: the token rides in the query string."""
    csrf = _cookie_login(client)[CSRF_HEADER_NAME]
    res = client.post(f"/api/telemetry/batch?csrf_token={csrf}",
                      content=json.dumps(_batch()), headers={"Content-Type": "text/plain"})
    assert res.status_code == 204, res.text
    assert len(_lines(telemetry_on)) == 1


def test_oversized_and_malformed_batches_are_refused(client, telemetry_on, monkeypatch):
    import config
    csrf = _cookie_login(client)
    monkeypatch.setattr(config, "TELEMETRY_MAX_RECORDS", 2)
    assert client.post("/api/telemetry/batch", json=_batch(3), headers=csrf).status_code == 413
    monkeypatch.setattr(config, "TELEMETRY_MAX_RECORDS", 5000)
    monkeypatch.setattr(config, "TELEMETRY_MAX_BATCH_BYTES", 50)
    assert client.post("/api/telemetry/batch", json=_batch(3), headers=csrf).status_code == 413
    monkeypatch.setattr(config, "TELEMETRY_MAX_BATCH_BYTES", 1024 * 1024)
    bad = client.post("/api/telemetry/batch", content=b"{not json",
                      headers={**csrf, "Content-Type": "application/json"})
    assert bad.status_code == 400
    assert client.post("/api/telemetry/batch", json={"records": "x"}, headers=csrf).status_code == 400
    assert _lines(telemetry_on) == []


def test_write_failure_is_logged_and_answered_204(client, telemetry_on, monkeypatch, caplog):
    from api.routers import telemetry as router

    def boom(path, line):
        raise OSError("disk full")

    monkeypatch.setattr(router, "_append", boom)
    csrf = _cookie_login(client)
    with caplog.at_level("WARNING"):
        res = client.post("/api/telemetry/batch", json=_batch(), headers=csrf)
    assert res.status_code == 204
    assert "telemetry write" in caplog.text


def test_only_ips_limits_capture(client, alice, telemetry_on, monkeypatch):
    import config
    monkeypatch.setattr(config, "TELEMETRY_ONLY_IPS", ["10.9.9.9"])
    assert client.get("/api/telemetry/config", headers=alice).status_code == 404
    monkeypatch.setattr(config, "TELEMETRY_ONLY_IPS", ["testclient"])
    assert client.get("/api/telemetry/config", headers=alice).status_code == 200

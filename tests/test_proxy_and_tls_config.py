import os
import re
import pytest
from fastapi import Response

import config
import app.config
from api.auth import issue_session_cookies, CSRF_COOKIE_NAME

REPO_ROOT = os.path.dirname(os.path.dirname(__file__))


def test_caddyfile_configuration():
    """Verify Caddyfile configuration exists and contains required TLS & proxy directives."""
    caddyfile_path = os.path.join(REPO_ROOT, "Caddyfile")
    assert os.path.isfile(caddyfile_path), "Caddyfile not found in repository root."

    with open(caddyfile_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert ":443" in content, "Caddyfile must listen on port 443"
    assert "tls internal" in content, "Caddyfile must specify TLS mode"
    assert "reverse_proxy 127.0.0.1:8000" in content or "reverse_proxy" in content
    assert "max_size 300MB" in content, "Caddyfile should permit 300MB upload sizes for dataset ZIPs"
    assert "header_up X-Forwarded-Proto" in content
    assert "header_up X-Forwarded-For" in content


def test_nginx_configuration():
    """Verify Nginx configuration exists and has SSL, body size, and proxy headers."""
    nginx_path = os.path.join(REPO_ROOT, "deploy", "nginx.conf")
    assert os.path.isfile(nginx_path), "deploy/nginx.conf not found."

    with open(nginx_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "listen 443 ssl" in content, "Nginx must listen on port 443 ssl"
    assert "proxy_pass http://annotation_backend" in content
    assert "client_max_body_size 300M" in content
    assert "proxy_set_header X-Forwarded-Proto $scheme" in content
    assert "proxy_set_header X-Forwarded-For" in content


def test_config_exports_proxy_settings():
    """Verify config and app.config export FORWARDED_ALLOW_IPS and PROXY_HEADERS."""
    assert hasattr(config, "FORWARDED_ALLOW_IPS")
    assert hasattr(config, "PROXY_HEADERS")
    assert hasattr(app.config, "FORWARDED_ALLOW_IPS")
    assert hasattr(app.config, "PROXY_HEADERS")

    assert "FORWARDED_ALLOW_IPS" in config.__all__
    assert "PROXY_HEADERS" in config.__all__


def test_cookie_secure_attributes(monkeypatch):
    """Verify that issue_session_cookies respects COOKIE_SECURE setting."""
    import api.auth as auth_mod

    # Test with COOKIE_SECURE = True
    monkeypatch.setattr(auth_mod, "COOKIE_SECURE", True)
    res_secure = Response()
    csrf_token = issue_session_cookies(res_secure, access_token="test-token-123")
    assert csrf_token is not None

    cookie_headers = res_secure.headers.getlist("set-cookie")
    assert any("access_token=" in h and "Secure" in h for h in cookie_headers), "access_token must have Secure flag"
    assert any(f"{CSRF_COOKIE_NAME}=" in h and "Secure" in h for h in cookie_headers), "csrf_token must have Secure flag"

    # Test with COOKIE_SECURE = False
    monkeypatch.setattr(auth_mod, "COOKIE_SECURE", False)
    res_insecure = Response()
    issue_session_cookies(res_insecure, access_token="test-token-456")

    insecure_headers = res_insecure.headers.getlist("set-cookie")
    assert any("access_token=" in h and "Secure" not in h for h in insecure_headers), "access_token must not have Secure flag"
    assert any(f"{CSRF_COOKIE_NAME}=" in h and "Secure" not in h for h in insecure_headers), "csrf_token must not have Secure flag"

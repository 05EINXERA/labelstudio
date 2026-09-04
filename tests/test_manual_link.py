"""The Help dialog's manual link.

The manual is served by a different box on the LAN, so the address is config
(MANUAL_URL) rather than a hardcoded href in app.html — if the manual moves,
only .env changes.
"""
import re
from pathlib import Path

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


def test_auth_config_exposes_manual_url(client):
    """The endpoint is unauthenticated, so the link works before login too."""
    res = client.get("/api/auth/config")
    assert res.status_code == 200
    body = res.json()
    assert "manual_url" in body
    assert body["manual_url"].startswith("http")


def test_help_modal_has_a_hidden_manual_link():
    html = (FRONTEND / "app.html").read_text(encoding="utf-8")
    anchor = re.search(r'<a id="manualLink".*?>', html, re.S)
    assert anchor, "manualLink anchor missing from the Help modal"
    tag = anchor.group(0)
    # Starts hidden and is revealed only once a URL is fetched, so a failed
    # lookup leaves no dead link behind.
    assert "hidden" in tag
    # Opens in a new tab without handing the manual host window.opener.
    assert 'target="_blank"' in tag
    assert "noopener" in tag


def test_modals_module_fills_in_the_manual_href():
    js = (FRONTEND / "js" / "components" / "modals.js").read_text(encoding="utf-8")
    assert "/api/auth/config" in js
    assert "manual_url" in js
    # The address must not be baked into the frontend.
    assert "192.168" not in js

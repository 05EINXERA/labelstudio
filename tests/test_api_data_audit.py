"""T3.1 — /api/data contract under a shared account.

Under a single shared login every client shares one owner_id, so anything
stored in /api/data is last-writer-wins across all annotators.  This file
documents and pins what IS and IS NOT allowed to live there.

Findings from the T3.1 audit:
- sync.js previously synced 'image-annotation-mvp-v1' (the legacy global
  annotation blob) to /api/data.  That key was already being migrated away
  from in workspace.js (loadSaved → removeItem).  The sync.js <script> tag
  has been removed from app.html; no per-annotator UI state is written to
  /api/data by any current code path.
- /api/data is now effectively unused by the frontend (no writes remain).
  The endpoint is retained so that a future legitimate project-global
  setting can use it without a new API surface.

Allowed in /api/data:
  - Genuinely project-global settings (e.g. a shared canvas preference set
    by the operator that should be the same for every client).

Not allowed in /api/data:
  - Per-annotator UI state (panel widths, zoom, last-opened task, username).
    These belong in localStorage, which is per-browser and never shared.
"""
from conftest import _register


def test_data_endpoint_is_per_user_scoped(client):
    """The server row is keyed by owner_id, so two distinct accounts see
    independent values — correct for any future multi-account deployment."""
    alice = _register(client, "data-audit-alice")
    bob   = _register(client, "data-audit-bob")

    client.post("/api/data", json={"key": "setting", "value": "a"}, headers=alice)
    client.post("/api/data", json={"key": "setting", "value": "b"}, headers=bob)

    assert client.get("/api/data", headers=alice).json()["setting"] == "a"
    assert client.get("/api/data", headers=bob).json()["setting"] == "b"


def test_data_endpoint_shared_account_is_last_writer_wins(client):
    """With a shared login (one account for all annotators) both writes go to
    the same row — this is the known trade-off documented in T3.1/L5.

    The test pins the behaviour so it is a conscious decision, not a surprise:
    if this test starts failing it means either the schema changed in a way
    that breaks the shared-account model, or the trade-off was revisited.
    """
    # Simulate two 'annotators' authenticated as the same user by reusing one
    # token (the shared-account scenario).
    shared = _register(client, "data-audit-shared")

    client.post("/api/data", json={"key": "ui_pref", "value": "first"}, headers=shared)
    client.post("/api/data", json={"key": "ui_pref", "value": "second"}, headers=shared)

    # Second write wins — expected and accepted under the shared-account model.
    assert client.get("/api/data", headers=shared).json()["ui_pref"] == "second"


def test_no_annotator_specific_keys_remain(client):
    """/api/data must start empty for a new user — no leaked cross-user state."""
    user = _register(client, "data-audit-empty")
    assert client.get("/api/data", headers=user).json() == {}

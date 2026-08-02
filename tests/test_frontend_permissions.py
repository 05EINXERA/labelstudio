"""Runs the client-side permission spec under node, from pytest.

Same arrangement as `test_offline_queue.py`: the frontend has no build step and
no JS test runner, but `frontend/js/permissions.js` is a hand-maintained mirror
of `api/permissions.py` (there is no build step to share one definition), and a
drifted copy renders controls that every click 403s on.

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "permissions_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_frontend_permissions_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"permissions spec failed:\n{result.stdout}\n{result.stderr}"
    )
    assert "failed" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_client_ranking_matches_the_server():
    """The two copies of the role ranking agree.

    The spec above asserts the client's ranking against a literal. This asserts
    the *server's* against the same shape, so the pair cannot drift by both
    being edited to a new but different order.
    """
    from api.permissions import ProjectRole, TeamRole, _RANK, _TEAM_RANK

    assert {r.value: v for r, v in _RANK.items()} == {
        "viewer": 0, "annotator": 1, "reviewer": 2, "manager": 3, "owner": 4,
    }
    assert {r.value: v for r, v in _TEAM_RANK.items()} == {
        "member": 0, "manager": 1, "owner": 2,
    }
    # And the vocabularies themselves, so adding a role server-side without
    # updating frontend/js/permissions.js fails here rather than in a browser.
    assert [r.value for r in ProjectRole] == [
        "viewer", "annotator", "reviewer", "manager", "owner",
    ]
    assert [r.value for r in TeamRole] == ["member", "manager", "owner"]

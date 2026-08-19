"""Runs the Home dashboard tile spec under node, from pytest.

Same arrangement as `test_frontend_objects_filter.py`: the frontend has no build
step and no JS test runner, so the spec is a plain node script invoked here.

`statusTiles` in `frontend/js/pages/project/home.js` derives one dashboard tile
per task status from the shared vocabulary. The spec's most important assertion
is that the approved group is *split* — Approved, Verified, Checked and Passed
each get their own count — because those names exist precisely to separate one
export batch from another (CLAUDE.md rule 11a), and folding them into a single
"Approved" tile threw that away. It also pins the click-to-filter links, so a
tile can never open the tasks view on a different set than it counted.

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "dashboard_tiles_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_dashboard_tiles_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"{SPEC.name} failed:\n{result.stdout}\n{result.stderr}"
    )
    assert "0 failed" in result.stdout

"""Runs the export status poller spec under node, from pytest.

Same arrangement as `test_frontend_opacity_slider.py`: the frontend has no
build step and no JS test runner, so the spec is a plain node script invoked
here.

`frontend/js/pages/project/export-poll.js` keeps at most one status poll in
flight, backs off to 5 s and pauses in a hidden tab. It replaced a 1 s
`setInterval` that stacked eleven overlapping polls on a starved server
(.devnotes/fix-exports-imports/02_ISSUES.md I-5).

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "export_poll_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_export_poll_spec():
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

"""Runs the Vertex pill spec (tests/js/vertex_controls_spec.mjs) under node.

The pill must show what is on screen (Off for a sticky or held V), keep
aria-pressed in step, and hand focus back after a click so V/H/Delete still
reach the canvas. See .devnotes/feat/hide-vertex/01_DESIGN.md D8.

Skips rather than fails when node is unavailable.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "vertex_controls_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_vertex_controls_spec():
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

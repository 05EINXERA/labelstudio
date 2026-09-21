"""Runs the vertex-handle sizing spec under node, from pytest.

Same arrangement as `test_frontend_objects_filter.py`: the frontend has no
build step and no JS test runner, so the spec is a plain node script invoked
here.

`frontend/js/canvas/handle-size.js` decides how big a vertex handle is drawn at
a given zoom. The spec's load-bearing assertions are the two clamps: the floor
keeps a handle from shrinking to sub-pixel at high zoom (visible vertices are
the whole point of handles), and the ceiling keeps one from being drawn wider
than `vertexGrabRadius`, which would make its rim visible but unclickable.
See .devnotes/fix-zoom-vertex/01_PLAN.md § 3.3.

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "handle_size_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_handle_size_spec():
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

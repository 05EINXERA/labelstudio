"""Runs the Opacity slider conversion spec under node, from pytest.

Same arrangement as `test_frontend_handle_size.py`: the frontend has no build
step and no JS test runner, so the spec is a plain node script invoked here.

`frontend/js/opacity-scale.js` converts the toolbar slider's 0-100 percentage
to the 0-1 alpha draw.js feeds `hexToRgba()`. The spec's load-bearing
assertions are the drift guard between the default in `feature-flags.js` and
the hardcoded fallback in `app.html`, and that no degenerate input yields NaN
-- a NaN alpha makes `hexToRgba()` emit an invalid colour string, which canvas
silently ignores, so every selected shape would lose its fill with nothing
logged. See .devnotes/opacity-slider-feature/01_PLAN.md § 5.1.

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "opacity_slider_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_opacity_slider_spec():
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

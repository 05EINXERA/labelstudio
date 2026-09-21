"""Runs the shortcut typing-guard spec under node, from pytest.

Same arrangement as `test_frontend_handle_size.py`: the frontend has no build
step and no JS test runner, so the spec is a plain node script invoked here.

`frontend/js/typing-target.js` decides whether a keydown landed in a text entry
control, in which case the canvas shortcut handler leaves it alone. The rule
was `instanceof HTMLInputElement`, which treated a range slider as a text
field: clicking the opacity slider left it focused and silently killed H,
the class digits, Delete and Ctrl+Z while the shape still looked selected.

The spec's two load-bearing properties are that a slider does NOT block
shortcuts, and that every real text entry control still does -- overshooting
would fire canvas actions on every letter typed into a field, which is worse
than the bug being fixed.

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "typing_target_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_typing_target_spec():
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

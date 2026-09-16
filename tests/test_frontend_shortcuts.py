"""Runs the canvas keyboard-shortcut spec under node, from pytest.

Same arrangement as `test_frontend_marquee.py`: the frontend has no build step
and no JS test runner, so the spec is a plain node script invoked here.

`frontend/js/shortcuts.js` holds the decisions behind the canvas key bindings —
which class a digit key selects, which annotations "H" targets, and whether an
"H" event is a tap, the start of a hold, or a release. That last one is the
flicker guard: a held key delivers a stream of auto-repeat keydowns, and the
binding used to toggle on every one of them, inverting the hide ~30 times a
second (.devnotes/new-hide-interactions/01_ANALYSIS.md § 4.2). The spec drives a
full press/hold/release sequence through the decision and asserts it collapses
to exactly one toggle and one peek.

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "shortcuts_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_shortcuts_spec():
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

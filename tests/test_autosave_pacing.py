"""Runs the autosave-pacing spec under node, from pytest.

Same arrangement as `test_save_coalesce.py`: the frontend has no build step and
no JS test runner, so the spec runs under plain node and this wrapper puts it in
`pytest tests/`. Skips rather than fails when node is unavailable.

What it guards: the pacer may only ever delay an automatic save, by a bounded
amount (never past the 10 s ceiling), must be invisible on a healthy server, and
must compose with the in-flight guard so the last save still carries the newest
state. See .devnotes/fix-performance-upgrade/02_PLAN.md (Phase 2).
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "autosave_pacing_spec.mjs"
WIRING = Path(__file__).parent / "js" / "autosave_wiring_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_autosave_pacing_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"{SPEC.name} failed:\n{result.stdout}\n{result.stderr}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_autosave_wiring_spec():
    """A tripwire on how pacing, the draft overflow and serialise-once are wired
    into workspace.js / init.js, which cannot be imported without a DOM."""
    assert WIRING.exists(), f"missing spec: {WIRING}"
    result = subprocess.run(
        [shutil.which("node"), str(WIRING)], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, (
        WIRING.name + " failed: " + result.stdout + " " + result.stderr
    )

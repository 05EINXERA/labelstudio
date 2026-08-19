"""Runs the tasks view reset-vs-restore spec under node, from pytest.

Same arrangement as `test_tasks_view_state.py`: the frontend has no build step
and no JS test runner, so the behaviour spec is a plain node script and this
shim surfaces it to pytest.

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "tasks_view_restore_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_tasks_view_restore_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"spec failed:\n{result.stdout}\n{result.stderr}"
    assert "0 failed" in result.stdout

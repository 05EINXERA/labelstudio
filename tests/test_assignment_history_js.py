"""Runs the assignment-history formatter spec under node, from pytest.

Same arrangement as test_wipe_guard_js.py. Skips when node is unavailable.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "assignment_history_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_assignment_history_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"spec failed:\n{result.stdout}\n{result.stderr}"
    assert "0 failed" in result.stdout

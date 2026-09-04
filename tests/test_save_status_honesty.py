"""Runs the save-indicator honesty JS spec under node, from pytest.

Guards .devnotes/network-lag/03_FALSE_SAVED_STATUS.md B1 and B3: the two ways
the canvas came to report "Saved" while the server still held a 30-minute-old
version of the task. Both were state-machine bugs in the offline queue rather
than transport failures, which is exactly the kind of regression a spec catches
and a manual check does not.

Same shape as test_offline_queue.py — shelling out to node keeps the spec inside
pytest (CLAUDE.md rule 21) without adding a JS toolchain, and skips rather than
fails where node is unavailable.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "save_status_honesty_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_save_status_honesty_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"save-status honesty spec failed:\n{result.stdout}\n{result.stderr}"
    )
    assert "failed" in result.stdout

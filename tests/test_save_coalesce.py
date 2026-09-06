"""Runs the in-flight save guard spec under node, from pytest.

Same arrangement as `test_frontend_permissions.py`: the frontend has no build
step and no JS test runner, so the spec runs under plain node and this wrapper
puts it in `pytest tests/`.

What it guards is a data-safety property, not just an optimisation. The guard
folds an automatic save into one already in flight, which is only safe because
the payload is absolute — every save carries the complete annotation set. The
spec pins the cases where folding must NOT happen (beacons, user-initiated
saves, explicit status changes) and that the guard always clears, including on
failure; a latched guard would silently stop autosaving for the rest of the
session. See .devnotes/fix-save-coalesce/01_PLAN.md.

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "save_coalesce_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_save_coalesce_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"save coalesce spec failed:\n{result.stdout}\n{result.stderr}"
    )

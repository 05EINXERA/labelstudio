"""Runs the offline-outbox JS spec under node, from pytest.

The frontend has no build step and no JS test runner (no package.json), but the
offline queue's logic is too easy to break silently to leave untested — its
delta-accumulation rule decides whether logged time is lost or double-counted.
Shelling out to node keeps the spec inside `pytest` (CLAUDE.md rule 21) without
adding a toolchain.

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "offline_queue_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_offline_queue_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    # The spec prints one line per assertion and exits non-zero on any failure;
    # surface its whole output so a failure is diagnosable from the pytest report.
    assert result.returncode == 0, (
        f"offline-queue spec failed:\n{result.stdout}\n{result.stderr}"
    )
    assert "failed" in result.stdout

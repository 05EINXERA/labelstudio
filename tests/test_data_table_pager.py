"""Runs the data-table pager spec under node, from pytest.

Same arrangement as `test_frontend_permissions.py`: the frontend has no build
step and no JS test runner, so the behaviour spec is a plain node script and
this shim just surfaces it to `pytest`.

The pager is shared by every table in the app (projects, tasks, classes, teams,
members, grants), so a windowing or clamping regression is not local to one
page — it strands users on an empty page wherever it is rendered.

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "data_table_pager_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_data_table_pager_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"data-table pager spec failed:\n{result.stdout}\n{result.stderr}"
    )
    assert "0 failed" in result.stdout

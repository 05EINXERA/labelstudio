"""Runs the list-view thumbnail spec under node, from pytest.

Same arrangement as the other `test_frontend_*.py` wrappers. The spec's
load-bearing assertion is that no list view (Tasks, Move, the edit preview,
or the error fallback) points at /uploads/: each original is ~10 MB and a
10-row page used to cost ~99 MB per annotator
(.devnotes/frontend-telemetry/07_TASKS_PAGE_THUMBNAILS.md).

Skips rather than fails when node is unavailable.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "thumb_url_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_thumb_url_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"{SPEC.name} failed:\n{result.stdout}\n{result.stderr}"
    assert " 0 failed" in result.stdout

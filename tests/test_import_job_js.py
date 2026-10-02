"""Runs the annotation-import job client spec under node, from pytest.

Same arrangement as the other node-spec wrappers. The spec pins that the
import upload is not bound by apiFetch's 45 s timeout, that a 202 is followed
to the job's real outcome, and that a lost job is never reported as a plain
failure, since an apply may already have committed
(.devnotes/fix-import-timeout/01_PLAN.md).

Skips rather than fails when node is unavailable.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "import_job_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_import_job_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"{SPEC.name} failed:\n{result.stdout}\n{result.stderr}"
    assert " 0 failed" in result.stdout

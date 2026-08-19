"""Runs the no-edit drain spec under node, from pytest.

Same arrangement as `test_frontend_objects_filter.py`: the frontend has no build
step and no JS test runner, so the spec is a plain node script invoked here.

What it guards (.devnotes/unwanted-time-change/01_DIAGNOSIS.md): a reviewer who
opens a task and only pans and zooms must bank no time and move no timestamp.
The gate that achieves this sits in the single drain point for
`timerState.taskSessionSeconds`, which every save on the canvas page flows
through — so the spec's negative assertions (an explicit status still saves, a
supplied annotation set still saves, a 409 still returns its seconds) matter as
much as the positive one. A gate that suppressed too much would lose annotation
work, which is the failure mode CLAUDE.md rule 11 exists to prevent.

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "timer_noop_drain_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_timer_noop_drain_spec():
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

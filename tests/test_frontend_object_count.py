"""Runs the `object_count` payload spec under node, from pytest.

Same arrangement as `test_frontend_timer_noop.py`: no build step, no JS test
runner, so the spec is a plain node script invoked here.

What it guards (.devnotes/logging/02_PLAN.md §5): the save payload carries the
Objects panel's own row count, so the service log can record what the annotator
was looking at when they saved. The field is diagnostic — the server counts the
blob itself — which is exactly why it needs a test: a diagnostic field is the
kind of thing a later refactor drops without anyone noticing until the day
someone needs the log to explain missing work.

The negative assertions matter as much as the positive ones. `object_count`
must not appear on a time-only save (it would describe a different moment than
the write) and must not perturb `annotations` or `allow_clear` — the logging
work exists to explain annotation loss, not to cause any.

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "object_count_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_object_count_spec():
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

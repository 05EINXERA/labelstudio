"""Runs the marquee-selection spec under node, from pytest.

Same arrangement as `test_frontend_objects_filter.py`: the frontend has no
build step and no JS test runner, so the spec is a plain node script invoked
here.

`frontend/js/canvas/marquee.js` decides which annotations a Shift+drag
rectangle selects. The spec's most important assertion is that the rule never
mutates the annotations it is asked about — `syncToBackend()`/`saveDraft()`
serialise `state.annotations`, so a selection gesture that reached them would
let a user drag a box and then save the damage
(.devnotes/drag-selection/01_DESIGN.md § 4.4).

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "marquee_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_marquee_spec():
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

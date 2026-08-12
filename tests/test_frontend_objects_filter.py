"""Runs the Objects panel specs under node, from pytest.

Same arrangement as `test_frontend_permissions.py`: the frontend has no build
step and no JS test runner, so the spec is a plain node script invoked here.

`frontend/js/objects-filter.js` decides which rows the Objects panel lists. The
spec's most important assertion is that filtering never mutates the row list or
the annotations behind it — `syncToBackend()`/`saveDraft()` serialise
`state.annotations`, so a filter that mutated it would let a user who selected
one object save that object over all their other work
(.devnotes/object-selection/01_DESIGN.md § 5).

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPECS = [
    Path(__file__).parent / "js" / "objects_filter_spec.mjs",
    Path(__file__).parent / "js" / "objects_panel_spec.mjs",
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("spec", SPECS, ids=lambda p: p.stem)
def test_objects_panel_specs(spec):
    assert spec.exists(), f"missing spec: {spec}"
    result = subprocess.run(
        [shutil.which("node"), str(spec)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"{spec.name} failed:\n{result.stdout}\n{result.stderr}"
    )
    assert "0 failed" in result.stdout

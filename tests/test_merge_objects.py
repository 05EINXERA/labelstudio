"""Runs the polygon union spec under node, from pytest.

Same arrangement as `test_polygon_untangle.py`: the frontend has no build step
and no JS test runner, so each `tests/js/*.mjs` spec needs a thin pytest wrapper
or it never runs in CI.

Why merge geometry needs a guard of its own, on top of the untangle one: merge
**destroys N annotations to create 1**. A wrong union is not a cosmetic glitch
that the next edit fixes — the inputs are gone, and the only way back is undo,
which is lost the moment the tab closes. The kernel is written to refuse rather
than guess whenever the geometry is ambiguous (touching-only shapes, degenerate
crossings, disjoint selections), and the spec's refusal cases are what keep that
property from being "optimised" away later.

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "merge_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_merge_objects_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"merge spec failed:\n{result.stdout}\n{result.stderr}"
    )
    assert "0 failed" in result.stdout

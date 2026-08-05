"""Runs the polygon untangle spec under node, from pytest.

Same arrangement as `test_frontend_permissions.py` and `test_offline_queue.py`:
the frontend has no build step and no JS test runner, but
`frontend/js/canvas/untangle.js` rewrites geometry an annotator drew by hand,
so a false positive there silently deletes part of someone's label. The spec is
the guard on that.

`formats/common.py`'s `is_simple_polygon` is the server-side mirror of the same
detection logic; the agreement between the two is asserted in
`test_formats_common.py` rather than here.

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "untangle_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_polygon_untangle_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"untangle spec failed:\n{result.stdout}\n{result.stderr}"
    )
    assert "0 failed" in result.stdout

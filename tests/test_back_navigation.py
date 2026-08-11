"""Runs the workspace back-arrow navigation spec under node, from pytest.

Same arrangement as `test_data_table_pager.py`: the frontend has no build step
and no JS test runner, so the behaviour spec is a plain node script and this
shim surfaces it to pytest.

What it protects is a navigation loop: the back arrow used to push a history
entry, so tasks -> canvas -> tasks left the browser Back button pointing at the
canvas, and the annotator could not get out.

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "back_navigation_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_back_navigation_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"spec failed:\n{result.stdout}\n{result.stderr}"
    assert "0 failed" in result.stdout


def test_the_storage_key_agrees_across_both_files():
    """The writer and the reader must name the same key.

    `tasks.js` writes the marker and `init.js` reads it, across a document
    boundary and with no build step to share a constant. A rename in one file
    alone would not fail anything at runtime — the arrow would silently go back
    to pushing history, restoring the loop — so it is asserted here instead.
    """
    root = Path(__file__).parent.parent / "frontend" / "js"
    pattern = re.compile(r'CAME_FROM_TASKS_KEY\s*=\s*"([^"]+)"')

    keys = {}
    for name, path in [
        ("init.js", root / "init.js"),
        ("tasks.js", root / "pages" / "project" / "tasks.js"),
    ]:
        found = pattern.search(path.read_text(encoding="utf-8"))
        assert found, f"CAME_FROM_TASKS_KEY not found in {name}"
        keys[name] = found.group(1)

    assert keys["init.js"] == keys["tasks.js"], (
        f"the back-navigation marker key has drifted: {keys}"
    )

    # And the spec asserts behaviour against the same literal.
    spec = (Path(__file__).parent / "js" / "back_navigation_spec.mjs").read_text(
        encoding="utf-8"
    )
    assert f"'{keys['init.js']}'" in spec, "the spec tests a different key than the app uses"

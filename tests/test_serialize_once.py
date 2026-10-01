"""Runs the serialise-once spec under node, from pytest.

A save used to serialise the open canvas three to four times; callers now build
the string once and pass it on. The spec pins that the string-taking forms of
`annotationsChangedSinceHydration` / `noteHydratedAnnotations` answer exactly as
the array-taking forms did, including the fail-safe direction (anything that
cannot be proven unchanged reads as changed). Skips when node is unavailable.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "serialize_once_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_serialize_once_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"{SPEC.name} failed:\n{result.stdout}\n{result.stderr}"

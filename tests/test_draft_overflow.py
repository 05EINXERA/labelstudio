"""Runs the draft-overflow spec under node, from pytest.

The per-task draft is the safety net for unsaved work (CLAUDE.md rule 18), and
localStorage's ~5 MB quota silently disabled it for large tasks. The overflow
store (IndexedDB) takes over only when the primary write fails. The spec pins
what must never happen: a draft destroyed by the move (rule 18a), a failure
swallowed silently, or a stale copy beating a newer one.

Skips rather than fails when node is unavailable.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "draft_overflow_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_draft_overflow_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"{SPEC.name} failed:\n{result.stdout}\n{result.stderr}"

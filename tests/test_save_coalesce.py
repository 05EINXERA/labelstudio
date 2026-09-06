"""Runs the in-flight save guard specs under node, from pytest.

Same arrangement as `test_frontend_permissions.py`: the frontend has no build
step and no JS test runner, so the specs run under plain node and this wrapper
puts them in `pytest tests/`.

What they guard is a data-safety property, not just an optimisation. The guard
folds an automatic save into one already in flight, which is only safe because
the payload is absolute — every save carries the complete annotation set. See
.devnotes/fix-save-coalesce/01_PLAN.md.

Skips rather than fails when node is unavailable, so a Python-only environment
does not report a spurious failure.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "save_coalesce_spec.mjs"
INTEGRATION_SPEC = Path(__file__).parent / "js" / "save_coalesce_integration_spec.mjs"


def _run(spec: Path) -> None:
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


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_save_coalesce_spec():
    """The guard's own bookkeeping, against a stubbed sender.

    Pins the cases where folding must NOT happen (beacons, user-initiated
    saves, explicit status changes) and that the guard always clears, including
    on failure — a latched guard would silently stop autosaving for the rest of
    the session.
    """
    _run(SPEC)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_save_coalesce_integration_spec():
    """The properties that decide whether an annotator loses work.

    The unit spec above stubs the sender, so it cannot see the bug that
    matters: what the *follow-up* save actually puts on the wire, and what the
    post-save bookkeeping then claims the server holds. A stale follow-up would
    drop every shape drawn during the in-flight save, and a fingerprint that
    over-claims would suppress the next autosave as "nothing to save" — both
    silent, both losing real work.

    This spec models the real wiring in `syncToBackend` and was verified to
    fail when the send-time canvas re-read is removed.
    """
    _run(INTEGRATION_SPEC)

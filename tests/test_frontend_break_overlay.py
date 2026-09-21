"""Runs the break-overlay spec under node, from pytest.

Same arrangement as the other frontend specs: no build step, no JS test
runner, so the spec runs directly and pytest wraps it. Skips rather than fails
when node is unavailable.

The Python test below guards the one thing the JS spec cannot see: that the
server agrees the overlay's contract is real — the break endpoints exist, are
self-scoped, and carry CSRF.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "break_overlay_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_break_overlay_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"break overlay spec failed:\n{result.stdout}\n{result.stderr}"
    )
    assert "failed" in result.stdout


def test_the_break_endpoints_are_state_changing_and_carry_csrf():
    """Rule 1a. The attendance router is otherwise all reads, so these two
    declare require_csrf per-route; a write without it is the gap the rule
    exists to close.
    """
    source = (
        Path(__file__).parent.parent / "api" / "routers" / "attendance.py"
    ).read_text(encoding="utf-8")

    start = source.index('@router.post("/break/start"')
    end = source.index('@router.post("/break/end"')
    open_get = source.index('@router.get("/break/open"')

    assert "require_csrf" in source[start:end], (
        "POST /break/start does not declare require_csrf"
    )
    assert "require_csrf" in source[end:open_get], (
        "POST /break/end does not declare require_csrf"
    )


def test_the_break_endpoints_take_no_user_parameter():
    """Safe by construction, not by a check.

    An endpoint that accepts a user id and validates it is a check that can be
    forgotten; one that cannot express another user cannot forge a break.
    """
    import inspect

    from api.routers import attendance as router

    for handler in (router.start_break, router.end_break, router.open_break):
        params = set(inspect.signature(handler).parameters)
        leaky = {p for p in params if "user" in p.lower()} - {"current_user"}
        assert not leaky, (
            f"{handler.__name__} accepts {leaky}; a break endpoint must not be "
            "able to name another user"
        )

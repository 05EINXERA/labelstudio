"""Runs the manual-break time spec under node, from pytest.

The JS spec covers the site-zone conversion. The Python tests below guard the
two properties that live on the server side of the same contract: the payload
cannot name another user, and the client's backdating window matches the one
the server enforces.
"""
import inspect
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "manual_break_spec.mjs"
ROOT = Path(__file__).parent.parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_manual_break_time_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"manual break spec failed:\n{result.stdout}\n{result.stderr}"
    )
    assert "failed" in result.stdout


def test_the_payload_cannot_name_another_user():
    """Safe by construction (Q16, § 4.1 point 3): a request body with no user
    field cannot be used to write a break against someone else."""
    from schemas import ManualBreakRequest

    fields = set(ManualBreakRequest.model_fields)
    assert fields == {"started_at", "ended_at"}, (
        f"ManualBreakRequest carries {fields}; it must be incapable of naming "
        "a user"
    )


def test_the_endpoint_declares_csrf():
    """Rule 1a: this is a state-changing call on a router that is otherwise
    all reads."""
    from api.routers import attendance

    source = inspect.getsource(attendance.manual_break)
    assert "require_csrf" in source


def test_the_client_window_matches_the_server_window():
    """The date picker's bounds are a convenience; the server's check is the
    rule. They must agree, or the picker offers a day the server refuses."""
    from api.routers.attendance import MANUAL_BREAK_BACKDATE_DAYS

    source = (ROOT / "frontend" / "js" / "pages" / "profile.js").read_text(
        encoding="utf-8"
    )
    # The picker allows today back to today-1.
    assert "shiftDate(todayInSiteZone(), -1)" in source
    assert MANUAL_BREAK_BACKDATE_DAYS == 1, (
        f"the server allows {MANUAL_BREAK_BACKDATE_DAYS} day(s) of backdating "
        "but the picker is pinned to 1; update both"
    )


def test_the_endpoint_writes_a_new_pair_rather_than_updating():
    """Q24. The whole feature rests on this table being append-only in
    application code."""
    from api.routers import attendance

    source = inspect.getsource(attendance.manual_break)
    assert "db.add_all" in source
    for mutation in (".update(", "db.delete(", ".delete("):
        assert mutation not in source, (
            f"manual_break contains {mutation}; a manual break must be a new "
            "row pair, never an edit"
        )


def test_a_failed_reroll_is_guarded_rather_than_raised():
    """The observations are committed before the re-roll. Raising would tell
    the user their break was not saved when it was."""
    from api.routers import attendance

    source = inspect.getsource(attendance._reroll)
    assert "except Exception" in source
    assert "logger.warning" in source, (
        "the re-roll swallows a failure without logging it; a net that can "
        "drop what it is catching is worse than no net"
    )

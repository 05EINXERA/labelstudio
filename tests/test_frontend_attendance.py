"""Runs the attendance dashboard's frontend specs under node, from pytest.

Same arrangement as `test_frontend_permissions.py`: the frontend has no build
step and no JS test runner, so the specs run directly and pytest wraps them so
`pytest tests/` covers them. Skips rather than fails when node is unavailable.

Two specs are wrapped:

- `attendance_format_spec.mjs` — the cell formatting, and in particular the
  three labelling rules the design calls load-bearing (a timeout is never shown
  as a logout, a manual break is marked, an unended break is not dropped).
- `app_nav_spec.mjs` — already wrapped by `test_frontend_app_nav.py` if that
  exists; the admin-tab assertions live there because that is where the nav is
  tested.

The Python test below guards the one thing the JS spec structurally cannot: the
column labels the *server* can support. "Tasks completed" is not derivable —
there is no author column on the annotation write path — so a column that
implied it would be presenting a guess as data.
"""
import shutil
import subprocess
from pathlib import Path

import pytest

SPEC = Path(__file__).parent / "js" / "attendance_format_spec.mjs"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_attendance_format_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"attendance format spec failed:\n{result.stdout}\n{result.stderr}"
    )
    assert "failed" in result.stdout


def test_the_page_does_not_promise_data_the_server_cannot_produce():
    """The dashboard must not name a column the aggregation cannot fill.

    `AttendanceRow` is the contract. If a field is added to the page that the
    schema does not carry, the column renders empty for everyone — and if a
    *label* implies per-user completion, it presents a guess as data.
    """
    from schemas import AttendanceRow

    fields = set(AttendanceRow.model_fields)

    # Every column the page renders maps to a real field.
    rendered = {
        "username", "local_date", "first_seen", "last_seen", "last_seen_reason",
        "session_count", "present_seconds", "active_seconds", "break_seconds",
        "manual_break_seconds", "has_unended_break", "tasks_touched",
        "tasks_reviewed",
    }
    assert rendered <= fields, (
        f"the page renders fields the schema does not carry: {rendered - fields}"
    )

    # And nothing in the contract implies a figure that is not derivable.
    assert not [f for f in fields if "completed" in f], (
        "a field name implies per-user task completion, which cannot be "
        "attributed — there is no author column on the annotation write path"
    )


def test_the_attendance_page_pins_its_entry_script():
    """Rule 13: module imports are version-pinned, including the entry tag.

    An unpinned entry script is served from cache indefinitely after a change,
    so the page silently keeps running the old module.
    """
    page = Path(__file__).parent.parent / "frontend" / "attendance.html"
    markup = page.read_text(encoding="utf-8")
    assert 'src="js/pages/attendance.js?v=' in markup, (
        "the entry script tag is not version-pinned"
    )
    assert 'href="styles.css?v=' in markup


def test_the_nav_hides_attendance_by_default():
    """The server is the boundary, but the default must still be closed.

    Read as text rather than executed: this asserts the *source* carries the
    adminOnly marker, so removing it is a visible diff rather than a silent
    change in who sees the tab.
    """
    nav = Path(__file__).parent.parent / "frontend" / "js" / "components" / "app-nav.js"
    source = nav.read_text(encoding="utf-8")
    assert "adminOnly: true" in source
    assert "attendance.html" in source

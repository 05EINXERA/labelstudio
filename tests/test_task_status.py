"""The task-status vocabulary: the approved group and its two copies.

'Approved', 'Verified', 'Checked' and 'Passed' are synonyms that differ only in
which export batch a sign-off belongs to. Everything that treats one of them
specially must treat all of them the same way, and the client's mirror must
agree with the server's definition — a batch the client does not know about
cannot be ticked in the export filter, which is the whole point of the feature.

The JS half of the guard runs under node (see tests/js/task_status_spec.mjs);
this module asserts the server side and pins the two vocabularies together.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

import schemas
from formats.common import TO_EXTERNAL_STATUS, from_external_status, to_external_status

SPEC = Path(__file__).parent / "js" / "task_status_spec.mjs"
MIRROR = Path(__file__).parent.parent / "frontend" / "js" / "task-status.js"


# --- the server-side vocabulary ---------------------------------------------

def test_approved_group_contents():
    """Pinned to a literal so adding a batch status is a deliberate act."""
    assert schemas.APPROVED_STATUSES == ("Approved", "Verified", "Checked", "Passed")


def test_task_statuses_is_working_plus_approved_plus_rejected():
    assert schemas.TASK_STATUSES == [
        "New", "In Progress", "Completed",
        "Approved", "Verified", "Checked", "Passed",
        "Rejected",
    ]


def test_every_approved_status_requires_review():
    """The reviewer gate reads REVIEW_STATUSES, so a batch missing from it
    would be settable by any annotator — a silent authorization hole."""
    for status in schemas.APPROVED_STATUSES:
        assert status in schemas.REVIEW_STATUSES
    assert "Rejected" in schemas.REVIEW_STATUSES
    # 'Completed' is the annotator's own submission, never a reviewer verdict.
    assert "Completed" not in schemas.REVIEW_STATUSES


def test_every_approved_status_is_terminal():
    """Editing an approved task must demote it whatever batch it is in."""
    for status in schemas.APPROVED_STATUSES:
        assert status in schemas.TERMINAL_STATUSES
    assert "Completed" in schemas.TERMINAL_STATUSES
    # A rejection means there *is* more work to do; it is not a finished state.
    assert "Rejected" not in schemas.TERMINAL_STATUSES


def test_is_approved():
    for status in schemas.APPROVED_STATUSES:
        assert schemas.is_approved(status)
    for status in ("New", "In Progress", "Completed", "Rejected", None, ""):
        assert not schemas.is_approved(status)


def test_every_approved_status_has_a_review_verb():
    """Without a verb the review endpoint 422s and the batch cannot be set at
    all through the audited path."""
    for status in schemas.APPROVED_STATUSES:
        verb = status.lower()
        assert verb in schemas.REVIEW_ACTIONS
        assert schemas.REVIEW_ACTION_STATUS[verb] == status


def test_review_action_literal_covers_every_verb():
    """The Pydantic Literal cannot be built from a variable, so it is a third
    copy. schemas.py asserts this at import time; asserting it here too names
    the failure clearly instead of surfacing as a collection error."""
    from typing import get_args

    assert set(get_args(schemas.ReviewActionLiteral)) == set(schemas.REVIEW_ACTIONS)


def test_approved_is_the_first_verb_for_backwards_compatibility():
    """Cached bundles only know 'approved'; it must keep working."""
    assert schemas.REVIEW_ACTION_STATUS["approved"] == "Approved"


# --- interop round trip ------------------------------------------------------

def test_every_task_status_has_an_interop_mapping():
    """An unmapped status exports raw and re-imports as 'New' (see
    from_external_status), silently destroying an approval."""
    for status in schemas.TASK_STATUSES:
        assert status in TO_EXTERNAL_STATUS


def test_batch_statuses_export_as_approved():
    for status in schemas.APPROVED_STATUSES:
        assert to_external_status(status) == ("completed", "approved")


def test_approved_round_trip_collapses_to_approved():
    """Deliberately lossy: the batch is our export bookkeeping, and a task
    arriving from outside was never in one of our batches. What matters is that
    approved stays approved rather than degrading to 'New'."""
    for status in schemas.APPROVED_STATUSES:
        base, external = to_external_status(status)
        assert from_external_status(base, external) == "Approved"


# --- the client mirror -------------------------------------------------------

def _js_array(name: str) -> list:
    """Read a `export const NAME = [...]` string array out of the mirror."""
    source = MIRROR.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \[(.*?)\];", source, re.S)
    assert match, f"{name} not found in {MIRROR}"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_client_mirror_lists_the_same_approved_group():
    """The pair cannot drift by both being edited to a new but different
    vocabulary — this compares the actual client file to the actual server
    constant, not each to a literal."""
    assert _js_array("APPROVED_STATUSES") == list(schemas.APPROVED_STATUSES)


def test_client_mirror_lists_the_same_working_statuses():
    assert _js_array("WORKING_STATUSES") == list(schemas.WORKING_STATUSES)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_client_task_status_spec():
    assert SPEC.exists(), f"missing spec: {SPEC}"
    result = subprocess.run(
        [shutil.which("node"), str(SPEC)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"task-status spec failed:\n{result.stdout}\n{result.stderr}"
    )

"""The premature-resumption bug: a real End Break reported "not ended".

Found in production 2026-09-24. See
`.devnotes/attendance-feature/11-BREAK-END-RACE.md`.

The client keeps emitting ambient `seen` rows during a declared break (the
overlay pauses the annotation timer, not the session heartbeat), and those rows
are irregular. When they stop for at least IDLE_GAP just before the annotator
returns, the resumption rule closed the break on the first row after the
silence and flagged it `ended=False` — then the genuine `break_end`, arriving
seconds later, found no open break and was silently discarded.

These tests pin both halves of the rule: an explicit end that is about to
arrive must win, and a break nobody ever ended must still be flagged.
"""
from datetime import datetime, timedelta, timezone

from api.attendance import (
    KIND_BREAK_END,
    KIND_BREAK_MANUAL_END,
    KIND_BREAK_MANUAL_START,
    KIND_BREAK_START,
    KIND_SEEN,
)
from api import attendance_report as report


def at(h, m, s=0, kind=KIND_SEEN, task_id=None):
    """One observation at a wall-clock time on the fixture day, UTC.

    Seconds matter here in a way they do not in test_attendance_report.py: the
    production failure turned on a 16-second distance between an ambient row
    and the user's click, so the fixtures spell out seconds.
    """
    return {
        "user_id": 1,
        "seen_at": datetime(2026, 9, 24, h, m, s, tzinfo=timezone.utc),
        "task_id": task_id,
        "instance_id": "test",
        "kind": kind,
        "created_at": None,
        "entered_by": None,
    }


def only_break(rows, **kwargs):
    """The single break across all sessions, asserting there is exactly one."""
    sessions = report.sessionise(rows, **kwargs)
    breaks = [b for s in sessions for b in s["breaks"]]
    assert len(breaks) == 1, f"expected exactly one break, got {breaks}"
    return breaks[0]


# --- The production sequence ------------------------------------------------


def test_the_production_sequence_honours_the_real_break_end():
    """The reported failure, verbatim.

    sanjeeta dahal, 2026-09-24: break_start 05:30:21Z, ambient `seen` rows
    through the break, a 12-minute silence, then `seen` at 05:57:37 and the
    real `break_end` at 05:57:53 — 16 seconds later.

    Before the fix this reported 1635s flagged "not ended". The endpoint had
    already logged seconds=1651 at write time, so the reader was the only
    thing disagreeing with reality.
    """
    rows = [
        at(5, 28, 27),
        at(5, 30, 3),
        at(5, 30, 21, kind=KIND_BREAK_START),
        at(5, 31, 5),
        at(5, 32, 37),
        at(5, 33, 37),
        at(5, 35, 37),
        at(5, 37, 37),
        at(5, 39, 37),
        at(5, 40, 37),
        at(5, 41, 37),
        at(5, 43, 37),
        at(5, 45, 37),
        # The 12-minute silence that used to be read as the return.
        at(5, 57, 37),
        at(5, 57, 53, kind=KIND_BREAK_END),
        at(5, 58, 24),
        at(5, 59, 53),
    ]

    taken = only_break(rows)

    assert taken["ended"] is True, (
        "the annotator pressed End Break; the break must not be flagged "
        "'not ended'"
    )
    # 05:30:21 -> 05:57:53 is 1652s at whole-second fixture precision. The
    # production rows carry milliseconds (.801 -> .659 = 1651.86s, truncated to
    # 1651, which is what the endpoint logged and what the fixed reporter now
    # returns against the real data). The point of the assertion is the figure
    # is derived from the real end row, not from the ambient row 16 seconds
    # before it -- which is where the pre-fix 1635s came from.
    assert taken["seconds"] == 1652, (
        "expected the true 05:30:21 -> 05:57:53 duration, got "
        f"{taken['seconds']}s"
    )
    assert taken["ended_at"] == datetime(
        2026, 9, 24, 5, 57, 53, tzinfo=timezone.utc
    ), "the break must end at the break_end row, not at the ambient row before it"

    sessions = report.sessionise(rows)
    assert not any(s["has_unended_break"] for s in sessions)


def test_the_ambient_row_before_the_end_does_not_shorten_the_break():
    """The 16-second shortfall, isolated.

    Closing on the resumption row loses exactly the distance between it and
    the real end. Small, but it makes the figure disagree with the duration the
    endpoint computed, and two sources of truth for one number is the bug.
    """
    rows = [
        at(9, 0),
        at(9, 5, kind=KIND_BREAK_START),
        at(9, 40),  # 35-minute silence, well past IDLE_GAP
        at(9, 40, 30, kind=KIND_BREAK_END),
    ]

    taken = only_break(rows)
    assert taken["seconds"] == (35 * 60) + 30
    assert taken["ended"] is True


# --- The rule it must not break --------------------------------------------


def test_a_forgotten_end_break_is_still_closed_and_flagged():
    """The resumption rule's whole purpose, unchanged.

    No break_end anywhere. The annotator went quiet and came back, so the
    break closes at the resumption and stays flagged — the figure is an upper
    bound and the flag is what says so.
    """
    rows = [
        at(9, 0),
        at(9, 5, kind=KIND_BREAK_START),
        at(9, 45),  # returns, never pressed End Break
        at(9, 46),
        at(9, 47),
    ]

    taken = only_break(rows)
    assert taken["ended"] is False, (
        "nobody ended this break; it must still be flagged 'not ended'"
    )
    assert taken["ended_at"] == datetime(2026, 9, 24, 9, 45, tzinfo=timezone.utc)

    sessions = report.sessionise(rows)
    assert any(s["has_unended_break"] for s in sessions)


def test_an_end_beyond_the_lookahead_does_not_rescue_the_break():
    """The window is bounded, and the resumption reading wins past it.

    An End Break pressed long after the annotator demonstrably came back is a
    different situation: they were working, then remembered the overlay. The
    break really did end when they returned, so closing at the resumption and
    flagging it is the honest reading.
    """
    late = report.BREAK_END_LOOKAHEAD + timedelta(minutes=5)
    rows = [
        at(9, 0),
        at(9, 5, kind=KIND_BREAK_START),
        at(9, 45),  # the resumption
        {
            **at(9, 45),
            "seen_at": datetime(2026, 9, 24, 9, 45, tzinfo=timezone.utc) + late,
            "kind": KIND_BREAK_END,
        },
    ]

    taken = only_break(rows)
    assert taken["ended"] is False
    assert taken["ended_at"] == datetime(2026, 9, 24, 9, 45, tzinfo=timezone.utc)


def test_an_end_exactly_at_the_lookahead_boundary_is_honoured():
    """The boundary is inclusive, matching how IDLE_GAP is treated.

    Pinned so retuning BREAK_END_LOOKAHEAD cannot silently invert the edge.
    """
    resumption = datetime(2026, 9, 24, 9, 45, tzinfo=timezone.utc)
    rows = [
        at(9, 0),
        at(9, 5, kind=KIND_BREAK_START),
        at(9, 45),
        {
            **at(9, 45),
            "seen_at": resumption + report.BREAK_END_LOOKAHEAD,
            "kind": KIND_BREAK_END,
        },
    ]

    taken = only_break(rows)
    assert taken["ended"] is True
    assert taken["ended_at"] == resumption + report.BREAK_END_LOOKAHEAD


def test_an_abandoned_break_past_max_break_still_wins_over_the_lookahead():
    """MAX_BREAK keeps its precedence.

    Someone reappearing past MAX_BREAK did not take a MAX_BREAK-long break,
    and a stray end row inside the lookahead must not turn a whole absence
    into declared break time. The ordering in `sessionise` is load-bearing.
    """
    start = datetime(2026, 9, 24, 9, 5, tzinfo=timezone.utc)
    back = start + report.MAX_BREAK + timedelta(minutes=10)
    rows = [
        at(9, 0),
        at(9, 5, kind=KIND_BREAK_START),
        {**at(9, 0), "seen_at": back},
        {**at(9, 0), "seen_at": back + timedelta(seconds=20), "kind": KIND_BREAK_END},
    ]

    sessions = report.sessionise(rows)
    # The abandoned interval counts as neither present nor break.
    assert all(s["ended_at"] <= start for s in sessions if s["started_at"] < start)
    total_break = sum(b["seconds"] for s in sessions for b in s["breaks"])
    assert total_break == 0, (
        f"an abandoned break must not be counted, got {total_break}s"
    )


def test_a_manual_break_is_unaffected_by_the_lookahead():
    """Retroactive breaks never hit the resumption rule in the first place.

    `source != "manual"` already exempts them, so the lookahead must not
    become a new way to reach them. A silence inside a retroactive break is
    just a silence.
    """
    rows = [
        at(9, 0),
        at(9, 5, kind=KIND_BREAK_MANUAL_START),
        at(9, 45),
        at(9, 46, kind=KIND_BREAK_MANUAL_END),
    ]

    taken = only_break(rows)
    assert taken["ended"] is True
    assert taken["source"] == "manual"
    assert taken["seconds"] == 41 * 60


def test_an_unrelated_break_start_does_not_count_as_an_end():
    """Only an end row rescues a break.

    Guards against a lookahead that matches any break-kind row: a second
    break_start inside the window is not evidence the first one ended.
    """
    rows = [
        at(9, 0),
        at(9, 5, kind=KIND_BREAK_START),
        at(9, 45),
        at(9, 45, 30, kind=KIND_BREAK_START),
    ]

    sessions = report.sessionise(rows)
    breaks = [b for s in sessions for b in s["breaks"]]
    first = min(breaks, key=lambda b: b["started_at"])
    assert first["ended"] is False, (
        "a second break_start is not evidence the first break ended"
    )
    assert first["ended_at"] == datetime(2026, 9, 24, 9, 45, tzinfo=timezone.utc)


def test_the_lookahead_does_not_reach_across_a_second_break_start():
    """A break_start between the resumption and an end row breaks the chain.

    start -> silence -> resumption -> start -> end. The end belongs to the
    SECOND break, so the first must still be flagged rather than borrowing it.
    """
    rows = [
        at(9, 0),
        at(9, 5, kind=KIND_BREAK_START),
        at(9, 45),
        at(9, 45, 20, kind=KIND_BREAK_START),
        at(9, 45, 40, kind=KIND_BREAK_END),
    ]

    sessions = report.sessionise(rows)
    breaks = [b for s in sessions for b in s["breaks"]]
    first = min(breaks, key=lambda b: b["started_at"])
    assert first["ended"] is False, (
        "the end row closes the second break; the first must not claim it"
    )

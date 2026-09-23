"""Sessionisation and day aggregation — the read side of attendance.

Pure functions over observation rows, deliberately: the CLI report (R3), the
admin endpoints (R4), the profile page (R9) and the nightly rollup (R8) all run
the *identical* aggregation, so a discrepancy between what the dashboard shows
and what the permanent record stores cannot arise from two implementations
drifting. This module knows nothing about FastAPI, and `sessionise()` does not
even take a database session.

**The +05:45 trap.** Nepal is on a 45-minute offset. Anything assuming
whole-hour offsets — a numeric offset, `timedelta(hours=...)`, truncating a UTC
timestamp to a date — is wrong by 45 minutes, and the symptom looks like a
rounding bug rather than a timezone bug. Every conversion here goes through
`ZoneInfo(config.ATTENDANCE_TZ)`. An observation at 00:15 Kathmandu is 18:30 UTC
*the previous day*, and it belongs to the local day.

**Definitions** (02-requirements-definitions.md §§ 1, 1.1, 4, 5):

- A **session** is a maximal run of observations with no gap greater than
  `IDLE_GAP`. An explicit logout closes it at the logout timestamp.
- A session is **open** if its last observation is younger than `IDLE_GAP`,
  otherwise **closed by timeout**. A timeout end is a lower bound and is
  reported as such — never as a plain logout time.
- A **break** is a declared interval *inside* a session, not a session
  boundary. Present time excludes breaks.
- An **unended break** is closed by the same fallback and reported
  "break, not ended" rather than silently dropped.
- **Tasks touched**, never "completed": no author column exists on the
  annotation write path, so per-user completion is not derivable.
"""
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import config
from api.attendance import (
    KIND_ACTIVE,
    KIND_BREAK_END,
    KIND_BREAK_MANUAL_END,
    KIND_BREAK_MANUAL_START,
    KIND_BREAK_START,
    KIND_LOGOUT,
)

# How long a gap may be before it ends a session, rather than being ordinary
# jitter inside one.
#
# 5 minutes, matching the annotation timer's own idle threshold
# (`IDLE_TIMEOUT_MS` in frontend/js/components/timer.js). Two mechanisms that
# both decide "this person stopped working" should agree on when, or the
# register and the timer tell different stories about the same gap. It is 10x
# the 30s signal cadence, so jitter or a brief network drop cannot split a
# session.
#
# WAS 10 minutes, and a real case showed that was too long in a specific way:
# an annotator closed her tab at 10:50 and logged back in at 11:00, and the
# two sessions merged into one 25-minute stretch instead of 10 + 5. The gap
# was *exactly* IDLE_GAP, and the comparison below was `>`, so the single
# duration the threshold is named for was the one value it let through. Both
# halves are fixed: the threshold is lower, and the comparison is now `>=`.
#
# Since Q4 this is the *fallback* that closes a session when someone simply
# closes the tab; declared breaks are the primary mechanism, and a declared
# break suspends this rule entirely.
IDLE_GAP = timedelta(minutes=5)

# How long a declared break may absorb silence before it is treated as
# abandoned rather than ongoing.
#
# A declared break suspends IDLE_GAP, which is right: during a break the client
# deliberately sends nothing, so the gap is almost always longer than the idle
# threshold and applying it would count the break as absence. But the
# suspension needs a ceiling, because `break_start` and `break_end` come from
# two separate clicks and the second one is exactly as skippable as a logout —
# the founding problem of this feature, reappearing inside its own fix.
#
# Without a ceiling an annotator who pressed Take a Break and then closed the
# tab had the idle rule suspended for the rest of the day: their next session
# hours later was absorbed into the abandoned one, the whole absence was
# recorded as a *declared* break, and present time collapsed to near zero.
# Q17 specifies the answer — an unended break "is closed by the same IDLE_GAP
# fallback" — so the suspension is bounded here rather than being open-ended.
#
# 4 hours is deliberately generous: it must not clip a real lunch or a genuine
# long break, only a break nobody ever came back from. Past it the session is
# closed at the break start, the last moment there is evidence of presence.
MAX_BREAK = timedelta(hours=4)

# How long a running break may go unconfirmed before the live "on break" pill
# stops claiming the annotator is on it.
#
# This bounds a *display* claim, not the accounting, and the two want different
# answers. The break keeps accruing up to MAX_BREAK either way — that is the
# honest record of an interval nobody closed. But "on break" in the present
# tense is a statement about right now, and after a long silence the only
# truthful thing to say is that we do not know: they may be at lunch, or they
# may have shut the laptop an hour ago. The pill drops off and the row reads as
# an ordinary unended break, which is what the evidence actually supports.
#
# 45 minutes covers a meal with room to spare while keeping a stale claim off
# the admin's screen for the rest of the afternoon.
BREAK_LIVE_MAX = timedelta(minutes=45)

# How a session ended. `open` is not a state a stored row keeps — it is what a
# session looks like when read while still running.
END_LOGOUT = "logout"
END_TIMEOUT = "timeout"
END_OPEN = "open"

_BREAK_STARTS = frozenset({KIND_BREAK_START, KIND_BREAK_MANUAL_START})
_BREAK_ENDS = frozenset({KIND_BREAK_END, KIND_BREAK_MANUAL_END})
_MANUAL_KINDS = frozenset({KIND_BREAK_MANUAL_START, KIND_BREAK_MANUAL_END})


def site_tz() -> ZoneInfo:
    """The configured site timezone. Never a numeric offset (see module note)."""
    return ZoneInfo(config.ATTENDANCE_TZ)


def as_utc(moment):
    """Force a datetime to tz-aware UTC, or None.

    **Not decoration — the aggregation crashes without it.** SQLite has no
    timezone type, so a `DateTime(timezone=True)` column read back through it
    yields a *naive* datetime, while Postgres yields an aware one. Comparing a
    naive value with an aware one raises `TypeError`, so the same code path
    works on the deployment and dies on the dev/test database (or on any row
    written before rule 7 was followed).

    Rows are always stored as UTC, so attaching UTC is a restoration of what
    the column means, not a guess. Doing it here — at the boundary where rows
    enter the aggregation — keeps every function below able to assume aware
    datetimes.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def local_day(moment: datetime) -> date:
    """The local calendar day a UTC instant falls on.

    This is the function the whole +05:45 problem reduces to. 18:30 UTC is
    00:15 the *next* day in Kathmandu, so the answer is not the UTC date and
    cannot be obtained by truncating one.
    """
    # Assume UTC for a naive value rather than the machine's local zone, which
    # would make the result depend on where the server happens to run.
    return as_utc(moment).astimezone(site_tz()).date()


def day_bounds(local_date: date) -> tuple:
    """The UTC half-open interval [start, end) covering one local day.

    Used to filter rows in SQL. Built by localising midnight rather than by
    adding an offset, so the 45 minutes are handled by the zone database
    instead of by arithmetic this module would have to get right.
    """
    tz = site_tz()
    start_local = datetime.combine(local_date, datetime.min.time(), tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def sessionise(observations, now=None, idle_gap=IDLE_GAP) -> list:
    """Group one user's observations into sessions. Pure; no I/O.

    `observations` is an iterable of dicts with at least `seen_at` and `kind`,
    in any order — they are sorted here, because the buffer merge (R4) appends
    live rows to rows read from the database and the two are not ordered
    relative to each other.

    Returns a list of session dicts. One pass, so the cost is linear in the
    number of observations rather than quadratic in the number of sessions.
    """
    # Normalised on the way in: stored rows and buffered rows arrive from two
    # sources, and on SQLite the stored ones come back naive (see as_utc).
    rows = sorted(
        (
            {**o, "seen_at": as_utc(o["seen_at"]), "created_at": as_utc(o.get("created_at"))}
            for o in observations
            if o.get("seen_at") is not None
        ),
        key=lambda o: o["seen_at"],
    )
    if not rows:
        return []

    now = now or datetime.now(timezone.utc)
    sessions = []
    current = None

    for row in rows:
        seen_at = row["seen_at"]
        kind = row.get("kind")

        if current is None:
            current = _new_session(seen_at)
        elif current["open_break"] is not None:
            # A declared break suspends the idle rule. During a break the
            # client sends nothing — that is the point of declaring it — so the
            # gap between `break_start` and `break_end` is almost always longer
            # than IDLE_GAP. Applying the timeout here would split every break
            # into two sessions and count it as absence, which is precisely
            # what declaring it is meant to prevent (§ 1.1: "a break does not
            # end the session").
            #
            # Only an explicit `break_end` or MAX_BREAK closes a break.
            # Ambient traffic must NOT, and this was got wrong once:
            #
            # A rule was added here treating any `seen` row during an open
            # break as evidence the annotator was back, on the premise that "a
            # client on a declared break is silent by design". That premise is
            # false. The overlay pauses the annotation *timer*, but it does not
            # stop the page making authenticated requests, and every one of
            # them records a `seen` observation in `get_current_user`
            # (api/auth.py). A second tab on the same login does it too.
            #
            # So a real 9-minute break froze at 6 minutes — IDLE_GAP plus one
            # throttle window — and stopped accruing, live on the dashboard and
            # permanently in the rollup. The break's own subject matter is a
            # person who is NOT working; using request traffic to detect their
            # return only works if nothing else can produce it.
            #
            # What IS evidence of a return is traffic resuming after a
            # SILENCE, which is a different signal from traffic merely
            # existing. A tab left open chatters continuously through a break
            # (saroj's case: no gap anywhere, so nothing below fires). Someone
            # who actually walked away goes quiet and then comes back, leaving
            # a gap of at least IDLE_GAP with the break still open — and that
            # resumption is the moment they returned.
            #
            # Without this, a forgotten End Break swallowed the rest of the day
            # up to MAX_BREAK: the 13:00 break of an annotator back at 13:40
            # ate their whole afternoon as break time. Bounding it only by
            # MAX_BREAK was too loose, exactly as closing on any traffic was
            # too tight.
            #
            # The break is closed at the RESUMPTION — the first observation
            # after the silence — because the annotator was away for the whole
            # of that silence and this is the first evidence they are back.
            # It stays flagged unended: the true end is somewhere at or before
            # this moment and we cannot know where, so the figure is an upper
            # bound and the flag is what says so.
            # Declared breaks only, for the same reason the traffic rule was
            # restricted (D61): a *manual* break is entered after the fact over
            # a stretch whose traffic pattern says nothing about it. A silence
            # inside a retroactive break is just a silence, not a return, and
            # closing on it truncates the interval the annotator explicitly
            # asked to record.
            #
            # MAX_BREAK is checked FIRST. A break that has already run past it
            # was abandoned, and someone reappearing five hours later did not
            # take a five-hour break — they left and came back. Letting the
            # resumption rule close it would record the whole absence as
            # declared break time, which is a far stronger claim than the
            # evidence supports.
            break_age = seen_at - current["open_break"]["started_at"]
            resumed_after_silence = (
                current["open_break"]["source"] != "manual"
                and kind not in _BREAK_ENDS
                and seen_at - current["last_at"] >= idle_gap
            )

            if break_age < MAX_BREAK and resumed_after_silence:
                current["breaks"].append(
                    _close_break(current["open_break"], seen_at)
                )
                current["breaks"][-1]["ended"] = False
                current["open_break"] = None
            elif break_age >= MAX_BREAK:
                # Past MAX_BREAK nobody came back. This observation belongs to
                # a new session rather than to the far side of an abandoned
                # break, and the old one is closed at the break's start — the
                # last moment with evidence of presence — so the abandoned
                # interval counts as neither present nor break.
                sessions.append(_close_session(
                    current, current["open_break"]["started_at"], END_TIMEOUT,
                ))
                current = _new_session(seen_at)
        elif kind in _BREAK_STARTS and seen_at - current["last_at"] < MAX_BREAK:
            # A break_start does not split the session it interrupts. Pressing
            # Take a Break is itself evidence the annotator was at the
            # keyboard, so the work before it belongs to the same session —
            # even when the last ambient observation is more than IDLE_GAP old
            # (the throttle spaces `seen` rows up to a minute apart, and a
            # quiet stretch just before stepping away is ordinary). Splitting
            # here stranded the pre-break work as a separate session showing
            # zero present time.
            #
            # Bounded by MAX_BREAK so this stays a continuation rule and does
            # not reach across a whole absence: a retroactive break entered
            # from the profile page for a much earlier moment, or a return
            # after hours away, still starts a new session.
            pass
        elif seen_at - current["last_at"] >= idle_gap:
            # The gap is too long to be jitter: close the run and start a new
            # one. The end is the last thing we *saw*, not the start of the
            # gap — a lower bound, which is why it is reported as a timeout.
            sessions.append(_close_session(current, current["last_at"], END_TIMEOUT))
            current = _new_session(seen_at)

        current["last_at"] = seen_at
        current["observations"] += 1

        if row.get("task_id") is not None:
            current["task_ids"].add(row["task_id"])
        if kind == KIND_ACTIVE:
            current["active_observations"] += 1

        if kind in _BREAK_STARTS:
            # A second break_start with one already open is not an error worth
            # rejecting at read time — the row exists and cannot be edited
            # (append-only). The earliest start wins, which is the
            # conservative reading: it never *shortens* a declared break.
            if current["open_break"] is None:
                current["open_break"] = {
                    "started_at": seen_at,
                    "source": "manual" if kind in _MANUAL_KINDS else "declared",
                    "entered_at": row.get("created_at"),
                }
        elif kind in _BREAK_ENDS:
            if current["open_break"] is not None:
                current["breaks"].append(_close_break(current["open_break"], seen_at))
                current["open_break"] = None
        elif kind == KIND_LOGOUT:
            # The one moment an end can be *stated* rather than inferred.
            sessions.append(_close_session(current, seen_at, END_LOGOUT))
            current = None

    if current is not None:
        # The final run is open if we saw it recently enough, otherwise it
        # timed out at its last observation.
        # `<`, matching the split rule: a session whose last observation
        # is exactly IDLE_GAP old has timed out, not still open.
        open_break = current["open_break"]
        if open_break is not None and now - open_break["started_at"] >= MAX_BREAK:
            # An abandoned break, read after the fact: closed at its start for
            # the same reason as in the loop, rather than reported as a session
            # still running hours later.
            sessions.append(_close_session(
                current, open_break["started_at"], END_TIMEOUT,
            ))
        elif open_break is not None:
            # A break in progress right now. The session ends at `now`, not at
            # the last observation: the annotator is on a declared break, so
            # the time since the last `seen` row is break time that is still
            # accruing, not a gap of unknown presence. Closing at `last_at`
            # froze a running break at whatever moment traffic last happened
            # to land on — the dashboard showed it ticking up and then stop.
            sessions.append(_close_session(current, now, END_OPEN))
        elif now - current["last_at"] < idle_gap:
            sessions.append(_close_session(current, current["last_at"], END_OPEN))
        else:
            sessions.append(_close_session(current, current["last_at"], END_TIMEOUT))

    return sessions


def _new_session(started_at: datetime) -> dict:
    return {
        "started_at": started_at,
        "last_at": started_at,
        "observations": 0,
        "active_observations": 0,
        "task_ids": set(),
        "breaks": [],
        "open_break": None,
    }


def _close_break(open_break: dict, ended_at: datetime) -> dict:
    seconds = max(0, int((ended_at - open_break["started_at"]).total_seconds()))
    return {
        "started_at": open_break["started_at"],
        "ended_at": ended_at,
        "seconds": seconds,
        "ended": True,
        "source": open_break["source"],
        "entered_at": open_break.get("entered_at"),
    }


def _close_session(session: dict, ended_at: datetime, end_reason: str) -> dict:
    breaks = list(session["breaks"])
    # Whether a break was still running when we read. Captured before the
    # open break is folded into `breaks` below, because afterwards an
    # in-progress break and one abandoned hours ago look identical — both are
    # flagged `ended: False`. Only a session that is itself still open can
    # carry a break that is still running; on any other session the break is
    # over, whatever it says, because the person is gone.
    break_in_progress = (
        session["open_break"] is not None and end_reason == END_OPEN
    )

    if session["open_break"] is not None:
        # An unended break — the feature's founding problem reappearing inside
        # the new mechanism. Closed at the session end and reported as a lower
        # bound, exactly as an unclosed session is (Q17). Never silently
        # dropped: a break that vanishes inflates present time.
        unended = _close_break(session["open_break"], ended_at)
        unended["ended"] = False
        breaks.append(unended)

    break_seconds = sum(b["seconds"] for b in breaks)
    manual_break_seconds = sum(
        b["seconds"] for b in breaks if b["source"] == "manual"
    )
    span = max(0, int((ended_at - session["started_at"]).total_seconds()))

    return {
        "started_at": session["started_at"],
        "ended_at": ended_at,
        "end_reason": end_reason,
        # Present time excludes declared breaks (§ 1.1). Floored at zero: a
        # break longer than its session would otherwise report negative time.
        "seconds": max(0, span - break_seconds),
        "span_seconds": span,
        "break_seconds": break_seconds,
        "manual_break_seconds": manual_break_seconds,
        "breaks": breaks,
        "task_ids": session["task_ids"],
        "tasks_touched": len(session["task_ids"]),
        "observations": session["observations"],
        "active_observations": session["active_observations"],
        "has_unended_break": any(not b["ended"] for b in breaks),
        "break_in_progress": break_in_progress,
    }


def active_seconds(sessions, throttle_seconds=None) -> int:
    """Time the annotation timer was running, from `active` observations.

    NOT from `time_logs`, which is a lifetime cumulative total with no date
    column and so cannot be bucketed by day at all (Q18).

    Each `active` observation is evidence the timer was running at that moment,
    and the throttle means one stands for up to one throttle window. Counting
    `n * window` would overstate the last observation of every run, so this is
    an estimate by construction — it is labelled "Active (timer)" in the UI and
    sits beside Present precisely because the two will not agree.
    """
    window = throttle_seconds or config.ATTENDANCE_THROTTLE_SECONDS
    total = 0
    for session in sessions:
        if not session["active_observations"]:
            continue
        # Never credit more active time than the session actually spans.
        total += min(session["active_observations"] * window, session["span_seconds"])
    return total


def _break_is_live(session: dict, now=None) -> bool:
    """Whether to claim, in the present tense, that this session is on a break.

    Narrower than `session["break_in_progress"]`, which only says a break was
    open when the session was closed. A break nobody has confirmed for longer
    than BREAK_LIVE_MAX stops being a live claim: the accounting still counts
    it, but the pill no longer asserts the annotator is sitting there.
    """
    if not session.get("break_in_progress"):
        return False
    running = [b for b in session["breaks"] if not b["ended"]]
    if not running:
        return False
    started_at = min(b["started_at"] for b in running)
    now = now or datetime.now(timezone.utc)
    return now - started_at < BREAK_LIVE_MAX


def summarise_day(observations, local_date, now=None, reviews=0) -> dict:
    """One user's day, as the admin table row shape.

    `observations` are that user's rows for the day; `reviews` is their
    `task_reviews` count, which is counted in SQL rather than here (rule 11b).
    """
    sessions = sessionise(observations, now=now)
    if not sessions:
        return None

    task_ids = set()
    for session in sessions:
        task_ids |= session["task_ids"]

    last = sessions[-1]
    return {
        "local_date": local_date,
        "first_seen": sessions[0]["started_at"],
        "last_seen": last["ended_at"],
        # The end reason of the *last* session is the day's end reason: it is
        # what the "latest logout" column is qualified by.
        "end_reason": last["end_reason"],
        "session_count": len(sessions),
        "present_seconds": sum(s["seconds"] for s in sessions),
        "break_seconds": sum(s["break_seconds"] for s in sessions),
        "manual_break_seconds": sum(s["manual_break_seconds"] for s in sessions),
        "active_seconds": active_seconds(sessions),
        "tasks_touched": len(task_ids),
        "tasks_reviewed": reviews,
        "has_unended_break": any(s["has_unended_break"] for s in sessions),
        # Read from the last session only: an earlier one cannot still be on a
        # break, because something was observed after it. Drives the "on break"
        # pill beside "still here", and is dropped once the break has run
        # longer than BREAK_LIVE_MAX without confirmation — see the constant.
        "break_in_progress": _break_is_live(last, now),
        "sessions": sessions,
    }


def observation_dicts(rows) -> list:
    """ORM `AttendanceObservation` rows → the dicts these functions take.

    The same shape `api.attendance.buffered_observations()` returns, so stored
    and still-buffered rows can be concatenated and sessionised together — which
    is what makes an open session show correctly rather than lagging a flush
    interval behind.
    """
    return [
        {
            "user_id": row.user_id,
            "seen_at": as_utc(row.seen_at),
            "task_id": row.task_id,
            "instance_id": row.instance_id,
            "kind": row.kind,
            "created_at": as_utc(row.created_at),
            "entered_by": row.entered_by,
        }
        for row in rows
    ]

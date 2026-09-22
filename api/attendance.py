"""The attendance capture buffer.

Presence is observed on the request path and written off it. This module owns
both halves: `note_seen()` is the in-request side, `flush()` the after-response
side.

**Dependency direction** (docs/ARCHITECTURE.md § 2, mirroring
`api/permissions.py`): this imports `models`, `database` and `config` and
*nothing* from `api/routers/`. That is what lets `api/auth.py` call it without
an import cycle — the auth layer must not learn which routers exist.

**What runs inside a request is the whole point of the design.** `note_seen()`
does one `datetime.now()`, one dict lookup, one comparison, and — at most once
per user per throttle window — two dict assignments. No I/O, no DB call, no
lock. This deployment has already been taken down once by per-request work on a
hot path (`models.py`'s `Annotation` docstring; `.devnotes/server-issue-
diagnosis/`), and `get_current_user` runs on *every* `/api/*` request.

**Attendance may never break annotation.** Every entry point here is wrapped so
it cannot raise into a request, but the exception is logged with a traceback
rather than swallowed — the principle `logging_service` states for itself: "a
net that can drop what it is catching is worse than no net" (CLAUDE.md rule 3).

**Single worker only.** `_LAST_SEEN` and `_PENDING` are in-process dicts, so
they join `JOBS`, `_models` and `_TASK_LOCKS` as reasons the app must run as
exactly one uvicorn worker (CLAUDE.md rule 9). Do not add `--workers N`.

The buffer is lost on restart, by design. The visible effect is a spurious
session break, not lost attendance (04-decision-and-impl-plan.md § 7).
"""
import logging
import threading
from datetime import datetime, timezone

import config
import models
from database import SessionLocal, commit_with_retry
from logging_service import log_event

logger = logging.getLogger(__name__)

# The kinds an observation may carry. `seen` is the ambient signal from the
# authenticated request path; the rest are explicit moments. See
# models.AttendanceObservation.kind.
KIND_SEEN = "seen"
KIND_ACTIVE = "active"
KIND_LOGIN = "login"
KIND_LOGOUT = "logout"
KIND_BREAK_START = "break_start"
KIND_BREAK_END = "break_end"
KIND_BREAK_MANUAL_START = "break_manual_start"
KIND_BREAK_MANUAL_END = "break_manual_end"

# Kinds that are *events*: they record a moment that matters in its own right,
# so they are never throttled away and never deduplicated. Only the ambient
# `seen`/`active` signals are throttled.
_EVENT_KINDS = frozenset({
    KIND_LOGIN,
    KIND_LOGOUT,
    KIND_BREAK_START,
    KIND_BREAK_END,
    KIND_BREAK_MANUAL_START,
    KIND_BREAK_MANUAL_END,
})

# user_id -> the monotonic-ish wall clock of that user's last *throttled*
# observation. Read on every authenticated request; written at most once per
# user per throttle window.
_LAST_SEEN: dict = {}

# Rows waiting to be written, as plain tuples rather than ORM objects: building
# an ORM instance per observation would allocate on the request path for a row
# that may be one of hundreds in a single batched INSERT.
_PENDING: list = []

# Guards _PENDING only, and only on the flush side. note_seen() deliberately
# does NOT take it — see the comment there.
_FLUSH_LOCK = threading.Lock()

# Wall clock of the last flush attempt, so the flush cannot fire more often
# than ATTENDANCE_FLUSH_SECONDS however much POST traffic there is.
_LAST_FLUSH = 0.0


def note_seen(user_id, *, kind: str = KIND_SEEN, task_id=None,
              seen_at=None, entered_by=None) -> None:
    """Record that `user_id` was seen. In-memory only; never raises.

    This is the function on the hot path. Everything it does is a dict
    operation — there is no I/O here and none may be added.

    Ambient `seen`/`active` observations are throttled to one per user per
    `ATTENDANCE_THROTTLE_SECONDS`; event kinds (login, logout, breaks) are
    always recorded, because a boundary that gets throttled away is the one
    observation that cannot be reconstructed.
    """
    if not config.ATTENDANCE_ENABLED:
        return
    try:
        if user_id is None:
            return

        now = datetime.now(timezone.utc)
        is_event = kind in _EVENT_KINDS

        if not is_event:
            # The throttle. A dict lookup and a comparison, and on the common
            # path (a user who was already seen this minute) that is the
            # entire cost of attendance for this request.
            #
            # Keyed on (user, kind), not user alone. `active` is the evidence
            # that the timer was running, and it is what the per-day "Active
            # (timer)" figure is summed from (Q18) — so it must not be
            # throttled away by an ordinary `seen` from an unrelated request a
            # moment earlier. Sharing one slot silently under-reports active
            # time in exactly the sessions where the annotator was busiest.
            throttle_key = (user_id, kind)
            last = _LAST_SEEN.get(throttle_key)
            if last is not None and (now - last).total_seconds() < config.ATTENDANCE_THROTTLE_SECONDS:
                return
            _LAST_SEEN[throttle_key] = now

        # No lock. `list.append` is atomic under the GIL, and taking a lock on
        # the hot path is exactly the blocking work invariant 1 forbids. The
        # flush swaps the list out rather than mutating it in place, so an
        # append racing a flush lands in either the old list or the new one —
        # never in a half-built one.
        _PENDING.append((
            user_id,
            seen_at or now,
            task_id,
            config.ATTENDANCE_INSTANCE_ID,
            kind,
            entered_by,
        ))

        # Bounded. If flushes have stopped firing, drop the oldest rather than
        # grow without limit: attendance is a nice-to-have and memory is not.
        if len(_PENDING) > config.ATTENDANCE_BUFFER_MAX:
            _drop_overflow()
    except Exception:  # pragma: no cover - attendance must never break a request
        # Broad by intent, like logging_service.log_event: this is the outermost
        # safety net on the annotation path. Logged with a traceback, never
        # silent (CLAUDE.md rule 3).
        logger.warning("attendance note_seen failed", exc_info=True)


def _drop_overflow() -> None:
    """Trim the buffer to the cap, oldest first, and say so."""
    overflow = len(_PENDING) - config.ATTENDANCE_BUFFER_MAX
    if overflow <= 0:
        return
    del _PENDING[:overflow]
    logger.warning(
        "attendance buffer hit %d rows; dropped %d oldest. Flushes may not be "
        "firing.", config.ATTENDANCE_BUFFER_MAX, overflow,
    )


def buffered_observations(user_ids=None, since=None) -> list:
    """The unflushed rows, as dicts, for merging into a read.

    The dashboard reads this so an open session shows correctly rather than
    lagging a flush interval behind. Reading an in-memory dict is not a
    database write, so this does not put a GET in breach of CLAUDE.md rule 4.
    """
    try:
        rows = list(_PENDING)
    except Exception:  # pragma: no cover - a read must never break the page
        logger.warning("attendance buffer read failed", exc_info=True)
        return []

    out = []
    for user_id, seen_at, task_id, instance_id, kind, entered_by in rows:
        if user_ids is not None and user_id not in user_ids:
            continue
        if since is not None and seen_at < since:
            continue
        out.append({
            "user_id": user_id,
            "seen_at": seen_at,
            "task_id": task_id,
            "instance_id": instance_id,
            "kind": kind,
            "entered_by": entered_by,
        })
    return out


def should_flush() -> bool:
    """Whether enough time has passed and there is anything to write."""
    if not config.ATTENDANCE_ENABLED or not _PENDING:
        return False
    elapsed = datetime.now(timezone.utc).timestamp() - _LAST_FLUSH
    return elapsed >= config.ATTENDANCE_FLUSH_SECONDS


def flush() -> int:
    """Write the buffer as one batched INSERT. Returns the row count.

    **Never call this from inside a request.** It is a `BackgroundTasks`
    callback, so it runs after the response is sent and adds nothing to any
    client's latency (04-decision-and-impl-plan.md § 5). It is the one part of
    the feature that touches the database on ordinary traffic: one pool
    connection for one INSERT, at most once per flush interval.
    """
    global _LAST_FLUSH

    if not config.ATTENDANCE_ENABLED:
        return 0

    # Swap the buffer out under the lock, then release it before touching the
    # database. Holding it across the INSERT would make every appending request
    # wait on the database — reintroducing exactly the blocking the design
    # exists to avoid.
    with _FLUSH_LOCK:
        if not _PENDING:
            return 0
        batch = list(_PENDING)
        del _PENDING[:]
        _LAST_FLUSH = datetime.now(timezone.utc).timestamp()

    started = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        # One executemany, not N inserts. Mappings rather than ORM instances:
        # there is nothing to read back, so the identity map and the flush
        # machinery would be pure cost.
        db.bulk_insert_mappings(models.AttendanceObservation, [
            {
                "user_id": user_id,
                "seen_at": seen_at,
                "task_id": task_id,
                "instance_id": instance_id,
                "kind": kind,
                "entered_by": entered_by,
            }
            for user_id, seen_at, task_id, instance_id, kind, entered_by in batch
        ])
        commit_with_retry(db)  # CLAUDE.md rule 10, never raw db.commit()
    except Exception:
        # The rows are dropped, deliberately. Putting them back would let a
        # persistent failure (a bad column, a full disk) grow the buffer
        # without bound while retrying forever, which turns a missing
        # attendance row into an outage. Attendance is the thing that gives
        # way. Logged at ERROR so it is visible in the errors.log trail.
        db.rollback()
        logger.error(
            "attendance flush failed; dropped %d observations", len(batch),
            exc_info=True,
        )
        log_event("attendance.flush_failed", level="ERROR", rows=len(batch))
        return 0
    finally:
        db.close()

    duration_ms = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    # Buffer depth at flush is the one metric worth watching: a growing depth
    # means flushes are not firing (§ 9 Monitoring).
    log_event("attendance.flush", rows=len(batch), ms=duration_ms)
    return len(batch)


def maybe_flush(background_tasks) -> None:
    """Queue a flush after the response, if one is due.

    Call from POST handlers that already take `BackgroundTasks`. Cheap enough
    to call unconditionally: on the common path it is a falsy flag check.
    """
    try:
        if should_flush() and background_tasks is not None:
            background_tasks.add_task(flush)
    except Exception:  # pragma: no cover - must never break the write it rides
        logger.warning("attendance maybe_flush failed", exc_info=True)


# --- The periodic drain -----------------------------------------------------
#
# WHY THIS EXISTS, and it is not an optimisation.
#
# The flush originally rode POST traffic alone, from the timer ping. That was
# wrong, and the failure mode is the one this feature is meant to record: an
# annotator closes the tab, so the pings stop, so the buffer that holds their
# last observations is never written. Their final ten minutes sat in memory
# until some *other* request happened to drain them -- and if nobody else had
# a task open, nothing drained at all, and a restart lost them.
#
# Riding request traffic cannot fix that, because the very event we need to
# record (someone leaving) is the event that stops the traffic. So the drain
# runs on the app's own clock instead.
#
# The task is still bound to the single worker (CLAUDE.md rule 9): it touches
# the same in-process buffer as everything else here.

_DRAIN_TASK = None


async def _drain_loop() -> None:
    """Flush the buffer on a fixed interval, regardless of request traffic."""
    import asyncio

    while True:
        try:
            await asyncio.sleep(config.ATTENDANCE_FLUSH_SECONDS)
            if not config.ATTENDANCE_ENABLED:
                continue
            if not _PENDING:
                continue
            # to_thread, because flush() is blocking DB work and this is the
            # event loop. Running it inline would stall every request for the
            # duration of the INSERT.
            await asyncio.to_thread(flush)
        except asyncio.CancelledError:
            # Shutdown. Drain what is left rather than discarding it: these are
            # the observations of whoever was working when the server stopped.
            try:
                flush()
            except Exception:
                logger.warning(
                    "attendance final flush failed during shutdown", exc_info=True
                )
            raise
        except Exception:
            # Never let the loop die: a loop that exits on one bad flush stops
            # draining forever, which is the bug this exists to fix.
            logger.warning("attendance drain loop iteration failed", exc_info=True)


def start_drain(loop=None) -> None:
    """Start the periodic drain. Idempotent; safe to call from startup."""
    global _DRAIN_TASK
    import asyncio

    if _DRAIN_TASK is not None and not _DRAIN_TASK.done():
        return
    try:
        _DRAIN_TASK = asyncio.get_running_loop().create_task(_drain_loop())
        logger.info(
            "Attendance drain started (every %ds)", config.ATTENDANCE_FLUSH_SECONDS
        )
    except RuntimeError:
        # No running loop (a script, a test). Not an error: the drain is only
        # meaningful inside the served app.
        logger.debug("No running loop; attendance drain not started")


async def stop_drain() -> None:
    """Cancel the drain and flush what is buffered. For shutdown."""
    global _DRAIN_TASK
    import asyncio

    task, _DRAIN_TASK = _DRAIN_TASK, None
    if task is None or task.done():
        # Still flush: the task may never have started, and the rows are real.
        flush()
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def drain_status() -> dict:
    """Whether attendance is actually being written, for /health.

    The one failure mode the rest of the app cannot see. `/health` reports
    that the process serves and the database answers, and both stay true while
    the drain is dead — observations would pile up in memory, be capped at
    ATTENDANCE_BUFFER_MAX, and vanish on the next restart, with nothing
    anywhere saying so.

    `buffered` is the number worth watching: it should sit near zero and fall
    back to it every flush interval. A number that only grows means the writes
    are not happening, whatever the task says about itself.
    """
    running = _DRAIN_TASK is not None and not _DRAIN_TASK.done()

    last_flush_age = None
    if _LAST_FLUSH:
        last_flush_age = int(
            datetime.now(timezone.utc).timestamp() - _LAST_FLUSH
        )

    # Stale only counts once there is something waiting: an idle instance with
    # an empty buffer legitimately never flushes, and calling that unhealthy
    # would cry wolf every night and every quiet lunchtime.
    stale = bool(
        _PENDING
        and last_flush_age is not None
        and last_flush_age > config.ATTENDANCE_FLUSH_SECONDS * 5
    )

    return {
        "enabled": bool(config.ATTENDANCE_ENABLED),
        "drain_running": running,
        "buffered": len(_PENDING),
        "buffer_max": config.ATTENDANCE_BUFFER_MAX,
        "last_flush_age_seconds": last_flush_age,
        # The single answer a supervisor or a human should read.
        "healthy": (not config.ATTENDANCE_ENABLED) or (running and not stale),
    }


def reset_for_tests() -> None:
    """Clear all in-process state. Tests only."""
    global _LAST_FLUSH, _DRAIN_TASK
    _LAST_SEEN.clear()
    del _PENDING[:]
    _LAST_FLUSH = 0.0
    _DRAIN_TASK = None

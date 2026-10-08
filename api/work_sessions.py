"""The work-session tracker: who worked on which task, for how long, and how many
shapes it held at each end.

Design and rationale: `.devnotes/feature/team-monitoring/02_DESIGN.md`. This is
the attendance buffer's pattern (`api/attendance.py`) applied to a different
question, and it keeps that module's hard-won rules:

- **No I/O on the request path.** `note_time()` / `note_save()` are dict
  operations under a lock held for microseconds. They never touch the database,
  never log, never call out.
- **Write-behind.** `flush()` runs on the app's own clock (`start_drain`),
  checkpoints the live stretches once a minute, and is the only database work.
- **Never raises into a request.** Every entry point is wrapped; failures are
  logged with a traceback (CLAUDE.md rule 3), never swallowed.
- **Monitoring may never break annotation.** The hooks sit *after* the commit of
  the request they ride, so nothing here can delay or roll back a save.

**Why a lock when attendance uses none.** Attendance appends a tuple (atomic
under the GIL). Here a ping and a save for one user run in different threadpool
threads and each does read-modify-write on a shared record (`seconds += n`,
"close the old stretch, open a new one"), which the GIL does not make atomic.
So `_LOCK` guards both containers, and the rule beside it is absolute: **dict
operations only inside the lock — no I/O, no logging, no calls out.** `flush`
swaps data out under it and releases it before touching the database.

**Single worker only** (CLAUDE.md rule 9). `_LIVE` and `_CLOSED` are in-process,
like `JOBS`, `_models`, `_TASK_LOCKS` and `attendance._PENDING`. Under two
workers each would see half the pings and split stretches — quietly wrong. Do
not add `--workers N`.

A crash or power cut loses at most one flush interval of the open stretches.
"""
import asyncio
import logging
import threading
from datetime import datetime, timezone

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

import config
import models
from database import SessionLocal, commit_with_retry
from logging_service import log_event

logger = logging.getLogger(__name__)

# (user_id, task_id) -> the stretch currently in progress for that pair.
_LIVE: dict = {}

# Stretches that ended (gap elapsed) and still owe their final write.
_CLOSED: list = []

# Guards _LIVE and _CLOSED. Dict operations only while held (see module note).
_LOCK = threading.Lock()

# Serialises whole flushes (drain loop vs shutdown). Only the flusher takes it;
# a request never does.
_FLUSH_LOCK = threading.Lock()

_LAST_FLUSH = 0.0
_DRAIN_TASK = None
_LAST_FLUSH_ROWS = 0


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _gap_seconds() -> float:
    return float(config.MONITOR_SESSION_GAP_SECONDS)


def _touch(user_id, task_id, now) -> dict:
    """The live stretch for this pair, opening a new one after a long enough gap.

    Caller holds `_LOCK`. `>=`, not `>`: a silence of exactly the threshold ends
    the stretch (the off-by-one `attendance_report.py` documents and fixed).
    """
    key = (user_id, task_id)
    rec = _LIVE.get(key)
    if rec is not None and (now - rec["last_at"]).total_seconds() >= _gap_seconds():
        _CLOSED.append(rec)
        rec = None
    if rec is None:
        rec = {
            "row_id": None,
            "user_id": user_id,
            "task_id": task_id,
            "started_at": now,
            "last_at": now,
            "seconds": 0,
            "objects_start": None,
            "objects_end": None,
            "dirty": True,
        }
        _LIVE[key] = rec
    rec["last_at"] = now
    rec["dirty"] = True
    return rec


def _trim_locked() -> None:
    """Bound memory: drop the oldest *closed* stretches past the cap."""
    overflow = len(_LIVE) + len(_CLOSED) - config.MONITOR_BUFFER_MAX
    if overflow > 0 and _CLOSED:
        drop = min(overflow, len(_CLOSED))
        del _CLOSED[:drop]
        logger.warning(
            "work-session buffer over %d; dropped %d oldest closed stretches. "
            "Flushes may not be firing.", config.MONITOR_BUFFER_MAX, drop,
        )


def note_time(user_id, task_id, seconds, now=None) -> None:
    """Credit `seconds` of active timer time to (user, task). Never raises.

    Called by the timer ping after its seconds were banked. A zero or negative
    delta means the timer was not running, so it neither credits time nor
    extends the stretch.
    """
    if not config.MONITOR_ENABLED:
        return
    try:
        if user_id is None or task_id is None:
            return
        seconds = int(seconds or 0)
        if seconds <= 0:
            return
        now = now or _now()
        with _LOCK:
            rec = _touch(user_id, task_id, now)
            rec["seconds"] += seconds
            _trim_locked()
    except Exception:  # pragma: no cover - monitoring must never break a request
        logger.warning("work_sessions.note_time failed", exc_info=True)


def note_save(user_id, task_id, objects_prev, objects_now, now=None) -> None:
    """Record an *accepted* save: the count before it and the count after.

    `objects_start` is set once, by the first save of the stretch (the count
    before the stretch's first edit); `objects_end` follows every save. Either
    count may be None when the caller could not measure it, and None is kept as
    "unknown" rather than turned into 0.
    """
    if not config.MONITOR_ENABLED:
        return
    try:
        if user_id is None or task_id is None:
            return
        now = now or _now()
        with _LOCK:
            rec = _touch(user_id, task_id, now)
            if rec["objects_start"] is None:
                rec["objects_start"] = objects_prev
            if objects_now is not None:
                rec["objects_end"] = objects_now
            _trim_locked()
    except Exception:  # pragma: no cover - monitoring must never break a request
        logger.warning("work_sessions.note_save failed", exc_info=True)


def last_flush_at():
    """When the tracker last checkpointed, or None. Lets the page say 'as of'."""
    return datetime.fromtimestamp(_LAST_FLUSH, tz=timezone.utc) if _LAST_FLUSH else None


def live_for_users(user_ids=None) -> list:
    """Read-only view of the stretches in progress, for a 'working now' hint.

    Returns copies; sums nothing, so it cannot double-count with stored rows.
    """
    with _LOCK:
        recs = [dict(r) for r in _LIVE.values()]
    if user_ids is not None:
        recs = [r for r in recs if r["user_id"] in user_ids]
    return recs


# --- Flush ------------------------------------------------------------------


def _take_batch(now):
    """Under `_LOCK`: close idle stretches and collect everything owing a write.

    Returns `[(rec, snapshot)]`. `rec` is the live dict (so a new row id can be
    stored back on it); `snapshot` is an immutable-by-us copy to write.
    """
    batch = []
    with _LOCK:
        gap = _gap_seconds()
        for key in [k for k, r in _LIVE.items()
                    if (now - r["last_at"]).total_seconds() >= gap]:
            _CLOSED.append(_LIVE.pop(key))
        for rec in _LIVE.values():
            if rec["dirty"]:
                rec["dirty"] = False
                batch.append((rec, dict(rec), False))
        for rec in _CLOSED:
            batch.append((rec, dict(rec), True))
        del _CLOSED[:]
    return batch


def _fill_counts(db, batch) -> None:
    """One grouped COUNT for stretches that never saw a save (look-only).

    This is the only COUNT the feature runs, and it runs here, off the request
    path. A failure leaves the counts None ("not measured"), never 0.
    """
    need = {s["task_id"] for _, s, _ in batch if s["objects_start"] is None}
    if not need:
        return
    try:
        counts = dict(
            db.query(models.Annotation.task_id, func.count(models.Annotation.id))
            .filter(models.Annotation.task_id.in_(need))
            .group_by(models.Annotation.task_id)
            .all()
        )
    except Exception:
        db.rollback()
        logger.warning("work-session object count failed; leaving NULL", exc_info=True)
        return
    for rec, snap, _ in batch:
        if snap["objects_start"] is not None:
            continue
        n = counts.get(snap["task_id"], 0)
        snap["objects_start"] = n
        if snap["objects_end"] is None:
            snap["objects_end"] = n
        with _LOCK:  # first writer wins: do not overwrite a save that raced us
            if rec["objects_start"] is None:
                rec["objects_start"] = n
            if rec["objects_end"] is None:
                rec["objects_end"] = n


def _task_names(db, batch) -> dict:
    ids = {s["task_id"] for _, s, _ in batch if s["row_id"] is None}
    if not ids:
        return {}
    try:
        return dict(
            db.query(models.Task.id, models.Task.description)
            .filter(models.Task.id.in_(ids))
            .all()
        )
    except Exception:
        db.rollback()
        logger.warning("work-session task-name lookup failed", exc_info=True)
        return {}


def _write(db, batch, names, drop_task_id=False) -> list:
    """Insert new stretches, update known ones. Returns `[(rec, row)]` for inserts."""
    inserted = []
    updates = []
    for rec, s, _ in batch:
        if s["row_id"] is None:
            name = names.get(s["task_id"])
            row = models.WorkSession(
                user_id=s["user_id"],
                task_id=None if drop_task_id else s["task_id"],
                task_name=(name or None) and name[:255],
                started_at=s["started_at"],
                last_at=s["last_at"],
                active_seconds=s["seconds"],
                objects_start=s["objects_start"],
                objects_end=s["objects_end"],
            )
            db.add(row)
            inserted.append((rec, row))
        else:
            updates.append({
                "id": s["row_id"],
                "last_at": s["last_at"],
                "active_seconds": s["seconds"],
                "objects_start": s["objects_start"],
                "objects_end": s["objects_end"],
            })
    if updates:
        db.bulk_update_mappings(models.WorkSession, updates)
    db.flush()  # assigns ids to the inserts
    return inserted


def _redirty(batch) -> None:
    """After a failed write, keep live stretches (their state is cumulative, so
    the next flush rewrites them whole). Closed ones are dropped: re-queueing
    them would let a persistent fault grow the buffer forever."""
    with _LOCK:
        for rec, _, closed in batch:
            if not closed:
                rec["dirty"] = True


def flush(now=None) -> int:
    """Checkpoint the tracker to `work_sessions`. Returns the rows written.

    Never call from inside a request; it is blocking database work.
    """
    global _LAST_FLUSH, _LAST_FLUSH_ROWS
    if not config.MONITOR_ENABLED:
        return 0
    with _FLUSH_LOCK:
        now = now or _now()
        batch = _take_batch(now)
        _LAST_FLUSH = now.timestamp()
        if not batch:
            return 0

        started = _now()
        db = SessionLocal()
        try:
            _fill_counts(db, batch)
            names = _task_names(db, batch)
            try:
                inserted = _write(db, batch, names)
                commit_with_retry(db)
            except IntegrityError:
                # A task (or user) was deleted while its stretch sat in the
                # buffer, so the FK rejected the INSERT. The task reference is
                # decoration; the fact that someone worked is the data. Keep the
                # row without it (the 2026-09-24 attendance incident lost a
                # whole batch to exactly this).
                db.rollback()
                inserted = _write(db, batch, names, drop_task_id=True)
                commit_with_retry(db)
                logger.warning(
                    "work-session flush retried without task_id; kept %d rows",
                    len(batch),
                )
                log_event("monitor.flush_degraded", level="WARNING", rows=len(batch))
            with _LOCK:
                for rec, row in inserted:
                    rec["row_id"] = row.id
        except Exception:
            db.rollback()
            _redirty(batch)
            logger.error(
                "work-session flush failed; %d stretches not written",
                len(batch), exc_info=True,
            )
            log_event("monitor.flush_failed", level="ERROR", rows=len(batch))
            return 0
        finally:
            db.close()

        _LAST_FLUSH_ROWS = len(batch)
        ms = int((_now() - started).total_seconds() * 1000)
        log_event("monitor.flush", rows=len(batch), ms=ms)
        return len(batch)


# --- The drain ----------------------------------------------------------------


async def _drain_loop() -> None:
    while True:
        try:
            await asyncio.sleep(config.MONITOR_FLUSH_SECONDS)
            if not config.MONITOR_ENABLED:
                continue
            # to_thread: flush() is blocking database work and this is the event loop.
            await asyncio.to_thread(flush)
        except asyncio.CancelledError:
            try:
                flush(now=_now())
            except Exception:
                logger.warning("work-session final flush failed", exc_info=True)
            raise
        except Exception:
            # A loop that exits on one bad flush stops draining forever.
            logger.warning("work-session drain iteration failed", exc_info=True)


def start_drain() -> None:
    """Start the periodic drain. Idempotent; a no-op when monitoring is off."""
    global _DRAIN_TASK
    if not config.MONITOR_ENABLED:
        return
    if _DRAIN_TASK is not None and not _DRAIN_TASK.done():
        return
    try:
        _DRAIN_TASK = asyncio.get_running_loop().create_task(_drain_loop())
        logger.info("Work-session drain started (every %ds)", config.MONITOR_FLUSH_SECONDS)
    except RuntimeError:
        logger.debug("No running loop; work-session drain not started")


async def stop_drain() -> None:
    """Cancel the drain and write what is held. For shutdown."""
    global _DRAIN_TASK
    task, _DRAIN_TASK = _DRAIN_TASK, None
    if task is None or task.done():
        # The drain never ran (or already ended); still write what is held.
        if config.MONITOR_ENABLED:
            flush()
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def drain_status() -> dict:
    """For /health: is the tracker actually being written?"""
    running = _DRAIN_TASK is not None and not _DRAIN_TASK.done()
    age = int(_now().timestamp() - _LAST_FLUSH) if _LAST_FLUSH else None
    with _LOCK:
        live, closed = len(_LIVE), len(_CLOSED)
    stale = bool(
        (live or closed) and age is not None
        and age > config.MONITOR_FLUSH_SECONDS * 5
    )
    return {
        "enabled": bool(config.MONITOR_ENABLED),
        "drain_running": running,
        "live": live,
        "buffered_closed": closed,
        "buffer_max": config.MONITOR_BUFFER_MAX,
        "last_flush_age_seconds": age,
        "healthy": (not config.MONITOR_ENABLED) or (running and not stale),
    }


def reset_for_tests() -> None:
    """Clear all in-process state. Tests only."""
    global _LAST_FLUSH, _DRAIN_TASK, _LAST_FLUSH_ROWS
    with _LOCK:
        _LIVE.clear()
        del _CLOSED[:]
    _LAST_FLUSH = 0.0
    _LAST_FLUSH_ROWS = 0
    _DRAIN_TASK = None

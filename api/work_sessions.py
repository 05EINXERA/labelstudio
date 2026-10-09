"""In-memory work-session tracker for team activity monitoring.

The task save calls `note_save` after its final commit. That call does dict
operations under a lock and nothing else: no query, no I/O, no logging inside
the lock. A background drain checkpoints the held state to `work_sessions` once
a minute, off the event loop, so the save and its transaction are untouched.

State is process-local (CLAUDE.md rule 9): under more than one worker stretches
would silently split. Never raise into a request -- monitoring may never break
annotation. Imports no router, so routers can import this module freely.

Design and rationale: .devnotes/team-monitoring/01_DESIGN.md.
"""
import asyncio
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

import config
import models
from database import SessionLocal, commit_with_retry

logger = logging.getLogger(__name__)

# (user_id, task_id) -> record. Records move to _CLOSED once their gap elapses.
_LIVE: Dict[Tuple[int, int], dict] = {}
_CLOSED: List[dict] = []
# Guards _LIVE/_CLOSED/_LAST_FLUSH_AT. Held for dict operations only.
_LOCK = threading.Lock()
# Serialises whole flushes; only the flusher takes it.
_FLUSH_LOCK = threading.Lock()
_LAST_FLUSH_AT: Optional[datetime] = None
_DRAIN_TASK: Optional["asyncio.Task"] = None

# Comments live in the annotations table but are not "objects".
_NOT_AN_OBJECT = "comment"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _gap() -> timedelta:
    return timedelta(seconds=config.MONITOR_SESSION_GAP_SECONDS)


def _touch(user_id: int, member_name: str, task_id: int, now: datetime, seconds: int) -> dict:
    """The live record for this pair, opening a new stretch if needed.

    Caller holds _LOCK. A silence of exactly the gap ends the stretch (>=, not >).
    """
    key = (user_id, task_id)
    rec = _LIVE.get(key)
    if rec is not None and (now - rec["last_at"]) >= _gap():
        _CLOSED.append(rec)
        rec = None
    if rec is None:
        # The event arrives after the work it reports (the client drains every
        # ~30 s), so back-date the start by the seconds it carries, capped at the
        # gap, rather than skewing the clock period later than reality.
        back = min(max(seconds, 0), config.MONITOR_SESSION_GAP_SECONDS)
        rec = {
            "row_id": None, "user_id": user_id, "member_name": member_name,
            "task_id": task_id, "started_at": now - timedelta(seconds=back),
            "last_at": now, "seconds": 0, "objects_start": None,
            "objects_end": None, "dirty": False,
        }
        _LIVE[key] = rec
    rec["last_at"] = now
    rec["dirty"] = True
    return rec


def note_save(
    user_id: Optional[int],
    member_name: Optional[str],
    task_id: Optional[int],
    seconds: Optional[int],
    objects_prev: Optional[int],
    objects_now: Optional[int],
    now: Optional[datetime] = None,
) -> None:
    """Record one accepted save. Call only after the save's final commit.

    `seconds` is the save's time_spent_delta; `objects_prev`/`objects_now` are
    the task's non-comment annotation count before/after, None when the save
    carried no annotation set. A save that carried neither time nor counts is not
    work and is ignored (a zero delta means the timer was not running).
    """
    if not config.MONITOR_ENABLED:
        return
    try:
        if user_id is None or task_id is None or not member_name:
            return
        secs = int(seconds) if seconds else 0
        if secs <= 0 and objects_now is None:
            return
        now = now or _now()
        trimmed = False
        with _LOCK:
            rec = _touch(user_id, member_name, task_id, now, secs)
            if secs > 0:
                rec["seconds"] += secs
            # First writer wins for the start; every save moves the end.
            if rec["objects_start"] is None and objects_prev is not None:
                rec["objects_start"] = objects_prev
            if objects_now is not None:
                rec["objects_end"] = objects_now
            if len(_LIVE) + len(_CLOSED) > config.MONITOR_BUFFER_MAX:
                excess = len(_LIVE) + len(_CLOSED) - config.MONITOR_BUFFER_MAX
                del _CLOSED[:excess]
                trimmed = True
        if trimmed:
            logger.warning("Work-session buffer over %d; dropped oldest closed stretches", config.MONITOR_BUFFER_MAX)
    except Exception:
        logger.exception("Work-session tracking failed; save is unaffected")


def _task_info(db, task_ids) -> Dict[int, str]:
    """task_id -> display name for tasks that still exist (one query)."""
    if not task_ids:
        return {}
    rows = db.query(models.Task.id, models.Task.description, models.Task.image_path).filter(
        models.Task.id.in_(task_ids)
    ).all()
    out = {}
    for tid, description, image_path in rows:
        name = description or (os.path.basename(image_path) if image_path else None) or f"Task {tid}"
        out[tid] = name[:255]
    return out


def flush(now: Optional[datetime] = None) -> int:
    """Checkpoint held state to the database. Never call inside a request.

    Returns the number of rows written (inserted or updated). Failures are
    logged and swallowed: live stretches are re-marked dirty (their state is
    cumulative, so the next flush rewrites them whole); closed ones are dropped,
    because re-queuing them would grow the buffer without bound under a
    persistent fault.
    """
    global _LAST_FLUSH_AT
    if not config.MONITOR_ENABLED:
        return 0
    with _FLUSH_LOCK:
        now = now or _now()
        with _LOCK:
            for key in [k for k, r in _LIVE.items() if (now - r["last_at"]) >= _gap()]:
                _CLOSED.append(_LIVE.pop(key))
            batch = []
            for rec in _LIVE.values():
                if rec["dirty"]:
                    rec["dirty"] = False
                    batch.append((rec, dict(rec), False))
            for rec in _CLOSED:
                batch.append((rec, dict(rec), True))
            _CLOSED.clear()
        if not batch:
            with _LOCK:
                _LAST_FLUSH_AT = now
            return 0

        db = SessionLocal()
        try:
            written = _write_batch(db, batch)
        except Exception:
            db.rollback()
            with _LOCK:
                for rec, _snap, closed in batch:
                    if not closed:
                        rec["dirty"] = True
            logger.exception("Work-session flush failed; will retry live stretches")
            return 0
        finally:
            db.close()
        with _LOCK:
            _LAST_FLUSH_AT = now
        return written


def _write_batch(db, batch) -> int:
    task_ids = {snap["task_id"] for _r, snap, _c in batch if snap["task_id"] is not None}
    names = _task_info(db, task_ids)

    # Stretches that never saw an annotation set (look-only visits) have no
    # counts; fill them with ONE grouped COUNT. Only for tasks that still exist:
    # a deleted task has no count, and 0 would claim it was empty. On failure the
    # counts stay NULL.
    need = {s["task_id"] for _r, s, _c in batch if s["objects_start"] is None and s["task_id"] in names}
    counts: Dict[int, int] = {}
    if need:
        try:
            rows = db.query(models.Annotation.task_id, func.count(models.Annotation.id)).filter(
                models.Annotation.task_id.in_(need), models.Annotation.type != _NOT_AN_OBJECT,
            ).group_by(models.Annotation.task_id).all()
            counts = {tid: n for tid, n in rows}
            counts = {tid: counts.get(tid, 0) for tid in need}
        except Exception:
            logger.exception("Work-session look-only count failed; leaving counts NULL")
            db.rollback()

    for rec, snap, _closed in batch:
        tid = snap["task_id"]
        if snap["objects_start"] is None and tid in counts:
            snap["objects_start"] = counts[tid]
            if snap["objects_end"] is None:
                snap["objects_end"] = counts[tid]
            # Write back only where still unset: a save may have raced in.
            with _LOCK:
                if rec["objects_start"] is None:
                    rec["objects_start"] = snap["objects_start"]
                if rec["objects_end"] is None:
                    rec["objects_end"] = snap["objects_end"]

    def persist(null_refs: bool) -> List[Tuple[dict, models.WorkSession]]:
        inserted = []
        updates = []
        for rec, snap, _closed in batch:
            if snap["row_id"] is None:
                row = models.WorkSession(
                    user_id=None if null_refs else snap["user_id"],
                    member_name=snap["member_name"],
                    task_id=None if null_refs else snap["task_id"],
                    task_name=names.get(snap["task_id"]),
                    started_at=snap["started_at"], last_at=snap["last_at"],
                    active_seconds=snap["seconds"],
                    objects_start=snap["objects_start"], objects_end=snap["objects_end"],
                )
                db.add(row)
                inserted.append((rec, row))
            else:
                updates.append({
                    "id": snap["row_id"], "last_at": snap["last_at"],
                    "active_seconds": snap["seconds"],
                    "objects_start": snap["objects_start"], "objects_end": snap["objects_end"],
                })
        if updates:
            db.bulk_update_mappings(models.WorkSession, updates)
        db.flush()
        commit_with_retry(db)
        return inserted

    try:
        inserted = persist(null_refs=False)
    except IntegrityError:
        # A task (or user) was deleted while its record sat in memory. The
        # reference is decoration; the fact someone worked is the data.
        db.rollback()
        logger.warning("Work-session flush hit a dangling task/user reference; retrying without it")
        inserted = persist(null_refs=True)

    with _LOCK:
        for rec, row in inserted:
            rec["row_id"] = row.id
    return len(batch)


async def _drain_loop() -> None:
    interval = config.MONITOR_FLUSH_SECONDS
    try:
        while True:
            await asyncio.sleep(interval)
            try:
                await asyncio.to_thread(flush)
            except Exception:
                logger.exception("Work-session drain iteration failed")
    except asyncio.CancelledError:
        # One last checkpoint so a clean shutdown loses nothing.
        try:
            await asyncio.to_thread(flush)
        except Exception:
            logger.exception("Work-session final flush failed")
        raise


def start_drain() -> None:
    """Start the background checkpoint task. Call from the running event loop."""
    global _DRAIN_TASK
    if not config.MONITOR_ENABLED or (_DRAIN_TASK and not _DRAIN_TASK.done()):
        return
    _DRAIN_TASK = asyncio.get_running_loop().create_task(_drain_loop())
    logger.info("Work-session monitoring enabled (flush every %ds, gap %ds)",
                config.MONITOR_FLUSH_SECONDS, config.MONITOR_SESSION_GAP_SECONDS)


async def stop_drain() -> None:
    """Cancel the drain and flush what is held."""
    global _DRAIN_TASK
    task, _DRAIN_TASK = _DRAIN_TASK, None
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def last_flush_at() -> Optional[datetime]:
    with _LOCK:
        return _LAST_FLUSH_AT


def drain_status() -> dict:
    """Snapshot for /health. Healthy when off, or running and not stale with data."""
    enabled = bool(config.MONITOR_ENABLED)
    running = bool(_DRAIN_TASK and not _DRAIN_TASK.done())
    with _LOCK:
        live, closed = len(_LIVE), len(_CLOSED)
        last = _LAST_FLUSH_AT
        has_data = closed > 0 or any(r["dirty"] for r in _LIVE.values())
    age = None if last is None else max(0.0, (_now() - last).total_seconds())
    stale = has_data and age is not None and age > 3 * config.MONITOR_FLUSH_SECONDS
    return {
        "enabled": enabled, "drain_running": running, "live": live,
        "buffered_closed": closed, "buffer_max": config.MONITOR_BUFFER_MAX,
        "last_flush_age_seconds": age,
        "healthy": (not enabled) or (running and not stale),
    }


def reset_for_tests() -> None:
    global _LAST_FLUSH_AT
    with _LOCK:
        _LIVE.clear()
        _CLOSED.clear()
        _LAST_FLUSH_AT = None

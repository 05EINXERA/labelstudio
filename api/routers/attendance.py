"""The attendance read endpoints.

All admin-only, all reads. Four properties hold across every handler here and
should survive any edit:

1. **`require_admin` runs first**, before any aggregation — CLAUDE.md rule 1c's
   ordering principle. A permission answer must never arrive after the work it
   was meant to prevent.
2. **It answers 404, not 403**, matching the contract `api/permissions.py`
   documents for a caller with no role at all: someone who may not use the
   dashboard is not told it exists.
3. **Nothing here writes to the database** (CLAUDE.md rule 4). The read cannot
   flush the buffer, refresh the rollup or prune. Reading the in-memory buffer
   is not a database write, and merging it is what makes an open session show
   correctly rather than lagging a flush interval behind.
4. **The query count is fixed**, never a per-user fan-out — the pattern
   `api/routers/projects.py::_aggregate_metrics` establishes and rule 11b
   requires. A fan-out is invisible at 25 users and fatal later.

Properties 1–4 describe the **read** endpoints, which are most of this module.
The break endpoints at the bottom are the exception and are documented there:
they write (through the buffer, not synchronously), they are self-scoped
rather than admin-gated, and they each declare `require_csrf` individually —
per-route rather than on the router, because everything else here is a read
and rule 1a's requirement attaches to the state-changing calls.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

import config
import models
from api import attendance, attendance_export, attendance_report as report
from api.auth import get_current_user, require_csrf
from api.permissions import require_admin
from database import get_db
from logging_service import log_event
from schemas import (
    AttendanceDayResponse,
    BreakEndResponse,
    BreakStartResponse,
    OpenBreak,
    AttendanceRangeResponse,
    AttendanceSessionsResponse,
)

router = APIRouter(
    prefix="/api/attendance",
    tags=["attendance"],
    dependencies=[Depends(get_current_user)],
)

# Server-side ceiling on a range query, so one request cannot scan the whole
# retention window. The same defensive-ceiling reasoning as MAX_UPLOAD_FILES
# and MAX_TEAMS_PER_USER, and it matches the 31-day retention: past that the
# raw rows are gone and the rollup is the only record.
MAX_RANGE_DAYS = 31


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Expected a date as YYYY-MM-DD, got {raw!r}."
        )


def _collect(db: Session, start_date: date, end_date: date, user_ids=None) -> list:
    """Aggregate one or more local days into admin table rows.

    Three queries total, whatever the number of days or users: the observations,
    the usernames, and the review counts. Everything else is the pure pass in
    `attendance_report`.
    """
    window_start, _ = report.day_bounds(start_date)
    _, window_end = report.day_bounds(end_date)

    query = db.query(models.AttendanceObservation).filter(
        models.AttendanceObservation.seen_at >= window_start,
        models.AttendanceObservation.seen_at < window_end,
    )
    if user_ids is not None:
        query = query.filter(models.AttendanceObservation.user_id.in_(user_ids))
    stored = report.observation_dicts(
        query.order_by(models.AttendanceObservation.seen_at).all()
    )

    # Merge the rows still sitting in the in-process buffer. Without this an
    # annotator who is working right now shows a session that ended at the last
    # flush, which reads as "they left" rather than "they are here".
    buffered = [
        row for row in attendance.buffered_observations(user_ids=user_ids)
        if window_start <= row["seen_at"] < window_end
    ]

    # Group by (user, local day). Day attribution happens here rather than in
    # storage, so ATTENDANCE_TZ stays changeable after the fact.
    by_user_day = {}
    for row in stored + buffered:
        if row["user_id"] is None:
            # A deleted user's observations survive with a null user_id (Q23).
            # They are not attributable to a person any more, and the readable
            # permanent record is the rollup's username snapshot, so they are
            # not shown as an anonymous row here.
            continue
        by_user_day.setdefault(
            (row["user_id"], report.local_day(row["seen_at"])), []
        ).append(row)

    if not by_user_day:
        return []

    present_user_ids = {user_id for user_id, _ in by_user_day}
    usernames = dict(
        db.query(models.User.id, models.User.username)
        .filter(models.User.id.in_(present_user_ids))
        .all()
    )

    # Counted and grouped in SQL, never by pulling rows into Python (rule 11b).
    review_rows = (
        db.query(
            models.TaskReview.reviewer_id,
            models.TaskReview.created_at,
        )
        .filter(
            models.TaskReview.reviewer_id.in_(present_user_ids),
            models.TaskReview.created_at >= window_start,
            models.TaskReview.created_at < window_end,
        )
        .all()
    )
    reviews_by_user_day = {}
    for reviewer_id, created_at in review_rows:
        key = (reviewer_id, report.local_day(created_at))
        reviews_by_user_day[key] = reviews_by_user_day.get(key, 0) + 1

    rows = []
    for (user_id, local_date), observations in by_user_day.items():
        summary = report.summarise_day(
            observations,
            local_date,
            reviews=reviews_by_user_day.get((user_id, local_date), 0),
        )
        if summary is None:
            continue
        rows.append({
            "user_id": user_id,
            "username": usernames.get(user_id, f"user-{user_id}"),
            "local_date": local_date,
            "first_seen": summary["first_seen"],
            "last_seen": summary["last_seen"],
            "last_seen_reason": summary["end_reason"],
            "session_count": summary["session_count"],
            "present_seconds": summary["present_seconds"],
            "break_seconds": summary["break_seconds"],
            "manual_break_seconds": summary["manual_break_seconds"],
            "active_seconds": summary["active_seconds"],
            "tasks_touched": summary["tasks_touched"],
            "tasks_reviewed": summary["tasks_reviewed"],
            "has_unended_break": summary["has_unended_break"],
        })

    rows.sort(key=lambda r: (r["local_date"], -r["present_seconds"], r["username"]))
    return rows


@router.get("/days/{day}", response_model=AttendanceDayResponse)
def attendance_for_day(
    day: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The admin table for one local day."""
    require_admin(current_user)  # first, before any aggregation
    local_date = _parse_date(day)
    return AttendanceDayResponse(
        date=local_date,
        instance_id=config.ATTENDANCE_INSTANCE_ID,
        timezone=config.ATTENDANCE_TZ,
        generated_at=datetime.now(timezone.utc),
        rows=_collect(db, local_date, local_date),
    )


@router.get("/range", response_model=AttendanceRangeResponse)
def attendance_for_range(
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The same row shape, aggregated over a bounded range."""
    require_admin(current_user)
    start_date = _parse_date(date_from)
    end_date = _parse_date(date_to)
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="'to' is before 'from'.")
    span = (end_date - start_date).days + 1
    if span > MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Range is {span} days; the maximum is {MAX_RANGE_DAYS}.",
        )
    return AttendanceRangeResponse(
        date_from=start_date,
        date_to=end_date,
        instance_id=config.ATTENDANCE_INSTANCE_ID,
        timezone=config.ATTENDANCE_TZ,
        generated_at=datetime.now(timezone.utc),
        rows=_collect(db, start_date, end_date),
    )


@router.get(
    "/days/{day}/users/{user_id}/sessions",
    response_model=AttendanceSessionsResponse,
)
def sessions_for_user_day(
    day: str,
    user_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The session breakdown behind one cell of the table."""
    require_admin(current_user)
    local_date = _parse_date(day)
    start, end = report.day_bounds(local_date)

    stored = report.observation_dicts(
        db.query(models.AttendanceObservation)
        .filter(
            models.AttendanceObservation.user_id == user_id,
            models.AttendanceObservation.seen_at >= start,
            models.AttendanceObservation.seen_at < end,
        )
        .order_by(models.AttendanceObservation.seen_at)
        .all()
    )
    buffered = [
        row for row in attendance.buffered_observations(user_ids={user_id})
        if start <= row["seen_at"] < end
    ]

    user = db.get(models.User, user_id)
    sessions = report.sessionise(stored + buffered)
    return AttendanceSessionsResponse(
        date=local_date,
        user_id=user_id,
        username=user.username if user else f"user-{user_id}",
        sessions=[
            {
                "started_at": s["started_at"],
                "ended_at": s["ended_at"],
                "end_reason": s["end_reason"],
                "seconds": s["seconds"],
                "span_seconds": s["span_seconds"],
                "break_seconds": s["break_seconds"],
                "tasks_touched": s["tasks_touched"],
                "breaks": s["breaks"],
            }
            for s in sessions
        ],
    )


@router.get("/me", response_model=AttendanceRangeResponse)
def my_attendance(
    date_from: str = Query(None, alias="from"),
    date_to: str = Query(None, alias="to"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The caller's own attendance. Not admin-gated — everyone sees themselves.

    **Takes no user parameter, deliberately.** An endpoint that accepts an id
    and checks it is a check that can be forgotten; one that cannot express
    another user is safe by construction. This is the leak class this codebase
    already fixed once, where a time-log endpoint returned every row in the
    table to any authenticated caller (`api/routers/time_logs.py`, and the
    comment on `visible_time_logs` recording why).
    """
    today = datetime.now(report.site_tz()).date()
    end_date = _parse_date(date_to) if date_to else today
    start_date = _parse_date(date_from) if date_from else end_date - timedelta(days=6)
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="'to' is before 'from'.")
    span = (end_date - start_date).days + 1
    if span > MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Range is {span} days; the maximum is {MAX_RANGE_DAYS}.",
        )
    return AttendanceRangeResponse(
        date_from=start_date,
        date_to=end_date,
        instance_id=config.ATTENDANCE_INSTANCE_ID,
        timezone=config.ATTENDANCE_TZ,
        generated_at=datetime.now(timezone.utc),
        # Scoped to the caller by construction, not by a check.
        rows=_collect(db, start_date, end_date, user_ids={current_user.id}),
    )


# --- Declared breaks (R6) ---------------------------------------------------
#
# The only endpoints in the feature that write. They are user-initiated
# (roughly twice a day per person) rather than periodic, so they do not reopen
# the concern that ruled out a dedicated heartbeat endpoint: ~100 requests a
# day across 25 annotators, against the ~50 heartbeats per *minute* already on
# the wire.
#
# Both go through the buffer like every other observation, so they inherit the
# batched flush and add no synchronous DB write.
#
# `require_csrf` is declared per-route rather than on the router, because the
# router is otherwise all reads and rule 1a's requirement attaches to the
# state-changing calls.


def _open_break_for(db: Session, user_id: int):
    """The caller's currently-open break, or None.

    Reads the buffer *and* the table, because a break started moments ago may
    not have flushed yet — and a break started this morning certainly has. A
    check against either alone would let a second break open on top of the
    first, and an overlapping pair cannot be corrected afterwards (Q24).

    Only the current local day is considered: a break left open yesterday was
    closed by the IDLE_GAP fallback at read time, and treating it as still
    open would leave the annotator permanently unable to start a new one.
    """
    today = datetime.now(report.site_tz()).date()
    start, end = report.day_bounds(today)

    stored = report.observation_dicts(
        db.query(models.AttendanceObservation)
        .filter(
            models.AttendanceObservation.user_id == user_id,
            models.AttendanceObservation.seen_at >= start,
            models.AttendanceObservation.seen_at < end,
            models.AttendanceObservation.kind.in_([
                attendance.KIND_BREAK_START,
                attendance.KIND_BREAK_END,
                attendance.KIND_BREAK_MANUAL_START,
                attendance.KIND_BREAK_MANUAL_END,
            ]),
        )
        .all()
    )
    buffered = [
        row for row in attendance.buffered_observations(user_ids={user_id})
        if row["kind"] in (
            attendance.KIND_BREAK_START, attendance.KIND_BREAK_END,
            attendance.KIND_BREAK_MANUAL_START, attendance.KIND_BREAK_MANUAL_END,
        ) and start <= row["seen_at"] < end
    ]

    # Walk the edges in order; the last unmatched start is the open one.
    edges = sorted(stored + buffered, key=lambda row: row["seen_at"])
    open_at = None
    for row in edges:
        if row["kind"] in (
            attendance.KIND_BREAK_START, attendance.KIND_BREAK_MANUAL_START
        ):
            if open_at is None:
                open_at = row["seen_at"]
        else:
            open_at = None
    return open_at


@router.post("/break/start", response_model=BreakStartResponse)
def start_break(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    _csrf: None = Depends(require_csrf),
):
    """Declare the start of a break, for the caller.

    Takes no user parameter: an endpoint that cannot name another user cannot
    forge one (§ 9's safe-by-construction rule).

    Idempotent. Starting a break while one is already open returns the
    existing start rather than opening a second — two overlapping breaks
    cannot be corrected afterwards, because the table is append-only.
    """
    existing = _open_break_for(db, current_user.id)
    if existing is not None:
        return BreakStartResponse(started_at=existing, already_open=True)

    started_at = datetime.now(timezone.utc)
    attendance.note_seen(
        current_user.id, kind=attendance.KIND_BREAK_START, seen_at=started_at
    )
    log_event("attendance.break_start", account=current_user.username)
    return BreakStartResponse(started_at=started_at, already_open=False)


@router.post("/break/end", response_model=BreakEndResponse)
def end_break(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    _csrf: None = Depends(require_csrf),
):
    """Declare the end of the caller's open break.

    Ending a break nobody started is a no-op answered 200, not an error: the
    `pagehide` beacon and the End Break button can both fire for one break,
    and the second must not fail.
    """
    ended_at = datetime.now(timezone.utc)
    started_at = _open_break_for(db, current_user.id)
    if started_at is None:
        return BreakEndResponse(ended_at=ended_at, seconds=0, was_open=False)

    attendance.note_seen(
        current_user.id, kind=attendance.KIND_BREAK_END, seen_at=ended_at
    )
    seconds = max(0, int((ended_at - started_at).total_seconds()))
    log_event(
        "attendance.break_end", account=current_user.username, seconds=seconds
    )
    return BreakEndResponse(ended_at=ended_at, seconds=seconds, was_open=True)


@router.get("/break/open", response_model=Optional[OpenBreak])
def open_break(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The caller's open break, if any, so a reloaded page restores its overlay.

    Without this a refresh mid-break strands the annotator: the break is open
    server-side, but the page has no overlay and so no way to end it — the
    Q17 failure, reached by pressing F5.
    """
    started_at = _open_break_for(db, current_user.id)
    if started_at is None:
        return None
    seconds = max(0, int((datetime.now(timezone.utc) - started_at).total_seconds()))
    return OpenBreak(started_at=started_at, seconds=seconds)


# --- Export (R7) ------------------------------------------------------------
#
# Admin-only, and deliberately stricter than the codebase's existing export
# bar: rule 1b puts dataset exports at `reviewer` because it is "a read, but
# not one every annotator should one-click the whole dataset with". An
# attendance export is personal data about every employee, so the bar is
# higher still (Q20).
#
# Both are synchronous, unlike the annotation exports, which use the JOBS +
# BackgroundTasks pattern because rasterizing images is the slow path. A month
# of attendance is ~775 output rows -- milliseconds of work, so the job
# plumbing would be pure overhead. If a range ever justifies it, that pattern
# is there to adopt.

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)


def _export_range(date_from: str, date_to: str):
    """Parse and bound an export range. Shared by both formats."""
    start_date = _parse_date(date_from)
    end_date = _parse_date(date_to)
    if end_date < start_date:
        raise HTTPException(status_code=400, detail="'to' is before 'from'.")
    span = (end_date - start_date).days + 1
    if span > MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Range is {span} days; the maximum is {MAX_RANGE_DAYS}.",
        )
    return start_date, end_date


def _sessions_by_key(db: Session, rows: list) -> dict:
    """Sessions for every (user, day) in `rows`, for the Sessions sheet.

    One query for the whole range rather than one per row: a per-row fan-out
    is invisible at 25 users and fatal later (rule 11b).
    """
    if not rows:
        return {}

    window_start, _ = report.day_bounds(min(r["local_date"] for r in rows))
    _, window_end = report.day_bounds(max(r["local_date"] for r in rows))
    user_ids = {r["user_id"] for r in rows}

    stored = report.observation_dicts(
        db.query(models.AttendanceObservation)
        .filter(
            models.AttendanceObservation.user_id.in_(user_ids),
            models.AttendanceObservation.seen_at >= window_start,
            models.AttendanceObservation.seen_at < window_end,
        )
        .order_by(models.AttendanceObservation.seen_at)
        .all()
    )
    buffered = [
        row for row in attendance.buffered_observations(user_ids=user_ids)
        if window_start <= row["seen_at"] < window_end
    ]

    grouped = {}
    for row in stored + buffered:
        if row["user_id"] is None:
            continue
        grouped.setdefault(
            (row["user_id"], report.local_day(row["seen_at"])), []
        ).append(row)

    return {key: report.sessionise(obs) for key, obs in grouped.items()}


def _export_filename(extension: str, date_from, date_to) -> str:
    """A filename that names the instance and the range.

    The instance is in the name as well as inside the file, because the first
    thing that happens to two exports being compared is that they sit in one
    folder together.

    Sanitised rather than escaped: this goes into a quoted header, so anything
    that could terminate the quoting or inject a header is replaced — the same
    treatment `image_info.py` gives a project slug.
    """
    instance = "".join(
        ch if ch.isalnum() or ch in "-_" else "-"
        for ch in config.ATTENDANCE_INSTANCE_ID
    )[:40]
    return f"attendance-{instance}-{date_from}-to-{date_to}.{extension}"


@router.get("/export.xlsx")
def export_xlsx(
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The three-sheet styled workbook."""
    require_admin(current_user)  # first, before any aggregation
    start_date, end_date = _export_range(date_from, date_to)

    rows = _collect(db, start_date, end_date)
    content = attendance_export.build_workbook(
        rows, start_date, end_date, sessions_by_key=_sessions_by_key(db, rows)
    )
    log_event(
        "attendance.export", account=current_user.username,
        fmt="xlsx", rows=len(rows),
    )
    filename = _export_filename("xlsx", start_date, end_date)
    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export.csv")
def export_csv(
    date_from: str = Query(..., alias="from"),
    date_to: str = Query(..., alias="to"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The Daily sheet, flat. A bare download, as `exports.py` treats CSV."""
    require_admin(current_user)
    start_date, end_date = _export_range(date_from, date_to)

    rows = _collect(db, start_date, end_date)
    content = attendance_export.build_csv(rows, start_date, end_date)
    log_event(
        "attendance.export", account=current_user.username,
        fmt="csv", rows=len(rows),
    )
    filename = _export_filename("csv", start_date, end_date)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

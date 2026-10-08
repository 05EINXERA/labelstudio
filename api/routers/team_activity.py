"""Team activity — the read side of work-session monitoring.

`GET /api/teams/{team_id}/work-sessions/summary` is the owner's table: one row
per (member, task), the member's stretches pivoted together.
`GET /api/teams/{team_id}/work-sessions` lists the stretches behind one row.

Design: `.devnotes/feature/team-monitoring/02_DESIGN.md` § 4. The rules that
shape this file:

- **Permission first** (CLAUDE.md rule 1c): before any aggregation, 404 when the
  caller has no standing at all, 403 when they are a member below manager.
- **GET never writes** (rule 4). The tracker's in-memory state is only read, and
  only for the 'as of' stamp.
- **SQL does the counting** (rule 11b). The pivot is one grouped query with
  window functions (portable to SQLite and Postgres - no DISTINCT ON); Python
  only attaches usernames to at most `MAX_ROWS` rows.
- **Nothing is silently cut**: `truncated` says so; `enabled: false` says
  monitoring is off rather than returning an empty table that reads as 'nobody
  worked'.
"""
import logging
import time
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case, func
from sqlalchemy.orm import Session

import config
import models
from api import attendance_report as report
from api import work_sessions
from api.auth import get_current_user
from api.permissions import TeamRole, is_admin, require_team
from database import get_db
from schemas import (
    IdleMember,
    WorkSessionListResponse,
    WorkSessionRow,
    WorkSessionSummaryResponse,
    WorkSessionSummaryRow,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/teams/{team_id}/work-sessions",
    tags=["team-activity"],
    dependencies=[Depends(get_current_user)],
)

# The summary never returns more rows than this; past it `truncated` is true.
MAX_ROWS = 1000
# The detail list is the stretches behind one (member, task) row.
MAX_DETAIL_ROWS = 200


def _require_viewer(team_id: int, user: models.User, db: Session) -> models.Team:
    """Team owner/manager, or an instance admin for any team.

    Managers already see their members' lifetime hours
    (`time_logs.visible_time_logs`), so this widens nothing they cannot see.
    """
    if is_admin(user):
        team = db.get(models.Team, team_id)
        if team is None:
            raise HTTPException(status_code=404, detail="Team not found")
        return team
    return require_team(team_id, user, db, minimum=TeamRole.MANAGER)


def _parse_date(raw: str) -> date:
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"Expected a date as YYYY-MM-DD, got {raw!r}."
        )


def _range(date_from: Optional[str], date_to: Optional[str]):
    """Resolve and validate the site-local day range -> (from, to, lo_utc, hi_utc)."""
    today = report.local_day(datetime.now(timezone.utc))
    start = _parse_date(date_from) if date_from else today
    end = _parse_date(date_to) if date_to else start
    if end < start:
        raise HTTPException(status_code=400, detail="'to' is before 'from'.")
    span = (end - start).days + 1
    if span > config.MONITOR_MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Range is {span} days; the maximum is {config.MONITOR_MAX_RANGE_DAYS}.",
        )
    lo, _ = report.day_bounds(start)
    _, hi = report.day_bounds(end)
    return start, end, lo, hi


def _members(db: Session, team_id: int) -> dict:
    """{user_id: username} for the team. At most a few dozen rows."""
    return dict(
        db.query(models.TeamMembership.user_id, models.User.username)
        .join(models.User, models.User.id == models.TeamMembership.user_id)
        .filter(models.TeamMembership.team_id == team_id)
        .all()
    )


@router.get("/summary", response_model=WorkSessionSummaryResponse)
def work_session_summary(
    team_id: int,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    user_id: Optional[int] = Query(None),
    q: Optional[str] = Query(None, max_length=100),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    team = _require_viewer(team_id, current_user, db)  # first, before any aggregation
    start, end, lo, hi = _range(date_from, date_to)

    base = dict(
        enabled=bool(config.MONITOR_ENABLED),
        team_id=team.id,
        team_name=team.name,
        timezone=config.ATTENDANCE_TZ,
        date_from=start,
        date_to=end,
        as_of=work_sessions.last_flush_at(),
    )
    if not config.MONITOR_ENABLED:
        return WorkSessionSummaryResponse(**base)

    t0 = time.perf_counter()
    members = _members(db, team.id)
    if user_id is not None:
        if user_id not in members:
            raise HTTPException(status_code=404, detail="Member not found")
        scope = [user_id]
    else:
        scope = list(members)
    if not scope:
        return WorkSessionSummaryResponse(**base)

    WS, Task = models.WorkSession, models.Task
    # Live name wins (a rename shows), the snapshot covers a deleted task.
    name = func.coalesce(Task.description, WS.task_name).label("name")
    stretches = (
        db.query(
            WS.user_id.label("user_id"),
            WS.task_id.label("task_id"),
            name,
            WS.started_at.label("started_at"),
            WS.last_at.label("last_at"),
            WS.active_seconds.label("active_seconds"),
            WS.objects_start.label("objects_start"),
            WS.objects_end.label("objects_end"),
            func.row_number().over(
                partition_by=(WS.user_id, WS.task_id), order_by=WS.started_at.asc()
            ).label("rn_first"),
            func.row_number().over(
                partition_by=(WS.user_id, WS.task_id), order_by=WS.started_at.desc()
            ).label("rn_last"),
        )
        .outerjoin(Task, Task.id == WS.task_id)
        .filter(WS.user_id.in_(scope), WS.started_at >= lo, WS.started_at < hi)
    )
    if q:
        stretches = stretches.filter(
            func.lower(func.coalesce(Task.description, WS.task_name)).contains(
                q.lower(), autoescape=True
            )
        )
    s = stretches.subquery()
    agg = (
        db.query(
            s.c.user_id,
            s.c.task_id,
            func.max(s.c.name).label("name"),
            func.sum(s.c.active_seconds).label("active_seconds"),
            func.count().label("sessions"),
            func.min(s.c.started_at).label("first_start"),
            func.max(s.c.last_at).label("last_end"),
            func.max(case((s.c.rn_first == 1, s.c.objects_start))).label("objects_start"),
            func.max(case((s.c.rn_last == 1, s.c.objects_end))).label("objects_end"),
        )
        .group_by(s.c.user_id, s.c.task_id)
        .order_by(func.max(s.c.last_at).desc())
        .limit(MAX_ROWS + 1)
        .all()
    )
    truncated = len(agg) > MAX_ROWS

    rows = []
    for r in agg[:MAX_ROWS]:
        delta = (
            r.objects_end - r.objects_start
            if r.objects_start is not None and r.objects_end is not None
            else None
        )
        rows.append(WorkSessionSummaryRow(
            user_id=r.user_id,
            username=members.get(r.user_id, "?"),
            task_id=r.task_id,
            task_name=r.name,
            active_seconds=int(r.active_seconds or 0),
            sessions=int(r.sessions),
            first_start=report.as_utc(r.first_start),
            last_end=report.as_utc(r.last_end),
            objects_start=r.objects_start,
            objects_end=r.objects_end,
            objects_delta=delta,
        ))
    rows.sort(key=lambda x: (x.username.lower(), -x.last_end.timestamp()))

    # Who had no activity at all: a manager asks 'who did not work' as often as
    # 'who did'. Computed independently of the row cap so truncation cannot hide
    # an active member in the idle list.
    active_ids = {
        uid for (uid,) in db.query(WS.user_id)
        .filter(WS.user_id.in_(scope), WS.started_at >= lo, WS.started_at < hi)
        .distinct().all()
    }
    idle = [IdleMember(user_id=uid, username=name_)
            for uid, name_ in sorted(members.items(), key=lambda kv: kv[1].lower())
            if uid in scope and uid not in active_ids]

    since = db.query(func.min(WS.started_at)).filter(WS.user_id.in_(list(members))).scalar()

    logger.info(
        "work-session summary team=%s rows=%d truncated=%s ms=%d",
        team.id, len(rows), truncated, int((time.perf_counter() - t0) * 1000),
    )
    return WorkSessionSummaryResponse(
        **base,
        monitoring_since=report.as_utc(since),
        truncated=truncated,
        rows=rows,
        idle_members=idle,
    )


@router.get("", response_model=WorkSessionListResponse)
def work_session_list(
    team_id: int,
    user_id: int = Query(...),
    task_id: Optional[int] = Query(None),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The individual stretches behind one summary row, oldest first."""
    team = _require_viewer(team_id, current_user, db)
    _, _, lo, hi = _range(date_from, date_to)
    base = dict(enabled=bool(config.MONITOR_ENABLED), team_id=team.id,
                timezone=config.ATTENDANCE_TZ)
    if not config.MONITOR_ENABLED:
        return WorkSessionListResponse(**base)
    if user_id not in _members(db, team.id):
        raise HTTPException(status_code=404, detail="Member not found")

    WS, Task = models.WorkSession, models.Task
    query = (
        db.query(WS, func.coalesce(Task.description, WS.task_name))
        .outerjoin(Task, Task.id == WS.task_id)
        .filter(WS.user_id == user_id, WS.started_at >= lo, WS.started_at < hi)
    )
    if task_id is not None:
        query = query.filter(WS.task_id == task_id)
    got = query.order_by(WS.started_at.asc()).limit(MAX_DETAIL_ROWS + 1).all()
    truncated = len(got) > MAX_DETAIL_ROWS
    rows = [
        WorkSessionRow(
            id=w.id, user_id=w.user_id, task_id=w.task_id, task_name=nm,
            started_at=report.as_utc(w.started_at), last_at=report.as_utc(w.last_at),
            active_seconds=w.active_seconds,
            objects_start=w.objects_start, objects_end=w.objects_end,
        )
        for w, nm in got[:MAX_DETAIL_ROWS]
    ]
    return WorkSessionListResponse(**base, truncated=truncated, rows=rows)

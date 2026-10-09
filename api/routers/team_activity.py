"""Team work-activity reports (who worked on what, for how long, and the change).

Read side of the work-session monitor; capture lives in api/work_sessions.py.
Only the team's creator may read a team's activity. GET only, nothing written.
Design: .devnotes/team-monitoring/01_DESIGN.md (D2, D10).
"""
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import String, case, cast, func, select
from sqlalchemy.orm import Session

import config
import models
from api import work_sessions
from api.auth import get_current_user
from database import get_db
from schemas import (
    IdleMember,
    WorkSessionListResponse,
    WorkSessionRow,
    WorkSessionSummaryResponse,
    WorkSessionSummaryRow,
)

router = APIRouter(
    prefix="/api/teams/{team_id}/work-sessions",
    tags=["team-activity"],
    dependencies=[Depends(get_current_user)],
)

# Same bound as the other Teams endpoints use for the browser's UTC offset.
MAX_TZ_OFFSET_MINUTES = 14 * 60
SUMMARY_ROW_LIMIT = 1000
DETAIL_ROW_LIMIT = 200


def _require_creator(db: Session, team_id: int, user: models.User) -> models.Team:
    """The team, if the caller created it. Resolved before anything else is parsed.

    Authenticated username only -- not X-Annotator-Name, which any client can
    edit. 404 hides the team from people with no standing in it; 403 tells a
    plain member why they are refused.
    """
    team = db.query(models.Team).filter(models.Team.id == team_id).first()
    if team is None:
        raise HTTPException(status_code=404, detail="Team not found")
    if team.creator and team.creator == user.username:
        return team
    is_member = db.query(models.TeamMemberAssociation.id).filter(
        models.TeamMemberAssociation.team_id == team_id,
        models.TeamMemberAssociation.member_name == user.username,
    ).first()
    if is_member is None:
        raise HTTPException(status_code=404, detail="Team not found")
    raise HTTPException(status_code=403, detail="Only the team creator can view team activity")


def _team_members(db: Session, team: models.Team) -> List[str]:
    rows = db.query(models.TeamMemberAssociation.member_name).filter(
        models.TeamMemberAssociation.team_id == team.id
    ).all()
    names = {r[0] for r in rows}
    if team.creator:
        names.add(team.creator)
    return sorted(names, key=str.lower)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite hands back naive datetimes; everything leaves this module aware UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_day(value: Optional[str], default: date, label: str) -> date:
    if not value:
        return default
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"'{label}' must be YYYY-MM-DD")


def _range(date_from: Optional[str], date_to: Optional[str], tz_offset: int):
    """[lo, hi) in UTC for the caller's local days, plus the resolved local dates."""
    offset = timedelta(minutes=tz_offset)
    today = (datetime.now(timezone.utc) + offset).date()
    start = _parse_day(date_from, today, "from")
    end = _parse_day(date_to, start, "to")
    if end < start:
        raise HTTPException(status_code=400, detail="'to' must not be before 'from'")
    if (end - start).days + 1 > config.MONITOR_MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Range may cover at most {config.MONITOR_MAX_RANGE_DAYS} days",
        )
    lo = datetime(start.year, start.month, start.day, tzinfo=timezone.utc) - offset
    hi = datetime(end.year, end.month, end.day, tzinfo=timezone.utc) - offset + timedelta(days=1)
    return lo, hi, start, end


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/summary", response_model=WorkSessionSummaryResponse)
def work_session_summary(
    team_id: int,
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    tz_offset: int = Query(0, ge=-MAX_TZ_OFFSET_MINUTES, le=MAX_TZ_OFFSET_MINUTES),
    member: Optional[str] = Query(None, max_length=200),
    q: Optional[str] = Query(None, max_length=200),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> WorkSessionSummaryResponse:
    team = _require_creator(db, team_id, user)
    if not config.MONITOR_ENABLED:
        return WorkSessionSummaryResponse(enabled=False, team_id=team.id, team_name=team.name, tz_offset=tz_offset)

    lo, hi, start_day, end_day = _range(date_from, date_to, tz_offset)
    members = _team_members(db, team)
    if member is not None and member not in members:
        raise HTTPException(status_code=404, detail="Member not found in this team")
    selected = [member] if member else members

    ws = models.WorkSession
    base = ws.started_at >= lo, ws.started_at < hi

    def envelope(**extra) -> WorkSessionSummaryResponse:
        since = db.query(func.min(ws.started_at)).filter(ws.member_name.in_(members)).scalar() if members else None
        return WorkSessionSummaryResponse(
            enabled=True, team_id=team.id, team_name=team.name, tz_offset=tz_offset,
            date_from=start_day.isoformat(), date_to=end_day.isoformat(),
            as_of=work_sessions.last_flush_at(), monitoring_since=_as_utc(since), **extra,
        )

    if not members:
        return envelope()

    # One grouped query. A stretch whose task was deleted has no task_id, so those
    # group by the snapshot name instead of collapsing into one row per member.
    name = func.coalesce(models.Task.description, ws.task_name)
    grp = func.coalesce(cast(ws.task_id, String), ws.task_name, "")
    inner = (
        select(
            ws.member_name, grp.label("grp"), ws.task_id, name.label("name"),
            ws.started_at, ws.last_at, ws.active_seconds, ws.objects_start, ws.objects_end,
            func.row_number().over(partition_by=[ws.member_name, grp], order_by=ws.started_at.asc()).label("rn_first"),
            func.row_number().over(partition_by=[ws.member_name, grp], order_by=ws.started_at.desc()).label("rn_last"),
        )
        .select_from(ws)
        .outerjoin(models.Task, models.Task.id == ws.task_id)
        .where(ws.member_name.in_(selected), *base)
    )
    if q:
        inner = inner.where(func.lower(name).like(f"%{_escape_like(q.lower())}%", escape="\\"))
    s = inner.subquery()
    stmt = (
        select(
            s.c.member_name, s.c.grp, func.max(s.c.task_id), func.max(s.c.name),
            func.sum(s.c.active_seconds), func.count(), func.min(s.c.started_at), func.max(s.c.last_at),
            func.max(case((s.c.rn_first == 1, s.c.objects_start))),
            func.max(case((s.c.rn_last == 1, s.c.objects_end))),
        )
        .group_by(s.c.member_name, s.c.grp)
        .order_by(func.max(s.c.last_at).desc())
        .limit(SUMMARY_ROW_LIMIT + 1)
    )
    fetched = db.execute(stmt).all()
    truncated = len(fetched) > SUMMARY_ROW_LIMIT

    rows = []
    for m, _grp, task_id, task_name, seconds, count, first, last, o_start, o_end in fetched[:SUMMARY_ROW_LIMIT]:
        rows.append(WorkSessionSummaryRow(
            member_name=m, task_id=task_id, task_name=task_name,
            active_seconds=int(seconds or 0), sessions=count,
            first_start=_as_utc(first), last_end=_as_utc(last),
            objects_start=o_start, objects_end=o_end,
            objects_delta=(o_end - o_start) if o_start is not None and o_end is not None else None,
        ))
    # Query order is recency; present grouped by person, most recent work first.
    rows.sort(key=lambda r: (r.member_name.lower(), -r.last_end.timestamp()))

    idle: List[IdleMember] = []
    if not member:
        active = {r[0] for r in db.query(ws.member_name).filter(ws.member_name.in_(members), *base).distinct().all()}
        idle = [IdleMember(member_name=m) for m in members if m not in active]
    return envelope(truncated=truncated, rows=rows, idle_members=idle)


@router.get("", response_model=WorkSessionListResponse)
def work_session_detail(
    team_id: int,
    member: str = Query(..., max_length=200),
    task_id: Optional[int] = Query(None),
    task_name: Optional[str] = Query(None, max_length=255),
    date_from: Optional[str] = Query(None, alias="from"),
    date_to: Optional[str] = Query(None, alias="to"),
    tz_offset: int = Query(0, ge=-MAX_TZ_OFFSET_MINUTES, le=MAX_TZ_OFFSET_MINUTES),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
) -> WorkSessionListResponse:
    """The stretches behind one summary row. `task_name` identifies rows whose
    task was deleted (task_id NULL), matching how the summary groups them."""
    team = _require_creator(db, team_id, user)
    if not config.MONITOR_ENABLED:
        return WorkSessionListResponse(enabled=False, team_id=team.id, tz_offset=tz_offset)
    lo, hi, _s, _e = _range(date_from, date_to, tz_offset)
    if member not in _team_members(db, team):
        raise HTTPException(status_code=404, detail="Member not found in this team")

    ws = models.WorkSession
    query = db.query(ws).filter(ws.member_name == member, ws.started_at >= lo, ws.started_at < hi)
    if task_id is not None:
        query = query.filter(ws.task_id == task_id)
    else:
        query = query.filter(ws.task_id.is_(None))
        query = query.filter(ws.task_name == task_name) if task_name is not None else query.filter(ws.task_name.is_(None))
    found = query.order_by(ws.started_at.asc()).limit(DETAIL_ROW_LIMIT + 1).all()
    rows = [
        WorkSessionRow(
            id=r.id, member_name=r.member_name, task_id=r.task_id, task_name=r.task_name,
            started_at=_as_utc(r.started_at), last_at=_as_utc(r.last_at),
            active_seconds=r.active_seconds, objects_start=r.objects_start, objects_end=r.objects_end,
        )
        for r in found[:DETAIL_ROW_LIMIT]
    ]
    return WorkSessionListResponse(
        enabled=True, team_id=team.id, tz_offset=tz_offset,
        truncated=len(found) > DETAIL_ROW_LIMIT, rows=rows,
    )

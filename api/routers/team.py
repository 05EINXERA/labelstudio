import csv
import io
import re
from datetime import datetime, timezone, timedelta, date as date_cls
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

import models
from database import get_db, commit_with_retry
from schemas import (
    TeamTime, TeamMemberResponse, TeamTimeResponse, TeamMemberAssign, TeamMemberCreate,
    LoginSessionEntry, LoginSessionHistory,
)
from api.auth import get_current_user, require_csrf, get_current_annotator
from api.presence import (
    PRESENCE_TIMEOUT_SECONDS,
    close_stale_sessions,
    touch_session,
)

router = APIRouter(prefix="/api/team", tags=["team"], dependencies=[Depends(get_current_user)])

# Sessions are stored in UTC, but "today" means the annotator's local day. The
# client sends its UTC offset in minutes (the negation of Date#getTimezoneOffset)
# so a shift that starts at 09:00 local lands in the right bucket. Bounded to
# real-world offsets so a bogus value cannot shift the window arbitrarily.
MAX_TZ_OFFSET_MINUTES = 14 * 60

# A CSV export covers at most this many days per request, so one URL cannot ask
# the DB to scan an unbounded span of history.
MAX_EXPORT_DAYS = 366


def _safe_filename_part(value: str) -> str:
    """Reduce a member name to characters safe in a Content-Disposition filename."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value or "").strip("_")
    return cleaned[:60] or "member"


def _utc_iso(dt) -> str:
    if dt is None:
        return ""
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


def _day_bounds(day: date_cls, tz_offset_minutes: int = 0):
    """UTC [start, end) covering the given local calendar day."""
    offset = timedelta(minutes=tz_offset_minutes)
    local_midnight = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    start = local_midnight - offset
    return start, start + timedelta(days=1)


def _caller_names(user, annotator) -> list:
    names = [user.username]
    if annotator:
        names.append(annotator.name)
    return list(set(names))


def _readable_member_names(db: Session, caller_names: list) -> set:
    """Members whose hours the caller may read: themselves, plus every member
    of a team they created. Mirrors the rule on GET /{name}/tasks."""
    created_team_ids = {
        t[0] for t in db.query(models.Team.id).filter(models.Team.creator.in_(caller_names)).all()
    }
    readable = set(caller_names)
    if created_team_ids:
        readable |= {
            a[0] for a in db.query(models.TeamMemberAssociation.member_name).filter(
                models.TeamMemberAssociation.team_id.in_(created_team_ids)
            ).all()
        }
    return readable


def _session_seconds(session, now: datetime) -> int:
    """Elapsed seconds for a session; an open one is counted up to `now`."""
    login = session.login_at if session.login_at.tzinfo else session.login_at.replace(tzinfo=timezone.utc)
    end = session.logout_at
    if end is None:
        end = now
    elif not end.tzinfo:
        end = end.replace(tzinfo=timezone.utc)
    return max(0, int((end - login).total_seconds()))

@router.get("", response_model=List[TeamMemberResponse])
def get_team(
    tz_offset: int = Query(0, ge=-MAX_TZ_OFFSET_MINUTES, le=MAX_TZ_OFFSET_MINUTES),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    annotator = Depends(get_current_annotator),
):
    names = [user.username]
    if annotator:
        names.append(annotator.name)
    names = list(set(names))
        
    members_query = db.query(models.TeamMember)
    
    annotator_teams = db.query(models.TeamMemberAssociation.team_id).filter(
        models.TeamMemberAssociation.member_name.in_(names)
    ).all()
    annotator_team_ids = [t[0] for t in annotator_teams]
    
    # Also find teams created by the annotator or user
    created_teams = db.query(models.Team.id).filter(models.Team.creator.in_(names)).all()
    created_team_ids = {t[0] for t in created_teams}
    
    all_accessible_team_ids = list(set(annotator_team_ids) | created_team_ids)
    if not all_accessible_team_ids:
        return []
        
    # User can only see his team members
    visible_member_names = db.query(models.TeamMemberAssociation.member_name).filter(
        models.TeamMemberAssociation.team_id.in_(all_accessible_team_ids)
    ).subquery()
    
    members = members_query.filter(
        models.TeamMember.name.in_(db.query(visible_member_names.c.member_name))
    ).order_by(models.TeamMember.name).all()
    
    if not members:
        return []
        
    member_names = [m.name for m in members]
    associations = db.query(models.TeamMemberAssociation, models.Team).join(
        models.Team, models.TeamMemberAssociation.team_id == models.Team.id
    ).filter(
        models.TeamMemberAssociation.member_name.in_(member_names)
    ).all()
    
    teams_by_member = {name: [] for name in member_names}
    for assoc, team in associations:
        teams_by_member[assoc.member_name].append({
            "id": team.id,
            "name": team.name,
            "creator": team.creator,
            "created_at": team.created_at
        })
        
    now = datetime.now(timezone.utc)
    # Sweep before reporting so a member whose heartbeat died shows Offline here
    # and has their session closed at its real end time in the history.
    close_stale_sessions(db, now)

    # Today's session totals for every visible member in one query, so the
    # gallery does not fire a request per row just to fill in the column.
    local_today = (now + timedelta(minutes=tz_offset)).date()
    day_start, day_end = _day_bounds(local_today, tz_offset)
    todays_sessions = (
        db.query(models.LoginSession)
        .filter(
            models.LoginSession.member_name.in_(member_names),
            models.LoginSession.login_at >= day_start,
            models.LoginSession.login_at < day_end,
        )
        .all()
    )
    seconds_today = {name: 0 for name in member_names}
    for s in todays_sessions:
        seconds_today[s.member_name] = seconds_today.get(s.member_name, 0) + _session_seconds(s, now)

    results = []
    for m in members:
        is_logged_in = False
        last_active = None
        if m.last_active_at is not None:
            last_active_utc = m.last_active_at if m.last_active_at.tzinfo else m.last_active_at.replace(tzinfo=timezone.utc)
            is_logged_in = (now - last_active_utc).total_seconds() <= PRESENCE_TIMEOUT_SECONDS
            last_active = last_active_utc

        results.append({
            "name": m.name,
            "time_logged": m.time_logged,
            "teams": teams_by_member[m.name],
            "is_logged_in": is_logged_in,
            "last_active_at": last_active,
            "seconds_today": seconds_today.get(m.name, 0),
        })

    return results

@router.post("/ping", dependencies=[Depends(require_csrf)])
def ping_presence(
    request: Request,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
    annotator = Depends(get_current_annotator)
):
    annotator_name = request.headers.get("X-Annotator-Name") or (annotator.name if annotator else current_user.username)
    if annotator_name:
        member = db.query(models.TeamMember).filter(models.TeamMember.name == annotator_name).first()
        if not member:
            member = models.TeamMember(name=annotator_name, time_logged=0)
            db.add(member)
        now = datetime.now(timezone.utc)
        member.last_active_at = now
        commit_with_retry(db)
        # Advance (or open) the session row so the history tracks this beat.
        touch_session(db, annotator_name, now)
    return {"status": "ok"}

@router.get("/sessions/export")
def export_sessions_csv(
    name: Optional[str] = Query(None, description="Single member. Omit to export everyone readable."),
    start: Optional[str] = Query(None, description="First local day, YYYY-MM-DD. Defaults to `end`."),
    end: Optional[str] = Query(None, description="Last local day, YYYY-MM-DD. Defaults to today."),
    tz_offset: int = Query(0, ge=-MAX_TZ_OFFSET_MINUTES, le=MAX_TZ_OFFSET_MINUTES),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    annotator = Depends(get_current_annotator),
):
    """Download login/logout history as CSV, for one member or the whole team.

    One row per session so the detail stays auditable; `total_hours_that_day`
    repeats the per-annotator-per-day total on each of its rows, which is what
    makes a spreadsheet pivot roll it up without re-deriving anything.

    Times are written twice: a local clock string for reading, and the raw UTC
    ISO timestamp so the file survives being opened in another timezone.
    """
    caller_names = _caller_names(user, annotator)
    readable = _readable_member_names(db, caller_names)

    if name:
        if name not in readable:
            raise HTTPException(status_code=403, detail="You cannot view this member's history")
        targets = [name]
    else:
        targets = sorted(readable)

    now = datetime.now(timezone.utc)
    close_stale_sessions(db, now)

    local_today = (now + timedelta(minutes=tz_offset)).date()
    try:
        end_day = date_cls.fromisoformat(end) if end else local_today
        start_day = date_cls.fromisoformat(start) if start else end_day
    except ValueError:
        raise HTTPException(status_code=400, detail="start and end must be YYYY-MM-DD")
    if start_day > end_day:
        raise HTTPException(status_code=400, detail="start must not be after end")
    # Bounded so one request cannot scan an unbounded span of history.
    if (end_day - start_day).days > MAX_EXPORT_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Range too large; export at most {MAX_EXPORT_DAYS} days at a time.",
        )

    range_start, _ = _day_bounds(start_day, tz_offset)
    _, range_end = _day_bounds(end_day, tz_offset)

    rows = []
    if targets:
        rows = (
            db.query(models.LoginSession)
            .filter(
                models.LoginSession.member_name.in_(targets),
                models.LoginSession.login_at >= range_start,
                models.LoginSession.login_at < range_end,
            )
            .order_by(models.LoginSession.member_name.asc(), models.LoginSession.login_at.asc())
            .all()
        )

    offset = timedelta(minutes=tz_offset)

    def local(dt):
        if dt is None:
            return None
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)) + offset

    # Per-annotator-per-day totals, so each row can carry its day's total.
    totals = {}
    for r in rows:
        key = (r.member_name, local(r.login_at).date())
        totals[key] = totals.get(key, 0) + _session_seconds(r, now)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "annotator", "date", "login_local", "logout_local",
        "duration_hours", "duration_minutes", "ended",
        "total_hours_that_day", "login_utc", "logout_utc",
    ])
    for r in rows:
        seconds = _session_seconds(r, now)
        login_local = local(r.login_at)
        logout_local = local(r.logout_at)
        day = login_local.date()
        if r.logout_at is None:
            ended = "still logged in"
        elif r.ended_reason == "inactive":
            ended = "inactive"
        else:
            ended = "logout"
        writer.writerow([
            r.member_name,
            day.isoformat(),
            login_local.strftime("%H:%M"),
            logout_local.strftime("%H:%M") if logout_local else "",
            f"{seconds / 3600:.2f}",
            round(seconds / 60),
            ended,
            f"{totals[(r.member_name, day)] / 3600:.2f}",
            _utc_iso(r.login_at),
            _utc_iso(r.logout_at),
        ])

    if start_day == end_day:
        span = start_day.isoformat()
    else:
        span = f"{start_day.isoformat()}_to_{end_day.isoformat()}"
    stem = f"{_safe_filename_part(name)}-" if name else "team-"
    filename = f"{stem}login-history-{span}.csv"

    return PlainTextResponse(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

@router.get("/{name}/tasks")
def get_member_tasks(
    name: str,
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    annotator = Depends(get_current_annotator),
):
    """Return tasks assigned to a team member.
    Only team owners (creators) can see tasks for their team members.
    """
    caller_names = [user.username]
    if annotator:
        caller_names.append(annotator.name)
    caller_names = list(set(caller_names))

    # Verify the target member exists
    member = db.query(models.TeamMember).filter(models.TeamMember.name == name).first()
    if not member:
        raise HTTPException(status_code=404, detail="Team member not found")

    # Find teams where the caller is the creator
    created_team_ids = {
        t[0] for t in db.query(models.Team.id).filter(models.Team.creator.in_(caller_names)).all()
    }
    if not created_team_ids:
        raise HTTPException(status_code=403, detail="You do not own any teams")

    # Check the target member belongs to at least one of the caller's teams
    member_team_ids = {
        a[0] for a in db.query(models.TeamMemberAssociation.team_id).filter(
            models.TeamMemberAssociation.member_name == name
        ).all()
    }
    if not (member_team_ids & created_team_ids):
        raise HTTPException(status_code=403, detail="This member is not in any of your teams")

    # Fetch tasks assigned to the member with project info
    tasks = (
        db.query(
            models.Task.id,
            models.Task.description,
            models.Task.status,
            models.Task.image_path,
            models.Task.project_id,
            models.Task.updated_at,
            models.Project.name.label("project_name"),
        )
        .join(models.Project, models.Task.project_id == models.Project.id)
        .filter(models.Task.assignee == name)
        .order_by(models.Task.updated_at.desc())
        .all()
    )

    return [
        {
            "id": t.id,
            "description": t.description,
            "status": t.status,
            "image_path": t.image_path,
            "project_id": t.project_id,
            "project_name": t.project_name,
            "updated_at": t.updated_at,
        }
        for t in tasks
    ]

@router.get("/{name}/sessions", response_model=LoginSessionHistory)
def get_member_sessions(
    name: str,
    date: Optional[str] = Query(None, description="Local calendar day, YYYY-MM-DD. Defaults to today."),
    tz_offset: int = Query(0, ge=-MAX_TZ_OFFSET_MINUTES, le=MAX_TZ_OFFSET_MINUTES),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
    annotator = Depends(get_current_annotator),
):
    """Return a member's login/logout sessions for one day.

    Visibility matches `GET /{name}/tasks`: only the creator of a team the
    member belongs to may read it. On a shared-login deployment this keeps one
    annotator's hours from being readable by the other two dozen.
    """
    caller_names = _caller_names(user, annotator)

    member = db.query(models.TeamMember).filter(models.TeamMember.name == name).first()
    if not member:
        raise HTTPException(status_code=404, detail="Team member not found")

    # A member may always read their own history; otherwise the caller must own
    # a team the member belongs to.
    if name not in _readable_member_names(db, caller_names):
        raise HTTPException(status_code=403, detail="You cannot view this member's history")

    now = datetime.now(timezone.utc)
    close_stale_sessions(db, now)

    if date:
        try:
            day = date_cls.fromisoformat(date)
        except ValueError:
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    else:
        day = (now + timedelta(minutes=tz_offset)).date()

    day_start, day_end = _day_bounds(day, tz_offset)

    rows = (
        db.query(models.LoginSession)
        .filter(
            models.LoginSession.member_name == name,
            models.LoginSession.login_at >= day_start,
            models.LoginSession.login_at < day_end,
        )
        .order_by(models.LoginSession.login_at.asc())
        .all()
    )

    sessions = []
    total = 0
    for r in rows:
        seconds = _session_seconds(r, now)
        total += seconds
        sessions.append({
            "login_at": r.login_at,
            "logout_at": r.logout_at,
            "ended_reason": r.ended_reason,
            "duration_seconds": seconds,
            "is_open": r.logout_at is None,
        })

    return {
        "name": name,
        "date": day.isoformat(),
        "sessions": sessions,
        "total_seconds": total,
    }

@router.post("", response_model=TeamMemberResponse, dependencies=[Depends(require_csrf)])
def add_team_member(payload: TeamMemberCreate, request: Request, db: Session = Depends(get_db), annotator = Depends(get_current_annotator)):
    if not annotator:
        raise HTTPException(status_code=403, detail="Must be logged in as an annotator")
        
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
        
    registered_user = db.query(models.User).filter(models.User.username == name).first()
    if not registered_user:
        raise HTTPException(status_code=400, detail="Only registered users can be added as team members")
        
    annotator_teams = db.query(models.Team).filter(models.Team.creator == annotator.name).all()
    owned_team_ids = {t.id for t in annotator_teams}
    
    if payload.team_ids:
        unowned_adds = set(payload.team_ids) - owned_team_ids
        if unowned_adds:
            raise HTTPException(status_code=403, detail="You can only add members to teams you created")
            
    existing = db.query(models.TeamMember).filter(models.TeamMember.name == name).first()
    if not existing:
        member = models.TeamMember(name=name, time_logged=0)
        db.add(member)
    else:
        member = existing
        if not payload.team_ids:
            raise HTTPException(status_code=400, detail="User is already registered as a team member")
    
    if payload.team_ids:
        db_teams = db.query(models.Team).filter(models.Team.id.in_(payload.team_ids)).all()
        if len(db_teams) != len(payload.team_ids):
            raise HTTPException(status_code=400, detail="One or more teams not found")
            
        current_associations = set([a[0] for a in db.query(models.TeamMemberAssociation.team_id).filter(
            models.TeamMemberAssociation.member_name == name
        ).all()])
            
        if all(t in current_associations for t in payload.team_ids):
            raise HTTPException(status_code=400, detail="User is already in the selected team(s)")
            
        for t in db_teams:
            if t.id not in current_associations:
                assoc = models.TeamMemberAssociation(member_name=name, team_id=t.id)
                db.add(assoc)
            
    commit_with_retry(db)
    
    # Return all teams they are in
    all_teams_for_member = db.query(models.Team).join(
        models.TeamMemberAssociation, models.TeamMemberAssociation.team_id == models.Team.id
    ).filter(models.TeamMemberAssociation.member_name == name).all()
    
    teams_resp = [{
        "id": t.id,
        "name": t.name,
        "creator": t.creator,
        "created_at": t.created_at
    } for t in all_teams_for_member]
    
    return {
        "name": member.name,
        "time_logged": member.time_logged,
        "teams": teams_resp
    }

@router.post("/time", response_model=TeamTimeResponse, dependencies=[Depends(require_csrf)])
def update_team_time(
    payload: TeamTime,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    # In a shared-login deployment, we must trust the client-supplied name from
    # localStorage, otherwise all annotators log time against the single shared
    # account.
    name = payload.name
    
    if not name or name == 'Unknown':
        return {"status": "ignored", "time_logged": 0}

    member = db.query(models.TeamMember).filter(models.TeamMember.name == name).first()
    if not member:
        # Create the row so the seconds are never lost.
        member = models.TeamMember(name=name, time_logged=0)
        db.add(member)

    member.time_logged = (member.time_logged or 0) + payload.time_logged
    commit_with_retry(db)
    return {"status": "ok", "time_logged": member.time_logged}

@router.patch("/{name}", response_model=TeamMemberResponse, dependencies=[Depends(require_csrf)])
def update_team_member(
    name: str,
    payload: TeamMemberAssign,
    request: Request,
    db: Session = Depends(get_db),
    annotator = Depends(get_current_annotator)
):
    if not annotator:
        raise HTTPException(status_code=403, detail="Must be logged in as an annotator")
        
    member = db.query(models.TeamMember).filter(models.TeamMember.name == name).first()
    if not member:
        raise HTTPException(status_code=404, detail="Team member not found")
        
    annotator_teams = db.query(models.Team).filter(models.Team.creator == annotator.name).all()
    owned_team_ids = {t.id for t in annotator_teams}
    
    current_associations = db.query(models.TeamMemberAssociation).filter(models.TeamMemberAssociation.member_name == name).all()
    current_team_ids = {a.team_id for a in current_associations}
    
    new_team_ids = set(payload.team_ids) if payload.team_ids else set()
    
    added_teams = new_team_ids - current_team_ids
    if added_teams - owned_team_ids:
        raise HTTPException(status_code=403, detail="You can only add members to teams you created")
        
    final_team_ids = (current_team_ids - owned_team_ids) | (new_team_ids & owned_team_ids)
        
    # Remove existing associations
    db.query(models.TeamMemberAssociation).filter(models.TeamMemberAssociation.member_name == name).delete()
    
    teams = []
    if final_team_ids:
        db_teams = db.query(models.Team).filter(models.Team.id.in_(final_team_ids)).all()
        for t in db_teams:
            assoc = models.TeamMemberAssociation(member_name=name, team_id=t.id)
            db.add(assoc)
            teams.append({
                "id": t.id,
                "name": t.name,
                "creator": t.creator,
                "created_at": t.created_at
            })
            
    commit_with_retry(db)
    
    return {
        "name": member.name,
        "time_logged": member.time_logged,
        "teams": teams
    }

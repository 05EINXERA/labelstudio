"""Presence and login-session bookkeeping.

Two related things live here so the auth router and the team router share one
implementation instead of each growing its own copy:

* `TeamMember.last_active_at` — the single mutable "are they online now" cell.
* `LoginSession` rows — the append-only history the Teams page reads to show
  when someone logged in and out during a day.

The tricky case is the one that happens most: an annotator closes the tab or
drops off the LAN instead of clicking Log out, so no logout ever arrives. Rather
than leave those sessions open forever, `close_stale_sessions` stamps
`logout_at = last_seen_at` (the last heartbeat we actually received) once the
heartbeat has been silent longer than `PRESENCE_TIMEOUT_SECONDS`, and marks the
row `ended_reason='inactive'`. The recorded times therefore reflect when the
person actually stopped working, not when we noticed.

This is deliberately sweep-on-read: there is no background thread. The sweep
runs whenever presence is queried or a heartbeat lands, which on a live LAN box
is every few seconds, and it is a single indexed UPDATE.
"""
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session

import models
from database import commit_with_retry

logger = logging.getLogger(__name__)

# How long a heartbeat may be silent before the session counts as ended. The
# client pings every 30s (frontend/js/api.js initPresenceHeartbeat), so this
# tolerates ~4 missed pings before declaring someone gone.
PRESENCE_TIMEOUT_SECONDS = 120

# A ping within this many seconds of the last one is folded into the open
# session rather than opening a new one. Anything longer counts as a fresh
# login, which is what makes a lunch break show as two sessions.
SESSION_RESUME_WINDOW_SECONDS = PRESENCE_TIMEOUT_SECONDS


def as_utc(dt):
    """Re-attach UTC to a naive datetime read back from the DB."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def close_stale_sessions(db: Session, now: datetime | None = None) -> int:
    """Close open sessions whose heartbeat has gone silent.

    Returns the number of sessions closed. Safe to call on every request.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=PRESENCE_TIMEOUT_SECONDS)

    stale = (
        db.query(models.LoginSession)
        .filter(
            models.LoginSession.logout_at.is_(None),
            models.LoginSession.last_seen_at < cutoff,
        )
        .all()
    )
    if not stale:
        return 0

    for session in stale:
        # The logout time is the last beat we heard, not "now" — the person
        # stopped then; we are only noticing late.
        session.logout_at = session.last_seen_at
        session.ended_reason = "inactive"

    commit_with_retry(db)
    logger.info("Closed %d stale login session(s)", len(stale))
    return len(stale)


def open_session(db: Session, member_name: str, now: datetime | None = None) -> None:
    """Record an explicit login, closing any session left open for the member."""
    if not member_name:
        return
    now = now or datetime.now(timezone.utc)

    # An explicit login supersedes whatever was open (a previous tab that never
    # said goodbye), so close it at its last known beat rather than stacking a
    # second open row on the same member.
    open_rows = (
        db.query(models.LoginSession)
        .filter(
            models.LoginSession.member_name == member_name,
            models.LoginSession.logout_at.is_(None),
        )
        .all()
    )
    for row in open_rows:
        row.logout_at = as_utc(row.last_seen_at) or now
        row.ended_reason = "inactive"

    db.add(models.LoginSession(
        member_name=member_name,
        login_at=now,
        last_seen_at=now,
    ))
    commit_with_retry(db)


def touch_session(db: Session, member_name: str, now: datetime | None = None) -> None:
    """Advance the open session's heartbeat, opening one if none is live.

    A session is opened here (not only at login) because sessions predate no
    login event in two real cases: the shared account was already authenticated
    when this feature shipped, and a long-lived cookie means a returning tab may
    never hit /api/auth/token again.
    """
    if not member_name:
        return
    now = now or datetime.now(timezone.utc)

    session = (
        db.query(models.LoginSession)
        .filter(
            models.LoginSession.member_name == member_name,
            models.LoginSession.logout_at.is_(None),
        )
        .order_by(models.LoginSession.login_at.desc())
        .first()
    )

    if session is not None:
        last_seen = as_utc(session.last_seen_at)
        gap = (now - last_seen).total_seconds() if last_seen else None
        if gap is not None and gap > SESSION_RESUME_WINDOW_SECONDS:
            # The gap is long enough that this is a new sitting: close the old
            # row at its real end and start a fresh one.
            session.logout_at = last_seen
            session.ended_reason = "inactive"
            session = None
        else:
            session.last_seen_at = now

    if session is None:
        db.add(models.LoginSession(
            member_name=member_name,
            login_at=now,
            last_seen_at=now,
        ))

    commit_with_retry(db)


def close_session(db: Session, member_name: str, now: datetime | None = None) -> None:
    """Record an explicit logout for the member."""
    if not member_name:
        return
    now = now or datetime.now(timezone.utc)

    open_rows = (
        db.query(models.LoginSession)
        .filter(
            models.LoginSession.member_name == member_name,
            models.LoginSession.logout_at.is_(None),
        )
        .all()
    )
    if not open_rows:
        return
    for row in open_rows:
        row.logout_at = now
        row.ended_reason = "logout"
    commit_with_retry(db)

import logging
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Response, Request, Query
from sqlalchemy.exc import IntegrityError
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

import models
from config import ALLOW_REGISTRATION, IS_PRODUCTION, MIN_PASSWORD_LENGTH
from database import get_db
from schemas import UserCreate, Token, Me, MeTeam
from api.auth import (
    get_password_hash,
    verify_password,
    create_access_token,
    issue_session_cookies,
    issue_csrf_cookie,
    clear_session_cookies,
    get_current_user,
    require_csrf,
    CSRF_COOKIE_NAME,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.post("/register", response_model=Token)
def register(user: UserCreate, response: Response, db: Session = Depends(get_db)):
    """Register a new user.

    Self-registration is gated by ALLOW_REGISTRATION. On a LAN deployment it is
    turned off and the operator creates accounts with scripts/create_user.py —
    otherwise anyone who can reach the port can mint themselves an account.
    """
    if not ALLOW_REGISTRATION:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Registration is disabled. Contact your administrator.",
        )

    if len(user.password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
        )

    db_user = db.query(models.User).filter(models.User.username == user.username).first()
    if db_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered",
        )

    hashed_password = get_password_hash(user.password)
    new_user = models.User(username=user.username, hashed_password=hashed_password)
    try:
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered",
        )

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": new_user.username}, expires_delta=access_token_expires
    )
    csrf_token = issue_session_cookies(response, access_token)
    return {"access_token": access_token, "token_type": "bearer", "csrf_token": csrf_token}

@router.post("/token", response_model=Token)
def login_for_access_token(
    request: Request,
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    user = db.query(models.User).filter(models.User.username == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        # Logged so repeated failures are visible to the operator; the response
        # stays deliberately vague about which half was wrong.
        logger.warning(
            "Failed login for username=%r from %s",
            form_data.username, request.client.host if request.client else "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    csrf_token = issue_session_cookies(response, access_token)
    return {"access_token": access_token, "token_type": "bearer", "csrf_token": csrf_token}

@router.post("/logout")
def logout(response: Response):
    clear_session_cookies(response)
    return {"status": "ok"}


@router.get("/me", response_model=Me)
def me(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """The caller's real identity and team memberships.

    The frontend previously derived identity from
    `localStorage['dataset_username']` — free text the user typed, so neither
    trustworthy nor reliably accurate (CLAUDE.md rule 14). Every role-levelled UI
    decision needs the real thing, which is why this endpoint blocks Phase 4.

    `/api/auth` is the one router without a router-level auth dependency (it has
    to serve login and registration), so the dependency goes on this function.

    Also self-heals a missing CSRF cookie. `get_current_user` above already
    proved the session cookie is valid, so if the CSRF cookie didn't come back
    with it — e.g. a browser restart restored the (7-day) session cookie from
    disk but dropped the session-only CSRF cookie — every subsequent write
    would 403 until the user logs out and back in. The frontend calls this
    endpoint on every page load, so reissuing here fixes the desync before it
    can cause an autosave to be silently rejected. Set-Cookie on a GET is not a
    database write, so this doesn't run afoul of CLAUDE.md rule 4. See
    .devnotes/offline/INCIDENT_692.md.
    """
    if not request.cookies.get(CSRF_COOKIE_NAME):
        issue_csrf_cookie(response)
    teams = (
        db.query(models.Team, models.TeamMembership.role)
        .join(
            models.TeamMembership,
            models.TeamMembership.team_id == models.Team.id,
        )
        .filter(models.TeamMembership.user_id == current_user.id)
        .order_by(models.Team.name)
        .all()
    )
    return Me(
        id=current_user.id,
        username=current_user.username,
        teams=[MeTeam(id=t.id, name=t.name, role=role) for t, role in teams],
    )

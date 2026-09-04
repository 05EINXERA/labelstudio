import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
from jose import jwt

from fastapi import APIRouter, Depends, HTTPException, status, Response, Request, Query
from sqlalchemy.exc import IntegrityError
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

import models
from config import ALLOW_REGISTRATION, IS_PRODUCTION, MANUAL_URL, MIN_PASSWORD_LENGTH
from database import get_db, commit_with_retry
from schemas import UserCreate, Token, UserResponse
from api.auth import (
    get_password_hash,
    verify_password,
    create_access_token,
    issue_session_cookies,
    clear_session_cookies,
    get_current_user,
    require_csrf,
    get_token,
    SECRET_KEY,
    ALGORITHM,
    ACCESS_TOKEN_EXPIRE_MINUTES,
)
from api.presence import open_session, close_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/auth", tags=["auth"])

@router.get("/config")
def get_auth_config():
    return {"allow_registration": ALLOW_REGISTRATION, "manual_url": MANUAL_URL}

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
        # Also ensure TeamMember exists and update last_active_at
        member = db.query(models.TeamMember).filter(models.TeamMember.name == user.username).first()
        if not member:
            member = models.TeamMember(name=user.username, time_logged=0)
            db.add(member)
        member.last_active_at = datetime.now(timezone.utc)
        commit_with_retry(db)
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
    
    # Update TeamMember presence on login
    member = db.query(models.TeamMember).filter(models.TeamMember.name == user.username).first()
    if not member:
        member = models.TeamMember(name=user.username, time_logged=0)
        db.add(member)
    member.last_active_at = datetime.now(timezone.utc)
    commit_with_retry(db)
    open_session(db, user.username)

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.username}, expires_delta=access_token_expires
    )
    csrf_token = issue_session_cookies(response, access_token)
    return {"access_token": access_token, "token_type": "bearer", "csrf_token": csrf_token}

@router.get("/me", response_model=UserResponse)
def get_me(user: models.User = Depends(get_current_user)):
    return {"username": user.username}

@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    clear_session_cookies(response)
    username = request.headers.get("X-Annotator-Name")
    if not username:
        token = get_token(request)
        if token:
            try:
                payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
                username = payload.get("sub")
            except Exception:
                pass
    if username:
        member = db.query(models.TeamMember).filter(models.TeamMember.name == username).first()
        if member:
            member.last_active_at = None
            commit_with_retry(db)
        # Closed regardless of the TeamMember row: the session history is keyed
        # on the name, and a missing member row must not silently drop a logout.
        close_session(db, username)
    return {"status": "ok"}

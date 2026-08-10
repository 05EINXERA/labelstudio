import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status, Request, Response
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
import bcrypt
from sqlalchemy.orm import Session

import models
from config import (
    COOKIE_SAMESITE,
    COOKIE_SECURE,
    DATA_DIR,
    IS_PRODUCTION,
    JWT_SECRET,
)
from database import get_db

logger = logging.getLogger(__name__)


def _resolve_secret() -> str:
    """The JWT signing key.

    In production `config.validate_config()` has already refused to start
    without `JWT_SECRET`, so this is a straight read. In development we keep a
    generated key in a file so sessions survive a restart — but anchored to
    DATA_DIR rather than `os.getcwd()`. The old cwd-relative path meant that
    launching the server from a different directory silently minted a new key
    and signed everyone out.
    """
    if JWT_SECRET:
        return JWT_SECRET

    if IS_PRODUCTION:
        # Unreachable via main.py (validate_config raises first), but a direct
        # import of this module must not fall back to an ephemeral key.
        raise RuntimeError("JWT_SECRET must be set when APP_ENV=production.")

    secret_file = os.path.join(DATA_DIR, ".jwt_secret")
    if os.path.exists(secret_file):
        with open(secret_file, "r") as handle:
            existing = handle.read().strip()
            if existing:
                return existing

    generated = secrets.token_hex(32)
    try:
        with open(secret_file, "w") as handle:
            handle.write(generated)
    except OSError as exc:
        logger.warning(
            "Could not persist a development JWT secret to %s (%s); sessions "
            "will not survive a restart.", secret_file, exc,
        )
    return generated


SECRET_KEY = _resolve_secret()
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7 # 7 days

# Name of the readable companion cookie for double-submit CSRF (see
# require_csrf). Deliberately NOT httpOnly: the frontend has to read it to echo
# it back in a header, and that is exactly what a cross-origin page cannot do.
CSRF_COOKIE_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
# Query-parameter fallback for navigator.sendBeacon, which cannot set headers.
# See require_csrf() for why this is safe and why it stays a fallback.
CSRF_QUERY_PARAM = "csrf_token"


def issue_session_cookies(response: Response, access_token: str) -> str:
    """Set the session + CSRF cookie pair on a login/register response.

    Returns the CSRF token so the caller can also hand it back in the JSON body
    (a client that cannot read cookies, e.g. a test, still needs it).
    """
    # Both cookies get the same max_age as the JWT they gate. Previously both
    # were session cookies (no max_age) even though the token inside
    # access_token is valid for ACCESS_TOKEN_EXPIRE_MINUTES (7 days): a browser
    # restart could restore the session cookie from disk while dropping the
    # session-only CSRF cookie, leaving a client that reads as authenticated
    # (GETs succeed) but fails every write with 403. That desync is what wiped
    # task 692's annotations after the 2026-08-04 outage — see
    # .devnotes/offline/INCIDENT_692.md. Keeping the two cookies' lifetimes
    # identical removes the failure mode at the source; issue_csrf_cookie()
    # below is the second layer (self-heal on any authenticated GET).
    max_age = ACCESS_TOKEN_EXPIRE_MINUTES * 60
    response.set_cookie(
        key="access_token",
        value=f"Bearer {access_token}",
        httponly=True,
        samesite=COOKIE_SAMESITE,
        secure=COOKIE_SECURE,
        max_age=max_age,
    )
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,  # the frontend must read this to echo it in a header
        samesite=COOKIE_SAMESITE,
        secure=COOKIE_SECURE,
        max_age=max_age,
    )
    return csrf_token


def issue_csrf_cookie(response: Response) -> str:
    """Re-issue just the CSRF cookie for an already-authenticated request.

    Self-heal for the desync described in issue_session_cookies: if a client's
    session cookie survived (so it can reach an authenticated GET) but its CSRF
    cookie did not, this hands it a fresh one without forcing a full re-login.
    Called from GET /api/auth/me, which the frontend already polls on load.
    """
    csrf_token = secrets.token_urlsafe(32)
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=False,
        samesite=COOKIE_SAMESITE,
        secure=COOKIE_SECURE,
        max_age=ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )
    return csrf_token


def clear_session_cookies(response: Response) -> None:
    response.delete_cookie("access_token")
    response.delete_cookie(CSRF_COOKIE_NAME)


def require_csrf(request: Request) -> None:
    """Double-submit CSRF check for state-changing requests.

    The session lives in a cookie, so the browser attaches it to any request an
    attacker's page can trigger. SameSite=strict blocks most of that, but it is
    a single point of failure and does nothing for a same-site-but-untrusted
    page. Requiring a header that echoes a readable cookie closes it: a
    cross-origin page can cause the cookie to be sent but cannot read it to
    construct the header.

    Requests authenticated purely by an `Authorization: Bearer` header are
    exempt — those are not cookie-driven and so are not forgeable this way.

    The token may arrive in the `X-CSRF-Token` header or, failing that, in a
    `csrf_token` query parameter. The query fallback exists for
    `navigator.sendBeacon`, which cannot set request headers at all — a fixed
    property of that API, not something the frontend can work around. Beacons
    are how the app flushes unsaved annotations and session time on `pagehide`
    and `visibilitychange`, precisely when a normal fetch may not be delivered,
    so with header-only checking every unload flush was rejected 403 and the
    work it carried was dropped (sendBeacon reports queueing, not acceptance,
    so the client could not even detect it).

    The fallback preserves the double-submit property in full: the value must
    still equal the `csrf_token` cookie, and a cross-origin page still cannot
    read that cookie to construct the parameter. What changes is only *where*
    the echo may be carried, not what has to be proven.

    Query strings are more prone to landing in access logs than headers are, so
    this is deliberately a fallback and never the preferred path; `apiFetch`
    continues to send the header for every ordinary request.
    """
    if request.method in ("GET", "HEAD", "OPTIONS", "TRACE"):
        return

    # A bearer-token client (scripts, tests) is not subject to CSRF: the token
    # is never attached automatically by a browser.
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer ") and not request.cookies.get("access_token"):
        return

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME) or request.query_params.get(CSRF_QUERY_PARAM)

    if not cookie_token or not header_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing CSRF token. Reload the page and try again.",
        )
    # Constant-time compare: the tokens are secrets of equal length.
    if not secrets.compare_digest(cookie_token, header_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token mismatch. Reload the page and try again.",
        )

def get_token(request: Request):
    token = request.cookies.get("access_token")
    if token:
        if token.startswith("Bearer "):
            return token[7:]
        return token
    
    auth = request.headers.get("Authorization")
    if auth and auth.startswith("Bearer "):
        return auth[7:]
    
    return None

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)

def verify_password(plain_password, hashed_password):
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def get_password_hash(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def get_current_user(token: Optional[str] = Depends(get_token), db: Session = Depends(get_db)):
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = db.query(models.User).filter(models.User.username == username).first()
    if user is None:
        raise credentials_exception
    return user

"""Runtime configuration, read from the environment.

Everything deployment-shaped lives here so a LAN deployment is a matter of
setting env vars, not editing source. See
.devnotes/deployment-hardening/01_HARDENING_PLAN.md.

`APP_ENV` is the master switch. In `production` the settings that are merely
inconvenient in development become fatal at startup (see `validate_config`):
a missing JWT secret or a wildcard CORS origin must never reach a machine that
other people can connect to.

`.env` is loaded here rather than only by the launcher script, so *every*
entry point sees the same configuration. Loading it in `scripts/run.ps1` alone
meant `alembic upgrade head` ran with an unconfigured environment and silently
fell back to `alembic.ini`'s SQLite URL — or, once a native Postgres was also
installed, to the wrong server on the default port.
"""
import os
import sys
from pathlib import Path


def _load_dotenv() -> None:
    """Populate os.environ from the repo-root .env, if present.

    Real environment variables always win: `.env` is the deployment's stored
    defaults, while an explicitly exported variable is a deliberate override
    (and is how the test suite pins DATA_DIR).
    """
    # Never under pytest: the suite drives configuration through monkeypatched
    # environment variables and reloads this module to assert on the result. A
    # .env reload would put the developer's real deployment values back and
    # make those assertions pass or fail based on an untracked local file.
    if "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules:
        return

    env_path = Path(__file__).resolve().parent / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        # Not fatal: a deployment may set real environment variables instead.
        # Silence here would be worse than a warning, since the symptom
        # (connecting to the wrong database) looks nothing like the cause.
        print(
            f"WARNING: {env_path} exists but python-dotenv is not installed; "
            "its values are being ignored. Run: pip install -r requirements.txt"
        )
        return
    load_dotenv(env_path, override=False)


_load_dotenv()

# DATA_DIR allows us to store persistent data on a mounted disk (like Render's Persistent Disk at /data)
DATA_DIR = os.environ.get("DATA_DIR", ".")

# Ensure the DATA_DIR exists if it's customized
if DATA_DIR != "." and not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR, exist_ok=True)

# "development" | "production". Production is the hardened profile.
APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
IS_PRODUCTION = APP_ENV == "production"

# Bind address. Defaults to loopback so an unconfigured run is never exposed;
# a LAN deployment sets APP_HOST=0.0.0.0 explicitly.
APP_HOST = os.environ.get("APP_HOST", "127.0.0.1")
APP_PORT = int(os.environ.get("APP_PORT", "8000"))

# --- Database -------------------------------------------------------------
# DATABASE_URL is authoritative. Without it we fall back to the historical
# SQLite file so existing development checkouts keep working untouched.
_default_sqlite = f"sqlite:///{os.path.join(DATA_DIR, 'workspace.db').replace(os.sep, '/')}"
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip() or _default_sqlite
IS_SQLITE = DATABASE_URL.startswith("sqlite")

# Request threadpool cap. Sync route handlers run in Starlette's anyio
# threadpool (default 40). main.py sets the live cap from this at startup.
#
# It is pinned equal to the DB pool ceiling below so that under a burst a
# request waits for a *thread* (which frees in tens of ms once a DB call
# returns) rather than acquiring a thread and then blocking up to
# pool_timeout (30s) for a connection. Matching the two removes that second,
# far slower queue. See the load-test results in
# .devnotes/deployment-hardening/05_LOAD_TEST.md.
THREADPOOL_CAP = int(os.environ.get("THREADPOOL_CAP", "40"))

# --- Request decompression -------------------------------------------------
# Ceiling on a gzipped request body *after* inflation, in bytes.
#
# The client gzips large saves (frontend/js/api.js), which turns a 1.8 MB
# annotation blob into ~9 KB on the wire — see
# .devnotes/network-lag/01_AUDIT.md C1. Inflating without a bound would let a
# small request expand until the process runs out of memory, so the middleware
# stops at this many bytes and answers 413.
#
# 64 MB is far above any real annotation set (the largest observed task is
# 15.6 MB uncompressed as of 2026-09-05 -- see
# .devnotes/server-issue-diagnosis/) while staying inside the memory of a box that is
# also running Postgres. Raise it only if a genuine payload ever approaches it.
MAX_DECOMPRESSED_BODY = int(
    os.environ.get("MAX_DECOMPRESSED_BODY", str(64 * 1024 * 1024))
)

# Connection pool sizing. Only meaningful for Postgres; SQLite ignores it.
#
# Sized from the T4.1 load test (05_LOAD_TEST.md), not a guess:
#   - 25 clients (the target) doing the real shift pattern peaked at 22
#     concurrent Postgres connections — inside pool_size alone, zero
#     pool timeouts.
#   - 40 clients (a deliberate overload) peaked at 31, saturating overflow
#     but never exhausting it, still zero errors.
# So 20 + 20 = 40 gives the 25-user target clear headroom and matches the
# threadpool cap (see above), so neither queue is the bottleneck first.
#
# Postgres max_connections must be >= pool ceiling + admin headroom
# (40 + 5 = 45 minimum; default Postgres is 100, which is fine).
#
# NOTE — multi-worker gate (D3): the app must stay a single uvicorn worker
#   because JOBS, _models, and _TASK_LOCKS are in-process state (CLAUDE.md
#   rule 9). Do not add --workers N until that state is moved out of process.
DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "20"))
DB_MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "20"))
DB_POOL_TIMEOUT = int(os.environ.get("DB_POOL_TIMEOUT", "30"))
DB_POOL_RECYCLE = int(os.environ.get("DB_POOL_RECYCLE", "1800"))

# --- Auth -----------------------------------------------------------------
JWT_SECRET = os.environ.get("JWT_SECRET", "").strip()

# Open self-registration is fine for a single-user dev box and wrong on a LAN,
# where anyone who can reach the port could mint an account.
ALLOW_REGISTRATION = os.environ.get("ALLOW_REGISTRATION", "1").strip().lower() not in ("0", "false", "no")
MIN_PASSWORD_LENGTH = int(os.environ.get("MIN_PASSWORD_LENGTH", "8"))

# Set Secure on the session cookie. Off by default because the current LAN
# deployment is plain HTTP; a Secure cookie would simply never be sent and
# would lock everyone out. Turn on together with TLS (deferred item T-1).
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0").strip().lower() in ("1", "true", "yes")
COOKIE_SAMESITE = os.environ.get("COOKIE_SAMESITE", "strict").strip().lower()

# --- Annotation history ---------------------------------------------------
# Removed. `task_annotation_history` kept the superseded annotation set on
# every save; after the normalisation it was the last place still serialising
# a task's entire annotation set, at ~1,325 ms of GIL-held CPU and a 22 MB row
# write per save on the largest task. The ANNOTATION_HISTORY_KEEP and
# ANNOTATION_HISTORY_APPEND_SKIP knobs went with it; both are ignored if still
# present in a .env. Wipe recovery is now the hourly backup.
# See .devnotes/remove-annotation-history/02_PLAN.md.

# --- CORS -----------------------------------------------------------------
# Comma-separated exact origins, e.g. "http://192.168.1.81:8000".
_raw_cors = os.environ.get("CORS_ORIGINS", "").strip()
CORS_ORIGINS = [o.strip() for o in _raw_cors.split(",") if o.strip()] if _raw_cors else []

# --- Uploads --------------------------------------------------------------
# Bounds the work one multipart request can queue up. Without this a single
# request can hold a worker thread streaming files for an unbounded time.
MAX_UPLOAD_FILES = int(os.environ.get("MAX_UPLOAD_FILES", "200"))
MAX_IMPORT_BYTES = int(os.environ.get("MAX_IMPORT_BYTES", str(300 * 1024 * 1024)))

# --- Teams ----------------------------------------------------------------
# Cap on teams one user may own, so a compromised or buggy client cannot fill
# the table. Same reasoning as MAX_UPLOAD_FILES; 50 is far above any legitimate
# use on a single-org deployment. See .devnotes/teams/01_DESIGN.md § 5.
MAX_TEAMS_PER_USER = int(os.environ.get("MAX_TEAMS_PER_USER", "50"))

# Add-member is rate limited per user because it discloses whether a username
# exists (.devnotes/teams/01_DESIGN.md § 5.1). The limit stops it being driven
# as a bulk enumeration oracle; it is not meant to constrain honest use.
ADD_MEMBER_RATE_LIMIT = int(os.environ.get("ADD_MEMBER_RATE_LIMIT", "30"))
ADD_MEMBER_RATE_WINDOW_SECONDS = int(
    os.environ.get("ADD_MEMBER_RATE_WINDOW_SECONDS", "60")
)

# --- Logging --------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
# `or` rather than a plain default: an explicitly empty LOG_DIR="" in .env is
# how a deployment says "use the default", but os.environ.get would hand back
# the empty string and disable file logging entirely.
LOG_DIR = os.environ.get("LOG_DIR", "").strip() or os.path.join(DATA_DIR, "logs")
LOG_MAX_BYTES = int(os.environ.get("LOG_MAX_BYTES", str(10 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.environ.get("LOG_BACKUP_COUNT", "5"))


def _csv(name: str, default: str) -> list:
    """Comma-separated env list, empties dropped, whitespace stripped."""
    raw = os.environ.get(name, "")
    raw = raw.strip() or default
    return [part.strip() for part in raw.split(",") if part.strip()]


def _flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


# How many days of dated logs to keep: both the `service/YYYY-MM-DD/`
# directories and the `app.log.YYYY-MM-DD` backups. Retention is by date rather
# than by size because "the last month" is the question an operator actually
# asks; a size budget answers "the last however-long 60 MB happened to cover",
# which varies with error rate (01_AUDIT.md P10).
LOG_RETENTION_DAYS = int(os.environ.get("LOG_RETENTION_DAYS", "30"))

# Per-value cap in the service log. Values are user-influenced (descriptions,
# class names), so they are truncated as well as sanitised — an unbounded value
# would let one request write an arbitrarily long line.
LOG_VALUE_MAX = int(os.environ.get("LOG_VALUE_MAX", "120"))

# --- Service (request) log ------------------------------------------------
# The structured per-request record, written by the app itself rather than
# scraped from uvicorn's stdout. See .devnotes/logging/02_PLAN.md.
SERVICE_LOG_ENABLED = _flag("SERVICE_LOG_ENABLED", True)

# Methods that get their own file inside the day's directory. Anything else
# lands in OTHER.log, so an unexpected method is still recorded.
SERVICE_LOG_METHODS = [m.upper() for m in _csv(
    "SERVICE_LOG_METHODS", "GET,POST,PATCH,DELETE"
)]

# Never logged at all: static assets and the liveness probe. These are pure
# noise with no diagnostic value, and on this deployment they outnumber API
# calls by an order of magnitude. Matched as a prefix, or as a suffix when the
# entry starts with '*'.
SERVICE_LOG_SKIP_PATHS = _csv(
    "SERVICE_LOG_SKIP_PATHS",
    "/health,/uploads,/frontend,*.js,*.css,*.png,*.jpg,*.jpeg,*.ico,*.svg,*.map",
)

# Logged, but at most once per client per window (plus every non-2xx). The task
# soft-lock heartbeat and the timer ping fire every few seconds per open task
# per annotator; unsampled they drown the file that is supposed to make saves
# findable (01_AUDIT.md P4).
SERVICE_LOG_SAMPLE_PATHS = _csv(
    "SERVICE_LOG_SAMPLE_PATHS",
    "/heartbeat,/lock-status,/release-beacon,/api/team/time,/api/time-logs/time",
)
SERVICE_LOG_SAMPLE_WINDOW = int(os.environ.get("SERVICE_LOG_SAMPLE_WINDOW", "60"))


class ConfigError(RuntimeError):
    """A deployment-configuration problem that must stop startup."""


def validate_config() -> None:
    """Fail fast on a production configuration that is unsafe to serve.

    Called from main.py at import time. These are deliberately fatal rather
    than warnings: every one of them is invisible at runtime but changes
    whether a LAN-exposed instance can be trivially compromised.
    """
    if not IS_PRODUCTION:
        return

    problems = []

    if not JWT_SECRET:
        problems.append(
            "JWT_SECRET is not set. In production the signing key must come "
            "from the environment so it survives restarts and does not depend "
            "on the working directory."
        )
    elif len(JWT_SECRET) < 32:
        problems.append(
            f"JWT_SECRET is only {len(JWT_SECRET)} characters; use at least 32 "
            "(e.g. `python -c \"import secrets; print(secrets.token_hex(32))\"`)."
        )

    if not CORS_ORIGINS:
        problems.append(
            "CORS_ORIGINS is empty. Set it to the exact origin annotators use, "
            "e.g. CORS_ORIGINS=http://192.168.1.81:8000"
        )
    elif "*" in CORS_ORIGINS:
        problems.append(
            "CORS_ORIGINS contains '*'. A wildcard origin cannot be combined "
            "with cookie credentials; list exact origins instead."
        )

    if problems:
        raise ConfigError(
            "Refusing to start with an unsafe production configuration:\n  - "
            + "\n  - ".join(problems)
        )

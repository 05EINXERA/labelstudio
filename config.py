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
# NOTE — Worker architecture:
#   The application runs as a single Uvicorn worker process. HTTP concurrency
#   is handled by Starlette's AnyIO threadpool (THREADPOOL_CAP=40), matching
#   the DB pool ceiling. Running a single worker ensures that heavy PyTorch
#   model singletons (YOLO, SAM, CLIP) and interactive SAM feature caches
#   are shared in memory without multiplying VRAM/RAM footprint across processes.
DB_POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "20"))
DB_MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "20"))
DB_POOL_TIMEOUT = int(os.environ.get("DB_POOL_TIMEOUT", "30"))
DB_POOL_RECYCLE = int(os.environ.get("DB_POOL_RECYCLE", "1800"))

# --- ML & Inference Concurrency ------------------------------------------
# Bounds the number of concurrent heavy ML inference jobs running simultaneously.
# Gating inference to 1-2 concurrent jobs prevents GPU VRAM and CPU thrashing
# under simultaneous AI requests from multiple annotators.
MAX_INFERENCE_CONCURRENCY = int(os.environ.get("MAX_INFERENCE_CONCURRENCY", "1"))

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

# --- CORS -----------------------------------------------------------------
# Comma-separated exact origins, e.g. "http://192.168.1.81:8000".
_raw_cors = os.environ.get("CORS_ORIGINS", "").strip()
CORS_ORIGINS = [o.strip() for o in _raw_cors.split(",") if o.strip()] if _raw_cors else []

# --- Uploads --------------------------------------------------------------
# Bounds the work one multipart request can queue up. Without this a single
# request can hold a worker thread streaming files for an unbounded time.
MAX_UPLOAD_FILES = int(os.environ.get("MAX_UPLOAD_FILES", "200"))
MAX_IMPORT_BYTES = int(os.environ.get("MAX_IMPORT_BYTES", str(300 * 1024 * 1024)))

# --- Logging --------------------------------------------------------------
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").strip().upper()
# `or` rather than a plain default: an explicitly empty LOG_DIR="" in .env is
# how a deployment says "use the default", but os.environ.get would hand back
# the empty string and disable file logging entirely.
LOG_DIR = os.environ.get("LOG_DIR", "").strip() or os.path.join(DATA_DIR, "logs")
LOG_MAX_BYTES = int(os.environ.get("LOG_MAX_BYTES", str(10 * 1024 * 1024)))
LOG_BACKUP_COUNT = int(os.environ.get("LOG_BACKUP_COUNT", "5"))


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

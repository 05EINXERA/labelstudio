"""FastAPI app assembly: logging, config validation, middleware, routers, static files.

Deployment configuration is read from the environment via `config.py`. In
production (`APP_ENV=production`) an unsafe configuration stops startup rather
than serving — see `config.validate_config()`.

The schema is NOT created here. `Base.metadata.create_all` only ever creates
missing tables and silently ignores changed columns, so on a shared instance it
hides schema drift. Run `alembic upgrade head` as a deploy step instead
(CLAUDE.md rule 8, .devnotes/deployment-hardening/01_HARDENING_PLAN.md C-1).
"""
import logging
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import text

from config import (
    APP_HOST,
    APP_PORT,
    CORS_ORIGINS,
    DATA_DIR,
    IS_PRODUCTION,
    THREADPOOL_CAP,
    validate_config,
)
from logging_config import configure_logging

# Validated before the routers are imported: api.auth resolves the signing key
# at import time, so importing it first would surface a missing JWT_SECRET as a
# bare RuntimeError instead of the actionable message validate_config gives.
validate_config()
configure_logging()

from api.routers import projects, tasks, team, teams, grants, time_logs, data, detect, label_studio, labels, auth, imports, exports  # noqa: E402
from database import engine  # noqa: E402

logger = logging.getLogger(__name__)

app = FastAPI(title="Annotation Workspace")


@app.on_event("startup")
async def _set_threadpool_capacity() -> None:
    """Pin the anyio threadpool cap that carries sync route handlers.

    Sized in config.THREADPOOL_CAP to match the DB pool ceiling so a burst
    queues on threads (which free in tens of ms) rather than on pool_timeout.
    Set here, inside the running event loop, because the limiter is loop-bound.
    See .devnotes/deployment-hardening/05_LOAD_TEST.md.
    """
    try:
        from anyio import to_thread
        limiter = to_thread.current_default_thread_limiter()
        limiter.total_tokens = THREADPOOL_CAP
        logger.info("Request threadpool cap set to %d", THREADPOOL_CAP)
    except Exception as exc:  # pragma: no cover - never worth failing startup
        logger.warning("Could not set threadpool cap (%s); using anyio default", exc)


@app.middleware("http")
async def add_security_and_cache_headers(request, call_next):
    response = await call_next(request)
    path = request.url.path

    # Cheap, always-correct hardening headers. A CSP is deliberately not set
    # here — the annotation canvas uses inline handlers today, so a meaningful
    # policy needs frontend work first (tracked as deferred item T-4).
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"

    if (
        path.endswith(".js") or
        path.endswith(".html") or
        path.endswith(".css") or
        path == "/" or
        path.startswith("/frontend")
    ):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# Exact origins only. A wildcard cannot be combined with cookie credentials, and
# on a LAN it would let any page on the network call the API with the
# annotator's session attached. validate_config() rejects "*" in production.
if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
    )
else:
    # No CORS middleware at all: same-origin requests (how the app is actually
    # used) do not need it, and adding none is safer than adding a permissive
    # one. Development only — production requires CORS_ORIGINS.
    logger.info("CORS_ORIGINS not set; cross-origin requests are not permitted.")

# Include routers
app.include_router(data.router)
app.include_router(projects.router)
app.include_router(tasks.router)
app.include_router(teams.router)
app.include_router(grants.router)
app.include_router(time_logs.router)
# Deprecated alias for /api/time-logs, kept one release for cached JS bundles.
app.include_router(team.router)
app.include_router(detect.router)
app.include_router(label_studio.router)
app.include_router(labels.router)
app.include_router(auth.router)
app.include_router(imports.router)
app.include_router(exports.router)


@app.get("/health")
def health():
    """Liveness + database reachability, for the operator and any supervisor.

    Deliberately unauthenticated and free of detail: it reports whether the
    process can serve, not anything about its contents.
    """
    db_ok = True
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # surface as unhealthy, never as a 500
        db_ok = False
        logger.error("Health check database probe failed: %s", exc)

    return {
        "status": "ok" if db_ok else "degraded",
        "database": "up" if db_ok else "down",
        "environment": "production" if IS_PRODUCTION else "development",
    }


# Ensure uploads directory exists
uploads_dir = os.path.join(DATA_DIR, "uploads")
os.makedirs(uploads_dir, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=uploads_dir), name="uploads")

# Serve frontend static files
# Route the root URL to index.html
@app.get("/")
def read_index():
    return FileResponse("frontend/index.html")

# Mount the rest of the frontend directory
app.mount("/", StaticFiles(directory="frontend"), name="frontend")

if __name__ == "__main__":
    import uvicorn
    logger.info("App running at http://%s:%s/", APP_HOST, APP_PORT)
    uvicorn.run("main:app", host=APP_HOST, port=APP_PORT, reload=False)

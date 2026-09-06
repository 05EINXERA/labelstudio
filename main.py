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
from fastapi.middleware.gzip import GZipMiddleware
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

from api.middleware import ServiceLogMiddleware  # noqa: E402
from api.compression import RequestDecompressionMiddleware  # noqa: E402
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
        # `no-cache`, NOT `no-store`. The two sound alike and are very
        # different: `no-store` forbids writing the response to cache at all,
        # so every navigation and every reload re-downloaded the whole ~626 KB
        # of JS and CSS. `no-cache` allows storage but requires revalidation
        # before reuse — the server is still consulted on every request, so the
        # freshness guarantee is exactly as strong as before, but an unchanged
        # file comes back as a 304 with an empty body instead of the full
        # bytes.
        #
        # This is safe because StaticFiles already sends both validators
        # (verified: `ETag` and `Last-Modified` are present, and a request
        # carrying If-None-Match gets 304 with 0 bytes vs 200 with 63,941 for
        # styles.css). Nothing about stale-bundle risk changes: a client still
        # asks the server about every asset on every load, and the `?v=` import
        # pins (CLAUDE.md rule 13) remain the invalidation mechanism.
        #
        # Deliberately stopping short of `max-age=…, immutable` on versioned
        # assets, which would remove the round trip entirely and make reloads
        # truly instant. That needs an audit that every asset really is
        # `?v=`-pinned first — an unpinned file cached for a year is a bad
        # failure mode. Tracked as T13 in
        # .devnotes/server-optimization/07_RECOMMENDATIONS.md.
        response.headers["Cache-Control"] = "no-cache"
        # `Pragma`/`Expires` are HTTP/1.0 relics that only ever meant
        # "don't cache". Keeping them would contradict the header above for any
        # intermediary that still reads them, so they are dropped rather than
        # left to fight it. Cache-Control is authoritative for every client
        # this app serves.
    elif path.startswith("/uploads/"):
        # Task images. Unlike JS/CSS these are genuinely immutable: every
        # upload is written under a fresh uuid4().hex filename
        # (_save_upload, api/routers/projects.py) and nothing in this codebase
        # ever writes into an existing upload path afterward — a changed image
        # is a new file with a new name, never a mutation in place. A URL's
        # bytes therefore cannot change under an annotator, so caching it for a
        # year and skipping revalidation entirely is safe, where doing the same
        # for JS/CSS above would not be (see the no-cache branch's reasoning).
        #
        # This does not touch image quality or resolution in any way — same
        # file, same bytes, served with a different header. Distinct from T9
        # (a downscaled derivative for the canvas), which is deliberately not
        # implemented: that would change what an annotator draws against and
        # needs a careful coordinate-mapping design first.
        #
        # The payoff: opening a task a second time (switching back to a
        # previous image, or reopening after a reload) costs zero requests
        # instead of a conditional round trip, on files that average ~9 MB
        # (.devnotes/server-optimization/02_FINDINGS.md, F5).
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


# Compress text responses. Nothing here is served compressed otherwise: the JS
# module graph (~562 KB across 50 files), styles.css (~64 KB) and every JSON
# body went over the LAN raw. JSON and JS are the most compressible content
# there is — annotation blobs and task lists repeat the same keys once per
# shape — so this is 80-90% off the wire for a few ms of CPU per response.
# See .devnotes/server-optimization/06_CACHING.md (F3).
#
# Added *after* the header middleware deliberately: Starlette runs the
# last-added middleware outermost, so GZip wraps the header pass and compresses
# the finished response. The security and Cache-Control headers above are set
# on the inner response and survive untouched.
#
# Already-compressed formats (the JPEG/PNG/WebP uploads) are not re-compressed
# by GZipMiddleware, which is correct — shrinking those is an image-pipeline
# problem, not a transport one.
#
# minimum_size=500 is the intent — below roughly this the gzip header costs
# more than the compression saves. Note it does NOT currently take effect:
# GZipMiddleware only consults it when the body length is known up front, and
# `add_security_and_cache_headers` above is a BaseHTTPMiddleware, which makes
# every response streaming with no content-length. So small bodies are
# compressed too. The waste is a few hundred bytes on tiny acknowledgements —
# far smaller than what this middleware saves — so it is left as-is and
# documented rather than fixed by rewriting the header middleware as pure ASGI.
# Pinned by tests/test_response_compression.py.
#
# compresslevel=6, not Starlette's default of 9. Level 9 spends a great deal of
# CPU for almost nothing on this payload, and that CPU is GIL-held, so it
# stalls every other request in the process exactly as the annotation JSON
# parsing used to. Measured on a 14.39 MB task-detail body:
#
#     level 9 : 1387 ms -> 1.36 MB
#     level 6 :  247 ms -> 1.32 MB      <- 5.6x faster, 3% larger
#     level 1 :   59 ms -> 2.20 MB
#
# Level 6 is zlib's own default for good reason. Opening a large task measured
# 2,965 ms gzipped against 582 ms uncompressed over loopback, so on a fast LAN
# the compression was costing more than the bytes it saved.
#
# Do NOT raise this back to 9 without re-measuring: on the largest tasks it is
# worth well over a second of stalled event loop per request.
app.add_middleware(GZipMiddleware, minimum_size=500, compresslevel=6)


# The structured per-request log (.devnotes/logging/02_PLAN.md). This is what
# replaces uvicorn's stdout access log as the thing an operator actually reads;
# uvicorn's own lines stay on for one release as a cross-check (plan §9).
#
# Registered last, so Starlette runs it OUTERMOST — outside gzip and the header
# pass. That is deliberate, and it is the trade the plan's "innermost" note got
# wrong: outermost means the recorded duration includes a few milliseconds of
# compression, but it also means every exception from every inner layer passes
# through this middleware and gets logged. An inner position would time the
# handler slightly more precisely and miss failures raised above it, which is a
# bad exchange for a log whose whole purpose is finding what went wrong.
app.add_middleware(ServiceLogMiddleware)


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


# Inflate gzipped request bodies (.devnotes/network-lag/01_AUDIT.md C1). The
# client compresses large saves, turning a ~1.8 MB annotation blob into ~9 KB on
# the wire; this is the half that turns it back.
#
# Registered LAST, so Starlette runs it OUTERMOST — outside the service log, the
# header pass and gzip. That ordering is required rather than merely tidy: every
# layer below reads or reasons about the body, so the body has to already be
# plaintext by the time any of them run. In particular the router's
# `await request.json()` must never see compressed bytes.
#
# CSRF is unaffected by position: require_csrf reads a header and a query
# parameter, never the body, so the sendBeacon query-param fallback keeps
# working exactly as before.
app.add_middleware(RequestDecompressionMiddleware)

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

# The annotation manual is vendored under `frontend/manual/` and served by this
# same process rather than a second service on its own port. It is static files
# (~4 MB, mostly immutable JPEGs), so it adds no failure mode of its own, and
# the ETag/304 middleware above already gives it the cache behaviour a separate
# nginx would have been stood up for. Serving it here also keeps every link to
# it root-relative, so no hostname or port is baked into the frontend bundle.
#
# The `StaticFiles` mount below already serves `/manual/index.html` and the
# assets; this route only exists so the bare directory URL resolves, since
# `StaticFiles` does not imply an index for subdirectories.
@app.get("/manual/", include_in_schema=False)
def read_manual():
    return FileResponse("frontend/manual/index.html")

# Mount the rest of the frontend directory
app.mount("/", StaticFiles(directory="frontend"), name="frontend")

if __name__ == "__main__":
    import uvicorn
    logger.info("App running at http://%s:%s/", APP_HOST, APP_PORT)
    uvicorn.run("main:app", host=APP_HOST, port=APP_PORT, reload=False)

"""Network telemetry ingest — temporary.

The browser collector (frontend/js/telemetry/) buffers per-request timing and
posts it here every few minutes. Each batch becomes one line in
`TELEMETRY_DIR/<local date>/batches.ndjson`. Design, and how to remove it:
.devnotes/frontend-telemetry/ (02_DESIGN.md §2, 06_ROLLBACK_AND_CLEANUP.md).

**No database.** Nothing here opens a session: a table would need a migration
to add and another to remove (CLAUDE.md rule 8), and would ride along in the
hourly dumps. A file per day is deleted with one command.

**Telemetry is the thing that gives way.** A write failure is logged and
answered 204, so a full or broken disk never turns into client retries.

Every path answers 404 while `TELEMETRY_ENABLED` is off, exactly as if the
router were not mounted. The flag is read per request so `.env` plus a restart
is the only switch, and tests can flip it.
"""
import json
import logging
import os
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

import config
import models
from api.auth import get_current_user, require_csrf
from api.middleware import _client_ip
from logging_service import log_event
from schemas import TelemetryConfig

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/telemetry",
    tags=["telemetry"],
    dependencies=[Depends(get_current_user), Depends(require_csrf)],
)

# Serialises appends to the day file. Batches arrive a few per minute
# office-wide, so contention is nil; the lock only guarantees two lines never
# interleave.
_WRITE_LOCK = threading.Lock()


def _require_enabled(request: Request) -> None:
    """404 while off, or for a client outside TELEMETRY_ONLY_IPS."""
    if not config.TELEMETRY_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")
    if config.TELEMETRY_ONLY_IPS and _client_ip(request) not in config.TELEMETRY_ONLY_IPS:
        raise HTTPException(status_code=404, detail="Not Found")


def _day_file(now: datetime) -> str:
    # Local date, matching logs/service/<date>/: "the day" means the office's.
    day = now.astimezone().strftime("%Y-%m-%d")
    return os.path.join(config.TELEMETRY_DIR, day, "batches.ndjson")


@router.get("/config", response_model=TelemetryConfig)
def telemetry_config(request: Request):
    _require_enabled(request)
    return TelemetryConfig(
        enabled=True,
        flush_seconds=config.TELEMETRY_FLUSH_SECONDS,
        probes=config.TELEMETRY_PROBES_ENABLED,
        probe_seconds=config.TELEMETRY_PROBE_SECONDS,
        probe_bytes=config.TELEMETRY_PROBE_BYTES if config.TELEMETRY_PROBES_ENABLED else 0,
        server_now=int(datetime.now(timezone.utc).timestamp() * 1000),
    )


def _parse_batch(body: bytes) -> dict:
    """Shape check only. Records are not validated field by field: that would
    be CPU spent on a nice-to-have, and the report script is tolerant."""
    try:
        batch = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Telemetry batch is not valid JSON.")
    if not isinstance(batch, dict) or not isinstance(batch.get("records"), list):
        raise HTTPException(status_code=400, detail="Telemetry batch needs a records list.")
    if len(batch["records"]) > config.TELEMETRY_MAX_RECORDS:
        raise HTTPException(status_code=413, detail="Telemetry batch has too many records.")
    return batch


def _append(path: str, line: str) -> None:
    with _WRITE_LOCK:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def _ingest(body: bytes, ip: str, username: str) -> None:
    batch = _parse_batch(body)
    now = datetime.now(timezone.utc)
    line = json.dumps(
        {"received_at": now.isoformat(), "ip": ip, "user": username, "batch": batch},
        separators=(",", ":"),
    )
    path = _day_file(now)
    try:
        _append(path, line)
    except OSError:
        logger.warning("telemetry write to %s failed; batch dropped", path, exc_info=True)
        log_event("telemetry.write_failed", level="WARNING", records=len(batch["records"]))


@router.post("/batch", status_code=204)
async def telemetry_batch(
    request: Request,
    current_user: models.User = Depends(get_current_user),
):
    """Append one batch. `async` only to read the body; the parse and the file
    write run on the threadpool so the event loop never waits on either."""
    _require_enabled(request)

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > config.TELEMETRY_MAX_BATCH_BYTES:
        raise HTTPException(status_code=413, detail="Telemetry batch is too large.")
    # Already inflated by RequestDecompressionMiddleware when gzipped, so this
    # is the decompressed size.
    body = await request.body()
    if len(body) > config.TELEMETRY_MAX_BATCH_BYTES:
        raise HTTPException(status_code=413, detail="Telemetry batch is too large.")

    await run_in_threadpool(_ingest, body, _client_ip(request), current_user.username)
    return Response(status_code=204)


# --- bandwidth probes (TELEMETRY_PROBES_ENABLED) ----------------------------
#
# Passive capture cannot show a seat's capacity when that seat is barely using
# it; these measure it directly (02_DESIGN.md §3.5). Separate switch because
# they put real bytes on the wire.

_PROBE_PAYLOAD = b""
_PROBE_LOCK = threading.Lock()


def _require_probes(request: Request) -> None:
    _require_enabled(request)
    if not config.TELEMETRY_PROBES_ENABLED:
        raise HTTPException(status_code=404, detail="Not Found")


def _probe_payload() -> bytes:
    """Incompressible bytes, generated once per process on first use: random
    data so neither gzip nor any link-level compression flatters the figure."""
    global _PROBE_PAYLOAD
    with _PROBE_LOCK:
        if len(_PROBE_PAYLOAD) < config.TELEMETRY_PROBE_BYTES:
            _PROBE_PAYLOAD = os.urandom(config.TELEMETRY_PROBE_BYTES)
        return _PROBE_PAYLOAD


@router.get("/probe/down")
def probe_down(request: Request, n: int = 0):
    """`n` random bytes, capped at TELEMETRY_PROBE_BYTES.

    `Content-Encoding: identity` is preset on purpose: Starlette's
    GZipMiddleware leaves a response alone when that header already exists, so
    no CPU is spent trying to compress random data. `no-store` plus the
    client's nonce keep the browser cache out of the measurement.
    """
    _require_probes(request)
    size = max(1, min(n or config.TELEMETRY_PROBE_BYTES, config.TELEMETRY_PROBE_BYTES))
    return Response(
        content=_probe_payload()[:size],
        media_type="application/octet-stream",
        headers={"Content-Encoding": "identity", "Cache-Control": "no-store"},
    )


@router.post("/probe/up", status_code=204)
async def probe_up(request: Request):
    """Read and discard an upload, chunk by chunk, never holding it whole.

    X-Server-Ms starts before the body is read, so for this request it is
    roughly the upload's transfer time: throughput = bytes / srv.
    """
    _require_probes(request)
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > config.TELEMETRY_PROBE_BYTES:
            raise HTTPException(status_code=413, detail="Probe upload is too large.")
    return Response(status_code=204)

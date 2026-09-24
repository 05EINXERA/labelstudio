"""What a heavy job runs, and the entry point of the child process that runs it.

    python -m jobs.worker <spec.json>

The spec names the job kind, its inputs and where to write its outputs. The
child writes the result JSON (and, for an export, the body file) atomically,
then exits. The parent (`jobs/runner.py`) reads the result after the exit.

`execute(spec)` is the same function in every HEAVY_JOB_MODE: the child calls
it in `process` mode, a thread calls it in `thread` mode, and the request
calls it in `inline` mode (tests). One code path, so the tests exercise what
production runs.

Import discipline, which is what makes the child cheap and safe (0.65 s cold
start, measured on the deploy box): this module and everything it imports must
never import `main`, a router, or `detector`. A spawned child re-imports its
module graph, and `detector` loads ML weights. The in-process dicts of rule 9
(`JOBS`, `_models`, `_TASK_LOCKS`) are neither imported nor touched here.
`tests/test_heavy_jobs.py` pins this.
"""
import json
import logging
import os
import sys
import time

from sqlalchemy.orm import Session

import database
from api.export_service import build_export
from api.import_service import ImportRejected, apply_import, preview_import
from database import commit_with_retry
from logging_service import RequestContext, bind_context, new_request_id, reset_context
from schemas import ExportRequest

logger = logging.getLogger("jobs.worker")


def _session() -> Session:
    # expire_on_commit=False: build_export ends its read transaction before the
    # CPU phase and keeps using the loaded objects (02_ISSUES.md I-7).
    return Session(bind=database.engine, expire_on_commit=False, autoflush=False)


def write_atomic(path: str, data: bytes) -> None:
    """Write via a temp file and rename, so a reader never sees half a file."""
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _run_export(spec: dict) -> dict:
    req = ExportRequest(**spec["request"])
    db = _session()
    try:
        body, meta = build_export(db, req, spec["project_id"])
        # Image dimensions recovered from disk during the build: one short
        # write transaction at the end, not a snapshot held throughout.
        commit_with_retry(db)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    write_atomic(spec["body_path"], body)
    meta["bytes"] = len(body)
    return {"ok": True, "meta": meta}


def _run_import(spec: dict) -> dict:
    with open(spec["upload_path"], "rb") as fh:
        raw = fh.read()
    db = _session()
    try:
        if spec["mode"] == "preview":
            response = preview_import(db, spec["project_id"], spec["filename"], raw)
            db.rollback()   # a preview never writes
        else:
            response = apply_import(db, spec["project_id"], spec["mode"], spec["filename"], raw)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {"ok": True, "response": response}


def _run_test_load(spec: dict) -> dict:
    """`sleep` and `spin` kinds exist for the isolation and timeout tests.

    Not reachable from HTTP: only the runner's Python API submits a kind.
    """
    deadline = time.monotonic() + float(spec["seconds"])
    if spec["kind"] == "sleep":
        time.sleep(float(spec["seconds"]))
    else:
        n = 0
        while time.monotonic() < deadline:
            n += 1
    return {"ok": True, "meta": {}}


_KINDS = {"export": _run_export, "import": _run_import, "sleep": _run_test_load, "spin": _run_test_load}


def execute(spec: dict) -> dict:
    """Run one job and return its result dict. Never raises.

    `log_event` is request-scoped and a job is not a request, so a context is
    bound here and whatever the job recorded (label.auto_create,
    import.annotations) is returned under "event" for the parent to replay onto
    the request that is waiting for it.
    """
    ctx = RequestContext(new_request_id(), time.monotonic())
    token = bind_context(ctx)
    started = time.monotonic()
    try:
        result = _KINDS[spec["kind"]](spec)
    except ImportRejected as exc:
        result = {"ok": False, "status": 422, "error": str(exc)}
    except Exception:
        logger.exception("Heavy job %s (%s) failed", spec.get("job_id"), spec.get("kind"))
        result = {"ok": False, "status": 500, "error": f"{spec.get('kind', 'job').capitalize()} failed."}
    finally:
        reset_context(token)
    result["duration_ms"] = int((time.monotonic() - started) * 1000)
    result["event"] = (
        {"event": ctx.event, "level": ctx.level or "INFO", "fields": ctx.fields}
        if ctx.event else None
    )
    return result


def _lower_priority() -> None:
    """Below-normal priority, so a CPU-maxing job yields to uvicorn and Postgres.

    On Windows the parent already starts the child with
    BELOW_NORMAL_PRIORITY_CLASS; this covers every other platform.
    """
    if hasattr(os, "nice"):
        try:
            os.nice(10)
        except OSError as exc:
            logger.warning("Could not lower the job's priority: %s", exc)


def main(argv) -> int:
    # stdout only. The parent owns app.log, and a second process rotating the
    # same TimedRotatingFileHandler file fails on Windows. The child's stdout is
    # inherited, so its lines still reach the service's stdout.log.
    logging.basicConfig(
        level=logging.INFO, stream=sys.stdout,
        format="%(asctime)s %(levelname)-8s [job] %(name)s: %(message)s",
    )
    _lower_priority()
    with open(argv[1], encoding="utf-8") as fh:
        spec = json.load(fh)
    result = execute(spec)
    write_atomic(spec["result_path"], json.dumps(result, default=str).encode("utf-8"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

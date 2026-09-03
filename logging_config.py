"""Application logging setup.

The app previously wrote bare tracebacks to stdout, which on a supervised
laptop deployment means they are lost the moment the console scrolls or the
service restarts. This routes everything through the root logger to a dated
file as well as the console.

Call `configure_logging()` once, early in main.py.

This is the *application* log — what the code chose to say. The per-request
record lives in `logging_service.py` and writes under `logs/service/`; the two
are joined by the `req=` correlation id that `RequestIdFilter` stamps onto
every record here. See `.devnotes/logging/02_PLAN.md` §6.
"""
import logging
import logging.handlers
import os
import sys

import logging_service
from config import LOG_DIR, LOG_LEVEL, LOG_RETENTION_DAYS

# `[%(req)s]` is filled by RequestIdFilter below, which guarantees the field
# exists on every record — including ones logged outside a request, where it is
# a dash. A missing field would raise inside the formatter, i.e. logging itself
# would become the failure.
_LOG_FORMAT = "%(asctime)s %(levelname)-8s [%(req)s] %(name)s: %(message)s"
_configured = False


class RequestIdFilter(logging.Filter):
    """Stamp the in-flight request's correlation id onto every record.

    This is what makes a traceback in app.log joinable to the request that
    caused it. Previously the only link was timestamp proximity, which on a
    box serving ~25 concurrent annotators is a guess (01_AUDIT.md P8).
    """

    def filter(self, record) -> bool:
        try:
            ctx = logging_service.current_context()
            record.req = ctx.req_id if ctx is not None else "-"
        except Exception:  # pragma: no cover - a filter must never drop a record
            record.req = "-"
        return True


def configure_logging() -> None:
    """Attach console + dated-file handlers to the root logger.

    Idempotent: repeated calls (test collection, a reload) do not stack
    duplicate handlers that would multiply every log line.
    """
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    formatter = logging.Formatter(_LOG_FORMAT)
    request_ids = RequestIdFilter()

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    console.addFilter(request_ids)
    root.addHandler(console)

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        # Rotated by date, not by size. Size-based rotation kept an unknown
        # span of history — `app.log.3` gave no hint what dates it covered, and
        # the window shrank exactly when errors got interesting. This file is
        # ~310 KB after weeks, so the burst that size rotation guarded against
        # is not a real risk here (01_AUDIT.md P10).
        file_handler = logging.handlers.TimedRotatingFileHandler(
            os.path.join(LOG_DIR, "app.log"),
            when="midnight",
            backupCount=max(LOG_RETENTION_DAYS, 1),
            encoding="utf-8",
        )
        file_handler.suffix = "%Y-%m-%d"
        file_handler.setFormatter(formatter)
        file_handler.addFilter(request_ids)
        root.addHandler(file_handler)
    except OSError as exc:
        # A read-only or missing log directory must not stop the server from
        # serving; console logging still works.
        root.warning("File logging disabled, could not use %s: %s", LOG_DIR, exc)

    # Sweep aged-out service log directories at startup as well as on each
    # date rollover, so a box that was off for a month still prunes.
    try:
        logging_service.writer.sweep_old()
    except Exception:  # pragma: no cover - never fail startup over housekeeping
        root.warning("Service log retention sweep failed at startup", exc_info=True)

    _configured = True

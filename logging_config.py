"""Application logging setup.

The app previously wrote bare tracebacks to stdout, which on a supervised
laptop deployment means they are lost the moment the console scrolls or the
service restarts. This routes everything through the root logger to a rotating
file (so a long-running instance cannot fill the disk) as well as the console.

Call `configure_logging()` once, early in main.py.
"""
import logging
import logging.handlers
import os
import sys

from config import LOG_BACKUP_COUNT, LOG_DIR, LOG_LEVEL, LOG_MAX_BYTES

_LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
_configured = False


def configure_logging() -> None:
    """Attach console + rotating-file handlers to the root logger.

    Idempotent: repeated calls (test collection, a reload) do not stack
    duplicate handlers that would multiply every log line.
    """
    global _configured
    if _configured:
        return

    root = logging.getLogger()
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    formatter = logging.Formatter(_LOG_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, "app.log"),
            maxBytes=LOG_MAX_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError as exc:
        # A read-only or missing log directory must not stop the server from
        # serving; console logging still works.
        root.warning("File logging disabled, could not use %s: %s", LOG_DIR, exc)

    _configured = True

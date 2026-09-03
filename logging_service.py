"""The structured per-request service log.

The request record used to be uvicorn's stdout, scraped by the Windows-service
wrapper into one unbounded `service.log` (56 MB and growing, no rotation, no
date on the access lines, no authenticated user, no semantics). That file could
prove *that* a save happened and nothing at all about *what* it contained,
which is exactly the question an annotator reporting lost work asks. See
`.devnotes/logging/01_AUDIT.md`.

This module writes that record from inside the app instead, where the user, the
duration, the task id and the object count are all in reach. Layout:

    <LOG_DIR>/service/<YYYY-MM-DD>/<METHOD>.log
    <LOG_DIR>/service/<YYYY-MM-DD>/errors.log     every non-2xx, duplicated

One line per request:

    <ts> <LEVEL> <method> <path> <status> <ms>ms user=<u> ip=<a> req=<id> [k=v ...]

Everything after `req=` is contributed by the endpoint through `log_event()`,
which merges fields into a per-request `contextvars` bag that the middleware
drains when the response is produced. A contextvar rather than an attribute on
`Request` because the code with the interesting numbers (the annotation
clear-guard, the history recorder) sits several frames below the route and
never receives the request object.

Nothing here may raise into a request. A logging failure degrades to a warning
on the app logger — the same discipline `_record_annotation_history` follows,
for the same reason: a safety net that can drop what it is catching is worse
than no net.
"""
import contextvars
import datetime
import logging
import os
import shutil
import threading
import uuid

from config import (
    LOG_DIR,
    LOG_RETENTION_DAYS,
    LOG_VALUE_MAX,
    SERVICE_LOG_ENABLED,
    SERVICE_LOG_METHODS,
    SERVICE_LOG_SAMPLE_PATHS,
    SERVICE_LOG_SAMPLE_WINDOW,
    SERVICE_LOG_SKIP_PATHS,
)

logger = logging.getLogger(__name__)

SERVICE_LOG_DIR = os.path.join(LOG_DIR, "service")

# Keys whose values are never written, whatever a call site passes. No current
# caller passes any of them; this is here so that a future one cannot put a
# credential in a plaintext file by accident.
_REDACTED_KEYS = frozenset({
    "password", "new_password", "old_password", "token", "access_token",
    "secret", "csrf", "csrf_token", "authorization", "cookie", "annotations",
})

_MISSING = "-"


# --------------------------------------------------------------------------
# Per-request context
# --------------------------------------------------------------------------

class RequestContext:
    """Everything known about the request in flight.

    Mutable and single-request-scoped: the middleware creates one, endpoints
    add to it, the middleware reads it once and drops it.
    """

    __slots__ = ("req_id", "started", "user", "fields", "event", "level")

    def __init__(self, req_id: str, started: float):
        self.req_id = req_id
        self.started = started
        self.user = None
        self.event = None
        self.level = None
        self.fields = {}


_ctx: contextvars.ContextVar = contextvars.ContextVar("service_log_ctx", default=None)


def new_request_id() -> str:
    return uuid.uuid4().hex[:8]


def bind_context(ctx: RequestContext):
    return _ctx.set(ctx)


def reset_context(token) -> None:
    _ctx.reset(token)


def current_context():
    return _ctx.get()


def set_user(username) -> None:
    """Record who the authenticated caller is.

    Called from `get_current_user` as a side effect, because the middleware
    starts before dependency resolution and so cannot know the user itself
    without repeating the token decode and a database lookup.
    """
    ctx = _ctx.get()
    if ctx is not None and username:
        ctx.user = username


def log_event(event: str, level: str = "INFO", **fields) -> None:
    """Attach an event name and detail fields to the line for this request.

    Callable from anywhere inside a request, including helpers far below the
    route. Outside a request (a background job, a script, a test that never
    went through the middleware) it is a no-op rather than an error — the
    caller should not have to know which context it is in.

    The last call wins for `event`, and fields merge, so a handler can record a
    baseline early and refine it once the outcome is known.
    """
    try:
        ctx = _ctx.get()
        if ctx is None:
            return
        ctx.event = event
        # WARN/ERROR stick: once a request has done something destructive or
        # gone wrong, a later INFO detail must not quietly downgrade the line
        # out of the `grep WARN` destructive-action trail.
        if level != "INFO" or ctx.level is None:
            if _rank(level) >= _rank(ctx.level):
                ctx.level = level
        for key, value in fields.items():
            ctx.fields[key] = value
    except Exception:  # pragma: no cover - logging must never break a request
        logger.warning("log_event(%s) failed", event, exc_info=True)


_LEVEL_RANK = {None: -1, "DEBUG": 0, "INFO": 1, "WARN": 2, "WARNING": 2, "ERROR": 3}


def _rank(level) -> int:
    return _LEVEL_RANK.get(level, 1)


# --------------------------------------------------------------------------
# Value formatting
# --------------------------------------------------------------------------

def sanitize_value(value) -> str:
    """Render one field value so it cannot forge another field.

    Values reach here from user-controlled text (task descriptions, class
    names). Unescaped, a value containing a space and an `=` would parse as an
    extra key on the line, and one containing a newline would parse as an extra
    *line*. Whitespace collapses to `_`, `=` is dropped, and the result is
    truncated — an unbounded value would let a single request write an
    arbitrarily long line into a shared file.
    """
    if value is None:
        return _MISSING
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if not text:
        return _MISSING
    out = []
    for char in text:
        if char.isspace():
            out.append("_")
        elif char == "=":
            continue
        else:
            out.append(char)
    text = "".join(out)
    if len(text) > LOG_VALUE_MAX:
        text = text[:LOG_VALUE_MAX] + "~"
    return text or _MISSING


def format_fields(fields: dict) -> str:
    parts = []
    for key, value in fields.items():
        if key.lower() in _REDACTED_KEYS:
            parts.append(f"{key}=<redacted>")
            continue
        parts.append(f"{key}={sanitize_value(value)}")
    return " ".join(parts)


# --------------------------------------------------------------------------
# Path classification
# --------------------------------------------------------------------------

def _matches(path: str, patterns) -> bool:
    """Prefix match, or suffix match for a pattern starting with '*'."""
    for pattern in patterns:
        if pattern.startswith("*"):
            if path.endswith(pattern[1:]):
                return True
        elif path.startswith(pattern):
            return True
        elif pattern in path:
            # Sampled entries like '/heartbeat' sit at the end of a
            # parameterised path (/api/tasks/728/heartbeat), so a bare prefix
            # test would never match them.
            return True
    return False


def should_skip(path: str) -> bool:
    return _matches(path, SERVICE_LOG_SKIP_PATHS)


def is_sampled_path(path: str) -> bool:
    return _matches(path, SERVICE_LOG_SAMPLE_PATHS)


class _Sampler:
    """One line per (client, path) per window — but every non-2xx, always.

    The heartbeat and timer pings are ~90% of all requests. Dropping them
    outright would make a heartbeat storm (a symptom of the soft-lock going
    wrong) invisible; keeping them all makes the file unreadable. Sampling
    keeps the shape of the traffic while leaving the interesting lines legible,
    and failures are never sampled away because a failing heartbeat is the case
    anyone would be looking for.
    """

    def __init__(self):
        self._seen = {}
        self._lock = threading.Lock()

    def allow(self, key: str, status: int, now: float, level=None) -> bool:
        if status >= 300:
            return True
        if _rank(level) >= _rank("WARN"):
            # A 200 that recorded something notable — a silently dropped timer
            # delta, say — must survive sampling. Otherwise the endpoints most
            # worth thinning are exactly the ones whose rare interesting line
            # gets thinned away.
            return True
        if SERVICE_LOG_SAMPLE_WINDOW <= 0:
            return True
        with self._lock:
            last = self._seen.get(key)
            if last is not None and (now - last) < SERVICE_LOG_SAMPLE_WINDOW:
                return False
            self._seen[key] = now
            # Bounded: one entry per client tab per sampled path. A long-lived
            # process would otherwise accumulate an entry per dead tab forever.
            if len(self._seen) > 4096:
                cutoff = now - SERVICE_LOG_SAMPLE_WINDOW
                self._seen = {
                    k: v for k, v in self._seen.items() if v >= cutoff
                }
            return True


_sampler = _Sampler()


def allow_sampled(key: str, status: int, now: float, level=None) -> bool:
    return _sampler.allow(key, status, now, level=level)


# --------------------------------------------------------------------------
# The writer
# --------------------------------------------------------------------------

class ServiceLogWriter:
    """Append lines to `service/<date>/<method>.log`, rotating by date.

    There is no size-based rotation because the date boundary *is* the
    rotation: a new day opens new files and closes yesterday's, so no file can
    grow without bound the way the wrapper's single `service.log` did. At most
    len(methods)+2 handles are open at once.
    """

    def __init__(self, base_dir: str = SERVICE_LOG_DIR):
        self.base_dir = base_dir
        self._handles = {}
        self._date = None
        self._lock = threading.Lock()
        self._disabled = False

    def _file_for(self, day: str, name: str):
        handle = self._handles.get(name)
        if handle is not None and self._date == day:
            return handle

        if self._date != day:
            self._close_all()
            self._date = day
            # A rollover is the natural moment to sweep: it happens exactly
            # once a day on a running server, and once more at startup.
            self.sweep_old()

        directory = os.path.join(self.base_dir, day)
        os.makedirs(directory, exist_ok=True)
        handle = open(
            os.path.join(directory, f"{name}.log"), "a", encoding="utf-8"
        )
        self._handles[name] = handle
        return handle

    def _close_all(self) -> None:
        for handle in self._handles.values():
            try:
                handle.close()
            except OSError:
                pass
        self._handles = {}

    def write(self, method: str, line: str, is_error: bool = False,
              now: datetime.datetime = None) -> None:
        """Append one line to the method's file, and to errors.log if failing.

        Flushed per line rather than buffered. This deployment loses power with
        the app running (see .devnotes/deployment-hardening/08_BACKUP_TRUNCATION.md);
        the lines immediately before a crash are the ones worth having, and a
        buffer is precisely what would drop them.
        """
        if self._disabled or not SERVICE_LOG_ENABLED:
            return
        try:
            now = now or datetime.datetime.now()
            day = now.strftime("%Y-%m-%d")
            name = method.upper() if method.upper() in SERVICE_LOG_METHODS else "OTHER"
            with self._lock:
                handle = self._file_for(day, name)
                handle.write(line + "\n")
                handle.flush()
                if is_error:
                    errors = self._file_for(day, "errors")
                    errors.write(line + "\n")
                    errors.flush()
        except OSError as exc:
            # A read-only or full log directory must not stop the server from
            # serving. Warn once, then stay quiet rather than logging per
            # request into a file that is itself failing.
            self._disabled = True
            logger.warning(
                "Service logging disabled, could not write under %s: %s",
                self.base_dir, exc,
            )
        except Exception:  # pragma: no cover - never break a request
            logger.warning("Service log write failed", exc_info=True)

    def sweep_old(self, today: datetime.date = None) -> int:
        """Delete dated directories older than LOG_RETENTION_DAYS.

        Returns the number removed. Only directories whose name parses as a
        date are considered, so anything an operator drops in by hand survives.
        """
        if LOG_RETENTION_DAYS <= 0:
            return 0
        removed = 0
        today = today or datetime.date.today()
        cutoff = today - datetime.timedelta(days=LOG_RETENTION_DAYS)
        try:
            entries = os.listdir(self.base_dir)
        except OSError:
            return 0
        for entry in entries:
            path = os.path.join(self.base_dir, entry)
            if not os.path.isdir(path):
                continue
            try:
                day = datetime.datetime.strptime(entry, "%Y-%m-%d").date()
            except ValueError:
                continue  # not ours; leave it alone
            if day >= cutoff:
                continue
            try:
                shutil.rmtree(path)
                removed += 1
            except OSError as exc:
                logger.warning("Could not prune old log directory %s: %s", path, exc)
        if removed:
            logger.info(
                "Pruned %d service log director%s older than %d days.",
                removed, "y" if removed == 1 else "ies", LOG_RETENTION_DAYS,
            )
        return removed


writer = ServiceLogWriter()


# --------------------------------------------------------------------------
# Line assembly
# --------------------------------------------------------------------------

def build_line(
    ctx: RequestContext,
    method: str,
    path: str,
    status: int,
    duration_ms: int,
    ip: str,
    now: datetime.datetime = None,
) -> str:
    now = now or datetime.datetime.now().astimezone()
    level = ctx.level or ("ERROR" if status >= 500 else "WARN" if status >= 400 else "INFO")
    head = (
        f"{now.isoformat(timespec='milliseconds')} {level:<5} "
        f"{method} {path} {status} {duration_ms}ms "
        f"user={sanitize_value(ctx.user)} ip={sanitize_value(ip)} req={ctx.req_id}"
    )
    tail_fields = {}
    if ctx.event:
        tail_fields["event"] = ctx.event
    tail_fields.update(ctx.fields)
    tail = format_fields(tail_fields)
    return f"{head} {tail}".rstrip()

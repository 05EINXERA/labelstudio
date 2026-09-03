"""The service-log middleware: one structured line per request.

Added in `main.py` *first*, which makes it the innermost middleware — closest
to the route — so the duration it records is the handler's, not the handler
plus gzip and header work.

Deliberately thin. It mints the correlation id, times the request, resolves the
caller, and hands one line to `logging_service.writer`. Everything interesting
on the line is contributed by the endpoints through `log_event()`; this file
knows nothing about tasks or annotations.
"""
import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware

import logging_service
from config import SERVICE_LOG_ENABLED

logger = logging.getLogger(__name__)


def _client_ip(request) -> str:
    """The caller's address.

    X-Forwarded-For is honoured because the deployment plan allows a reverse
    proxy in front of the app; with no proxy the header is absent and the
    socket address is used. On a trusted LAN a spoofed header is not a
    meaningful threat, and the value is only ever logged, never authorised on.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    client = request.client
    return client.host if client else "-"


class ServiceLogMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if not SERVICE_LOG_ENABLED or logging_service.should_skip(path):
            return await call_next(request)

        ctx = logging_service.RequestContext(
            logging_service.new_request_id(), time.monotonic()
        )
        token = logging_service.bind_context(ctx)
        # Exposed on the request too, so a handler holding a Request can quote
        # the id in an error response without importing the context machinery.
        request.state.request_id = ctx.req_id

        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-Id"] = ctx.req_id
            return response
        except Exception as exc:
            # Recorded, then re-raised untouched: this middleware observes
            # failures, it does not handle them.
            logging_service.log_event(
                "http.error", level="ERROR", exc=type(exc).__name__
            )
            raise
        finally:
            try:
                self._emit(request, ctx, path, status)
            except Exception:  # pragma: no cover - never break a request
                logger.warning("Service log emit failed", exc_info=True)
            logging_service.reset_context(token)

    def _emit(self, request, ctx, path, status) -> None:
        method = request.method
        duration_ms = int((time.monotonic() - ctx.started) * 1000)

        if logging_service.is_sampled_path(path):
            # Keyed on the client tab where one is identified, so sampling
            # thins each annotator's heartbeats independently instead of one
            # busy tab suppressing everyone else's.
            client = request.query_params.get("client_id") or _client_ip(request)
            key = f"{client}|{path}"
            if not logging_service.allow_sampled(
                key, status, time.monotonic(), level=ctx.level
            ):
                return

        line = logging_service.build_line(
            ctx, method, path, status, duration_ms, _client_ip(request)
        )
        logging_service.writer.write(method, line, is_error=status >= 400)

"""`X-Server-Ms`: the server's own share of each /api/ request.

Temporary, part of the network telemetry (.devnotes/frontend-telemetry/
02_DESIGN.md §2.3). The browser measures a request end to end; this header
lets it subtract the time the server spent, leaving network time.

A custom header rather than `Server-Timing`: browsers expose
`PerformanceResourceTiming.serverTiming` only in secure contexts, and the LAN
deployment is plain HTTP. A header on a same-origin fetch is always readable.

Pure ASGI rather than BaseHTTPMiddleware, for the reason api/compression.py
gives: no task per request, and nothing touches the body. The value is taken
when the response headers are sent, so it covers handler, database,
serialisation and the first gzip chunk, and excludes streaming the rest of the
body — that part is network, and the client times it itself.

The flag is read per request, not at import, so `.env` stays the only switch
and the disabled cost is one attribute read.
"""
import time

import config

_HEADER = b"x-server-ms"


class ServerTimeMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or not config.TELEMETRY_ENABLED
            or not scope.get("path", "").startswith("/api/")
        ):
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()

        async def send_with_timing(message):
            if message["type"] == "http.response.start":
                elapsed_ms = (time.perf_counter() - started) * 1000
                headers = list(message.get("headers", []))
                headers.append((_HEADER, f"{elapsed_ms:.1f}".encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_timing)

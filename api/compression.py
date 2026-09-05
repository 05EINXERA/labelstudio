"""Request-body decompression: the receiving half of gzip on the wire.

`GZipMiddleware` in main.py compresses **responses**. Nothing compressed
requests, and the request direction is the one that hurts: every save uploads
the task's entire annotation set (15.6 MB on the largest task as of 2026-09-05;
the "~1.8 MB" this once said was measured before the data grew), which gzips to about
9 KB — a ~200x reduction on the LAN's scarcer uplink. That single number
accounts for the 25-30 s saves and the truncated-upload 400s diagnosed in
.devnotes/network-lag/01_AUDIT.md (C1).

This middleware inflates `Content-Encoding: gzip` request bodies so every
downstream router sees plain JSON and needs no knowledge that compression
happened.

Written as **pure ASGI** rather than BaseHTTPMiddleware on purpose. Rewriting a
request body means wrapping `receive`, which BaseHTTPMiddleware does not let you
do in a way `await request.json()` reliably observes downstream; it also spawns
a task per request, which this does not.

Accepting an uncompressed body is a permanent behaviour, not a migration step.
`CompressionStream` is feature-detected on the client, and `navigator.sendBeacon`
deliberately never compresses (it cannot await a stream without risking the
unload flush being dropped — see the client for the full reasoning), so
plaintext bodies keep arriving forever and are entirely normal.
"""
import gzip
import logging
import zlib

from config import MAX_DECOMPRESSED_BODY

logger = logging.getLogger(__name__)

# Inflate in bounded steps rather than in one call, so a body that expands past
# the ceiling is abandoned *while* being inflated instead of after a hostile
# ratio has already been materialised in memory.
_CHUNK = 64 * 1024


class BodyTooLarge(Exception):
    """The inflated body passed MAX_DECOMPRESSED_BODY."""


class MalformedBody(Exception):
    """The body did not decompress as gzip."""


def _inflate(payload: bytes, limit: int) -> bytes:
    """Inflate a gzip payload, refusing to exceed `limit` bytes.

    `zlib.decompressobj` with a wbits of 16+MAX_WBITS reads the gzip container.
    `max_length` caps each step, which is what makes the ceiling meaningful: a
    one-shot `gzip.decompress` would allocate the whole expansion before any
    size check could run.
    """
    decompressor = zlib.decompressobj(16 + zlib.MAX_WBITS)
    chunks = []
    total = 0
    try:
        # `max_length` caps each step, and whatever input that cap left unread is
        # held in `unconsumed_tail`. Feeding that tail back is what makes the
        # loop drain the whole stream — passing b"" instead stops after the
        # first chunk and silently truncates the body to _CHUNK bytes, which is
        # a 64 KB annotation set where a 1.8 MB one was sent.
        pending = payload
        while True:
            data = decompressor.decompress(pending, _CHUNK)
            if data:
                total += len(data)
                if total > limit:
                    raise BodyTooLarge()
                chunks.append(data)
            pending = decompressor.unconsumed_tail
            if not pending:
                break
        # Trailing bytes the gzip stream buffered but had not emitted yet.
        tail = decompressor.flush()
        if tail:
            total += len(tail)
            if total > limit:
                raise BodyTooLarge()
            chunks.append(tail)
    except zlib.error as exc:
        raise MalformedBody(str(exc)) from exc
    return b"".join(chunks)


async def _send_error(send, status: int, detail: str) -> None:
    """Answer without reaching the app.

    Hand-built rather than raising an HTTPException: at this depth there is no
    FastAPI exception handler in scope, so a raise would surface as a 500 and
    lose the actionable status the client needs to distinguish "resend this
    uncompressed" from "this payload is too big".
    """
    body = f'{{"detail":"{detail}"}}'.encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
        ],
    })
    await send({"type": "http.response.body", "body": body})


class RequestDecompressionMiddleware:
    """Inflate gzipped request bodies before anything downstream reads them."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = scope.get("headers") or []
        encoding = b""
        for key, value in headers:
            if key.lower() == b"content-encoding":
                encoding = value.strip().lower()
                break

        if encoding != b"gzip":
            await self.app(scope, receive, send)
            return

        # Read the compressed body in full. ASGI delivers it across as many
        # `http.request` messages as the server chooses, and treating the first
        # one as the whole body is the classic way to truncate a large upload —
        # exactly the payloads this exists for. `more_body` is the only
        # authority on where the body ends.
        compressed = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                # The client went away mid-upload. Nothing to answer to.
                return
            compressed.extend(message.get("body", b""))
            if not message.get("more_body", False):
                break

        try:
            body = _inflate(bytes(compressed), MAX_DECOMPRESSED_BODY)
        except BodyTooLarge:
            logger.warning(
                "Rejected gzipped body over %d bytes inflated (path=%s)",
                MAX_DECOMPRESSED_BODY, scope.get("path", "-"),
            )
            await _send_error(send, 413, "Request body too large.")
            return
        except MalformedBody as exc:
            # A 400 and not a 500: the request is at fault, and the client can
            # act on it by retrying uncompressed. Logged because a *recurring*
            # one would mean the client's compression is broken, which is worth
            # noticing rather than absorbing silently.
            logger.warning(
                "Malformed gzip request body (path=%s): %s",
                scope.get("path", "-"), exc,
            )
            await _send_error(send, 400, "Malformed gzip request body.")
            return

        # Present the inflated body as the request. Content-Encoding is dropped
        # (it no longer describes the body) and Content-Length is corrected —
        # leaving the compressed length would make downstream readers stop
        # short, silently truncating the annotation set.
        new_headers = [
            (key, value)
            for key, value in headers
            if key.lower() not in (b"content-encoding", b"content-length")
        ]
        new_headers.append((b"content-length", str(len(body)).encode()))
        scope = dict(scope)
        scope["headers"] = new_headers

        # Replay the inflated body once, then report the stream as ended. A
        # consumer that calls receive() again (Starlette does when a handler
        # re-reads) must not hang, so subsequent calls return an empty
        # terminal message rather than blocking on a stream already drained.
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {
                    "type": "http.request",
                    "body": body,
                    "more_body": False,
                }
            return {"type": "http.request", "body": b"", "more_body": False}

        await self.app(scope, replay, send)

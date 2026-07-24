"""Shared upload-handling helpers for the HTTP layer.

Kept out of `formats/` deliberately: that package is pure, server-free format
logic, while this is about the request boundary.
"""
from fastapi import HTTPException, UploadFile

from config import MAX_IMPORT_BYTES

_READ_CHUNK = 1024 * 1024


async def read_capped(file: UploadFile, max_bytes: int = MAX_IMPORT_BYTES) -> bytes:
    """Read an upload into memory, refusing anything over `max_bytes`.

    `await file.read()` with no argument pulls the whole upload in regardless of
    size, so several concurrent imports could exhaust the memory of the laptop
    serving everyone. Reading in chunks lets the cap be enforced *while*
    reading, so an oversized upload is rejected rather than absorbed first.

    Callers parse the complete bytes (zipfile seeks, json parses whole), so this
    stays an in-memory read — bounded rather than streamed to disk.
    """
    chunks = []
    total = 0
    while True:
        chunk = await file.read(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Upload exceeds the {max_bytes // (1024 * 1024)} MB limit.",
            )
        chunks.append(chunk)
    return b"".join(chunks)

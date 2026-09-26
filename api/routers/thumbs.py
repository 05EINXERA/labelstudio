"""`GET /thumbs/{filename}` — list-view thumbnails of task images.

Beside `/uploads/`, not under `/api/`, and with the same access posture as
`/uploads/`, which is a public static mount: gating a 2 KB derivative behind
auth while the 10 MB original it comes from is public would add friction and
no security. If `/uploads` is ever put behind auth, this moves with it.

A GET that may write a file: CLAUDE.md rule 4 forbids GETs writing to the
*database*. A derived-file cache is the same class of thing as the HTTP cache,
and there is no database access here at all.

The generator lives in api/thumbnails.py, shared with the backfill script.
Design: .devnotes/frontend-telemetry/07_TASKS_PAGE_THUMBNAILS.md.
"""
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from api.thumbnails import ThumbnailBusy, ThumbnailUnavailable, ensure_thumbnail

router = APIRouter(tags=["thumbs"])

# Uploads are never rewritten in place, so neither is a thumbnail: the same
# policy main.py gives /uploads/.
_CACHE = "public, max-age=31536000, immutable"


@router.get("/thumbs/{filename}", include_in_schema=False)
def thumbnail(filename: str):
    try:
        path = ensure_thumbnail(filename)
    except (ValueError, ThumbnailUnavailable):
        # The client shows a neutral placeholder. It never falls back to the
        # original, which would reinstate the cost this exists to remove.
        raise HTTPException(status_code=404, detail="Not Found")
    except ThumbnailBusy:
        raise HTTPException(
            status_code=503, detail="Thumbnail is being generated; retry shortly.",
            headers={"Retry-After": "2"},
        )
    return FileResponse(path, media_type="image/webp", headers={"Cache-Control": _CACHE})

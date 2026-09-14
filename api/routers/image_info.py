"""Images Info — per-project image resolution and file-size inventory.

Answers "which images in this project are what size", which the team currently
reconstructs from memory or a hand-kept spreadsheet because the information is
spread one-image-per-canvas-open across thousands of tasks.

WHY A SEPARATE ROUTER. This is an analytics read with its own permission story
(see below) and its own response vocabulary, and `projects.py` is already the
largest router in the tree. Nothing here mutates, so it carries
`get_current_user` but deliberately not `require_csrf` — there is no state to
protect (CLAUDE.md rule 1a applies to state-changing routers).

TWO DIFFERENT MINIMUM ROLES, deliberately:

  - The **table** is `viewer`. It is strictly less than the Tasks view already
    shows the same user: filenames are already there, and the resolution is
    visible the moment they open the image. Withholding it would protect
    nothing.
  - The **spreadsheet** is `reviewer`, matching the rationale the Exports tab
    already applies (`.devnotes/teams/03_API.md` § 4.1): browsing an inventory
    and one-clicking the whole dataset's inventory into a file that leaves the
    building are different acts. Every annotator having the former is fine;
    every annotator having the latter is not the intent.

This asymmetry will look like a bug to the next reader, which is why it is
written down in both places.

PERFORMANCE. The two rules this module exists to obey
(`.devnotes/image-size-check/01_PLAN.md` § 3.2):

  1. **Never load a `models.Task` entity.** Every query here is a
     `with_entities` projection of scalar columns. Loading the entity risks
     dragging the deferred legacy annotation blob through the driver — measured
     at 103-154 ms *per row* on the big production tasks (`models.py`), which
     is the exact failure mode `.devnotes/performance-fixes/` was written to
     end. A narrow projection makes this endpoint structurally immune to it.
  2. **Never stat the whole project to render a page.** `os.path.getsize` is a
     syscall per row; the table stats only the page it is returning. The
     spreadsheet stats everything because it is a deliberate, occasional,
     whole-set act — not something that fires on every keystroke.
"""
import logging
import os
from datetime import datetime, timezone
from io import BytesIO
from typing import Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, or_, tuple_
from sqlalchemy.orm import Session

import models
from config import DATA_DIR
from database import get_db
from api.auth import get_current_user
from api.permissions import ProjectRole, require_project
from formats.image_sizes import (
    CATEGORY_ORDER,
    OTHER,
    SIZE_CATEGORIES,
    UNKNOWN,
    categorize,
    known_sizes_for,
)
from schemas import ImageInfoPage, ImageInfoRow, ImageInfoSummary

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/projects",
    tags=["image-info"],
    dependencies=[Depends(get_current_user)],
)

DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 500

#: Above this many rows in the filtered set, the page summary reports the bytes
#: it could see rather than stat-ing everything for a total. 2,000 stats is
#: ~100-200 ms of blocking I/O, which is an acceptable ceiling for a number the
#: user explicitly asked for; beyond it the cost stops being worth a figure
#: that is only ever read approximately. `total_bytes_partial` tells the client
#: which of the two it got, so a partial figure is never shown as a total.
TOTAL_BYTES_STAT_LIMIT = 2000

#: Sort keys the client may send, mapped to the column each one orders by.
#: A whitelist rather than getattr(): an arbitrary column name from the query
#: string is how an ORDER BY becomes an information leak.
SORT_COLUMNS = {
    "filename": models.Task.description,
    "width": models.Task.image_width,
    "height": models.Task.image_height,
    "status": models.Task.status,
}
DEFAULT_SORT = "filename"


def _abs_path(image_path: Optional[str]) -> Optional[str]:
    """Absolute path for a task's stored `image_path`, or None.

    `image_path` is stored relative to DATA_DIR as "uploads/<name>" with a
    forward slash regardless of platform (see `projects._save_upload`), so it
    is split on "/" rather than handed to os.path.join whole.
    """
    if not image_path:
        return None
    return os.path.join(DATA_DIR, *image_path.split("/"))


def _file_size(image_path: Optional[str]) -> Optional[int]:
    """Size in bytes of a task's image, or None if it cannot be read.

    None rather than 0: a missing file and an empty file are different facts,
    and reporting a missing 6 MB original as "0 B" would be a quietly wrong
    number in a report whose entire job is to be right about sizes.

    Not logged per row. A project whose uploads directory is unmounted would
    otherwise write one warning per task; the count is returned in the summary
    instead, which is both quieter and more useful.
    """
    path = _abs_path(image_path)
    if not path:
        return None
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def _apply_filters(query, q: Optional[str], category: Optional[str]):
    """Narrow by the filename search box and the category select, in SQL.

    Both run server-side for the same reason the Tasks view's do: the client
    holds one page, so a client-side filter would search 100 rows out of
    several thousand and confidently report "no matches" for an image that
    plainly exists (`.devnotes/tasks-pagination/PLAN.md` § 3.3).
    """
    if q:
        # Case-insensitive substring on the filename. `ilike` rather than
        # lower(...) so Postgres can still use an index, and the wildcards are
        # bound as a parameter rather than concatenated into SQL.
        query = query.filter(models.Task.description.ilike(f"%{q}%"))

    if not category or category == "All":
        return query

    measured = (
        models.Task.image_width.isnot(None)
        & models.Task.image_height.isnot(None)
        # Zero is what formats.common.image_size() writes for a file it could
        # not read, so it is "unmeasured" here exactly as NULL is. Leaving it
        # out would put unreadable images in Other, which reads as "an unusual
        # resolution" rather than "we never got a resolution".
        & (models.Task.image_width != 0)
        & (models.Task.image_height != 0)
    )
    pairs = tuple_(models.Task.image_width, models.Task.image_height)

    if category == UNKNOWN:
        return query.filter(~measured)

    if category == OTHER:
        return query.filter(measured, pairs.notin_(list(SIZE_CATEGORIES.keys())))

    sizes = known_sizes_for(category)
    if sizes is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown category '{category}'. Expected one of: "
                + ", ".join(["All", *CATEGORY_ORDER])
            ),
        )
    return query.filter(measured, pairs.in_(sizes))


def _summary_counts(base_query) -> Tuple[int, Dict[str, int]]:
    """Category totals over the whole filtered set.

    One GROUP BY rather than counting the rows in Python. There are only a
    handful of distinct resolutions in a project, so this returns a few rows
    however many tasks it aggregated — the cost is the scan, not the result,
    and the alternative (fetching every row to count it here) is the mistake
    CLAUDE.md rule 11b names explicitly.
    """
    grouped = (
        base_query.with_entities(
            models.Task.image_width,
            models.Task.image_height,
            func.count(models.Task.id),
        )
        # An ORDER BY inherited from the page query is wasted work on an
        # aggregate, and Postgres rejects ordering by a column that is neither
        # grouped nor aggregated.
        .order_by(None)
        .group_by(models.Task.image_width, models.Task.image_height)
        .all()
    )

    # Every category present with a zero, so the summary strip keeps a stable
    # shape as the user filters instead of reflowing when a bucket empties.
    by_category = {name: 0 for name in CATEGORY_ORDER}
    total = 0
    for width, height, count in grouped:
        by_category[categorize(width, height)] += count
        total += count
    return total, by_category


def _rows_query(project_id: int, db: Session):
    """The narrow projection every read in this module starts from.

    `with_entities` and not `db.query(models.Task)` — see the module docstring.
    """
    return db.query(
        models.Task.id,
        models.Task.description,
        models.Task.image_width,
        models.Task.image_height,
        models.Task.image_path,
        models.Task.status,
    ).filter(models.Task.project_id == project_id)


def _to_rows(records) -> Tuple[List[ImageInfoRow], int, int]:
    """Build response rows, stat-ing each file once.

    Returns (rows, summed_bytes, missing_count) so the caller does not walk the
    list a second time to derive either.
    """
    rows: List[ImageInfoRow] = []
    summed = 0
    missing = 0
    for rec in records:
        size = _file_size(rec.image_path)
        if size is None:
            missing += 1
        else:
            summed += size
        rows.append(ImageInfoRow(
            task_id=rec.id,
            filename=rec.description,
            width=rec.image_width,
            height=rec.image_height,
            category=categorize(rec.image_width, rec.image_height),
            size_bytes=size,
            status=rec.status,
        ))
    return rows, summed, missing


@router.get("/{project_id}/image-info", response_model=ImageInfoPage)
def get_image_info(
    project_id: int,
    q: Optional[str] = Query(None, description="Filename substring, case-insensitive"),
    category: Optional[str] = Query(None, description="All | Full | Half | Other | Unknown"),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    sort: str = Query(DEFAULT_SORT),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """One page of the project's image inventory, plus whole-set category totals.

    `viewer`, because this is strictly less than the Tasks view already shows
    the same caller — see the module docstring for why the spreadsheet below is
    gated higher.

    Read-only, and it must stay that way. A dimension recovered from disk is
    *not* written back here even though `formats.common.image_size()` can:
    CLAUDE.md rule 4 forbids a GET writing, and a table that silently repaired
    rows as you paged would make the Unknown count depend on where you had
    browsed. Repair is `scripts/backfill_image_dimensions.py`, run deliberately.
    """
    require_project(project_id, user, db, minimum=ProjectRole.VIEWER)

    base = _apply_filters(_rows_query(project_id, db), q, category)

    # The category totals come from an aggregate over the filtered set, so they
    # describe everything the filter selected rather than the page in hand.
    total, by_category = _summary_counts(base)

    column = SORT_COLUMNS.get(sort, SORT_COLUMNS[DEFAULT_SORT])
    direction = column.desc() if order == "desc" else column.asc()
    # Filename as a tiebreak on every sort, and id under that. Without a total
    # order, two pages of a project where hundreds of rows share a resolution
    # can repeat and omit rows as the database re-plans between requests.
    ordered = base.order_by(direction, models.Task.description.asc(), models.Task.id.asc())

    records = ordered.offset((page - 1) * page_size).limit(page_size).all()
    rows, page_bytes, missing_on_page = _to_rows(records)

    # Whole-set bytes where that is affordable, page bytes where it is not.
    # The flag is what keeps the difference honest on screen.
    if total <= TOTAL_BYTES_STAT_LIMIT:
        paths = base.with_entities(models.Task.image_path).order_by(None).all()
        total_bytes = 0
        missing = 0
        for (image_path,) in paths:
            size = _file_size(image_path)
            if size is None:
                missing += 1
            else:
                total_bytes += size
        partial = False
    else:
        total_bytes = page_bytes
        missing = missing_on_page
        partial = True

    if missing:
        # Once per request, never once per row: an unmounted uploads directory
        # would otherwise write thousands of lines for a single page view.
        logger.warning(
            "Images Info: %s of %s image file(s) missing on disk for project %s",
            missing, total, project_id,
        )

    return ImageInfoPage(
        items=rows,
        summary=ImageInfoSummary(
            total=total,
            by_category=by_category,
            total_bytes=total_bytes,
            total_bytes_partial=partial,
            missing_files=missing,
        ),
        total=total,
        page=page,
        page_size=page_size,
        # ceil with a floor of 1: an empty project has one empty page, not
        # zero, so the pager always has a page to be on.
        total_pages=max(1, -(-total // page_size)),
    )

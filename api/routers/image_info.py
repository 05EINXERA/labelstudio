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
from sqlalchemy import func, tuple_
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


# ---------------------------------------------------------------------------
# Spreadsheet
# ---------------------------------------------------------------------------

XLSX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
)

#: Hard ceiling on one download. Well above any real project here, and present
#: so a mistyped filter can never try to build a million-row workbook in memory
#: on a box that is also serving 25 annotators.
XLSX_MAX_ROWS = 50000

#: (header, width). Widths are eyeballed for the real data: filenames are the
#: long column, everything else is short.
XLSX_COLUMNS = [
    ("Filename", 34),
    ("Width", 10),
    ("Height", 10),
    ("Resolution", 16),
    ("Category", 12),
    ("Size (MB)", 12),
    ("Status", 14),
    ("Task ID", 10),
]


def _safe_sheet_name(name: str) -> str:
    """A project name reduced to something Excel will accept as a sheet name.

    Excel rejects []:*?/\\ and caps the name at 31 characters. Unhandled, a
    project called "Site A / Roofs" produces a workbook Excel refuses to open —
    a failure the user would reasonably read as "the export is broken".
    """
    cleaned = "".join(" " if ch in "[]:*?/\\" else ch for ch in (name or "")).strip()
    return (cleaned[:31] or "Images")


def _build_workbook(project, rows: List[ImageInfoRow], by_category: Dict[str, int]):
    """The two-sheet workbook, as bytes.

    Imported inside the function rather than at module scope, against the usual
    rule (CLAUDE.md rule 2), for one specific reason: openpyxl is pulled in by
    exactly one endpoint that is used occasionally, and the alternative is
    paying its import on every worker start for a feature most requests never
    touch. The rule's target is `import json` hidden inside a hot function to
    dodge a cycle; this is a deliberate, documented deferral of a heavy
    optional dependency.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Images"

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="374151")

    ws.append([label for label, _ in XLSX_COLUMNS])
    for idx, (_, width) in enumerate(XLSX_COLUMNS, start=1):
        cell = ws.cell(row=1, column=idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center")
        ws.column_dimensions[get_column_letter(idx)].width = width

    for row in rows:
        # Size as a real number, not a formatted string: the recipient's first
        # instinct is to select the column and read the sum, and text cells
        # make that silently produce nothing.
        #
        # float(), and rounded to 4 rather than 2 places. Both matter. A small
        # file rounded to 2 places is 0.0, which openpyxl writes as the integer
        # 0 — so the column becomes a mix of ints and floats, and a genuinely
        # small image reads as "no size at all" rather than "small". The cell's
        # 0.00 display format still shows two places; the extra precision only
        # keeps the stored value honest and the SUM correct.
        megabytes = (
            None if row.size_bytes is None
            else float(round(row.size_bytes / (1024 * 1024), 4))
        )
        resolution = (
            f"{row.width}x{row.height}" if row.width and row.height else ""
        )
        ws.append([
            row.filename or "",
            row.width, row.height, resolution,
            row.category,
            megabytes,
            row.status or "",
            row.task_id,
        ])

    for row_cells in ws.iter_rows(min_row=2, min_col=6, max_col=6):
        for cell in row_cells:
            cell.number_format = "0.00"

    # Freeze the header and turn on the filter dropdowns, so the file is usable
    # as a working document rather than only as a printout.
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(XLSX_COLUMNS))}{ws.max_row}"

    # --- Summary sheet ---
    # Small, and it is the half that gets pasted into an email.
    summary = wb.create_sheet("Summary")
    summary.append(["Project", project.name or ""])
    summary.append(["Generated (UTC)", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")])
    summary.append(["Total images", len(rows)])
    summary.append([])
    summary.append(["Category", "Count", "Size (MB)"])
    for cell in summary[5]:
        cell.font = header_font
        cell.fill = header_fill

    bytes_by_category: Dict[str, int] = {}
    for row in rows:
        if row.size_bytes is not None:
            bytes_by_category[row.category] = (
                bytes_by_category.get(row.category, 0) + row.size_bytes
            )

    # float() for the same reason as the Images sheet: a category whose files
    # round to 0.0 must not become an integer cell in a numeric column.
    def _mb(total_bytes: int) -> float:
        return float(round(total_bytes / (1024 * 1024), 4))

    for name in CATEGORY_ORDER:
        summary.append([
            name,
            by_category.get(name, 0),
            _mb(bytes_by_category.get(name, 0)),
        ])
    summary.append([
        "Total",
        sum(by_category.values()),
        _mb(sum(bytes_by_category.values())),
    ])
    for label, width in (("A", 22), ("B", 12), ("C", 14)):
        summary.column_dimensions[label].width = width
    for row_cells in summary.iter_rows(min_row=6, min_col=3, max_col=3):
        for cell in row_cells:
            cell.number_format = "0.00"

    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


@router.get("/{project_id}/image-info.xlsx")
def download_image_info(
    project_id: int,
    q: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    sort: str = Query(DEFAULT_SORT),
    order: str = Query("asc", pattern="^(asc|desc)$"),
    db: Session = Depends(get_db),
    user: models.User = Depends(get_current_user),
):
    """The filtered inventory as a formatted .xlsx workbook.

    `reviewer`, not `viewer` like the table above it. Browsing an inventory and
    one-clicking the whole dataset's inventory into a file that leaves the
    building are different acts — the same reasoning the Exports tab already
    applies (`.devnotes/teams/03_API.md` § 4.1). The asymmetry is deliberate.

    **The same filters as the table**, honoured deliberately: a download button
    under a filtered table means "give me this", and one that silently returned
    everything would be discovered only after someone had acted on it.

    Synchronous rather than routed through the `JOBS` queue that `exports.py`
    uses. The payload is a few hundred KB of text even for thousands of rows
    and builds in well under a second — there is no rasterisation here — and
    `JOBS` is single-worker in-process state (rule 9) that should not grow
    without cause.
    """
    project = require_project(project_id, user, db, minimum=ProjectRole.REVIEWER)

    base = _apply_filters(_rows_query(project_id, db), q, category)
    total, by_category = _summary_counts(base)

    if total > XLSX_MAX_ROWS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"{total} images match; the spreadsheet limit is {XLSX_MAX_ROWS}. "
                "Narrow the filter and download in parts."
            ),
        )

    column = SORT_COLUMNS.get(sort, SORT_COLUMNS[DEFAULT_SORT])
    direction = column.desc() if order == "desc" else column.asc()
    records = base.order_by(
        direction, models.Task.description.asc(), models.Task.id.asc()
    ).all()

    rows, _, missing = _to_rows(records)
    if missing:
        logger.warning(
            "Images Info export: %s of %s image file(s) missing on disk for project %s",
            missing, total, project_id,
        )

    content = _build_workbook(project, rows, by_category)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    slug = (project.slug or project.name or f"project-{project_id}")
    # Filename goes in a quoted header, so anything that could terminate the
    # quoting or inject a header is replaced rather than escaped.
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in slug)[:40]
    filename = f"image-info-{slug or project_id}-{stamp}.xlsx"

    return Response(
        content=content,
        media_type=XLSX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

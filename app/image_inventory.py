"""Query logic for the Image Inventory report.

Kept out of the router so the filter/sort/summary rules are testable without a
server, following the same instinct as `formats/` (pure, testable modules).

Two performance rules govern everything here, and both are structural rather
than incidental:

  1. Never load full Task entities. `tasks.annotations_legacy` is a Text column
     holding up to 3.45 MB per row (avg 561 KB across 55 non-null rows in
     production, 30.9 MB total). Loading entities would drag that through
     psycopg for every row of every page. Every query below uses an explicit
     `with_entities` projection of just the scalar columns the report shows, so
     the endpoint is structurally immune rather than accidentally fast.

  2. Never stat the whole project to render one page. A file size costs one
     filesystem call per row, so only the returned rows are checked -- except
     for the bounded whole-set total described in `page_size_total`.
"""
import logging
import os
from typing import Dict, List, Optional, Tuple

from sqlalchemy import and_, func, not_, tuple_
from sqlalchemy.orm import Session

import models
from config import DATA_DIR
from schemas import (
    categorize_image_size,
    IMAGE_SIZE_CATEGORIES,
    IMAGE_SIZE_NAMED,
    IMAGE_SIZE_OTHER,
    IMAGE_SIZE_UNKNOWN,
)

logger = logging.getLogger(__name__)


# Sort keys the client may ask for, mapped explicitly to columns.
#
# An explicit dictionary rather than `getattr(models.Task, sort_by)`: an
# attribute lookup on a query-string value lets a caller order by any column on
# the model, including ones the report does not expose. The task list endpoint
# next door still does the getattr version; this is the pattern new code
# follows (CLAUDE.md: where existing code disagrees with a rule, the rule wins).
SORT_COLUMNS = {
    "filename": models.Task.description,
    "width": models.Task.image_width,
    "height": models.Task.image_height,
    "status": models.Task.status,
    "id": models.Task.id,
}
DEFAULT_SORT = "filename"


def _has_dimensions():
    """SQL predicate: this row carries usable pixel dimensions.

    Zero is treated as unmeasured, not as a real dimension --
    `formats.common.image_size()` returns (0, 0) for an unreadable file, so a
    zero row is in the same state as a NULL one. A half-measured row (width
    but no height) fails this too, because we cannot name a resolution we only
    half know.
    """
    return and_(
        models.Task.image_width.isnot(None),
        models.Task.image_height.isnot(None),
        models.Task.image_width > 0,
        models.Task.image_height > 0,
    )


def category_filter(category: str):
    """SQL predicate selecting exactly the rows in `category`.

    The category filter is expressed in SQL, never in application code: with
    one page of 50 out of several thousand rows, filtering in Python (or in the
    browser) searches those 50 and confidently reports "no matches" for an
    image that plainly exists on page 30.

    The `Other` arm is the one that goes subtly wrong. Written as the naive
    "not in the known sizes" it sweeps up every unmeasured row, because in SQL
    `NULL NOT IN (...)` is NULL -- not true -- and the row is dropped from the
    Other bucket by a *different* mechanism than the one the author intended,
    then reappears the moment the comparison is restructured. Pairing the
    negation with an explicit has-dimensions condition makes the intent
    independent of null comparison semantics. See the dedicated test.
    """
    known = list(IMAGE_SIZE_NAMED.keys())
    pair = tuple_(models.Task.image_width, models.Task.image_height)

    if category == IMAGE_SIZE_UNKNOWN:
        return not_(_has_dimensions())

    if category == IMAGE_SIZE_OTHER:
        # Measured AND not one of the named pairs -- both halves required.
        return and_(_has_dimensions(), not_(pair.in_(known)))

    sizes = [size for size, name in IMAGE_SIZE_NAMED.items() if name == category]
    if not sizes:
        # Caller should have validated against IMAGE_SIZE_CATEGORIES first.
        raise ValueError(f"Unknown image size category: {category!r}")
    return and_(_has_dimensions(), pair.in_(sizes))


def base_query(db: Session, project_id: int, search: Optional[str] = None,
               category: Optional[str] = None):
    """Rows of one project, narrowed by the search and category filters.

    Scoped to `project_id` on every path, so rows from another project cannot
    appear regardless of what else is asked for.

    Starts from `Task.id` rather than `Task` so the query is narrow even before
    a caller applies its own projection. Every consumer below re-projects with
    `with_entities`, so this makes no difference today -- but a `db.query(
    models.Task)` here would be a loaded gun: the next person to add a caller
    and execute this directly would silently pull annotations_legacy (up to
    3.45 MB a row) for every row in the filtered set, which is precisely the
    regression rule 1 exists to prevent.
    """
    query = db.query(models.Task.id).filter(models.Task.project_id == project_id)

    if search:
        # Filename substring. Matched in the database, not over the page.
        query = query.filter(models.Task.description.ilike(f"%{search}%"))

    if category and category.lower() != "all":
        query = query.filter(category_filter(category))

    return query


def ordered(query, sort_by: Optional[str], sort_desc: bool):
    """Apply a whitelisted sort with a total ordering.

    Falls back to the default for an unrecognised key rather than erroring: a
    stale bookmark carrying an old sort key should render the page, not a 422.

    The trailing (description, id) tiebreak is what makes paging stable. Sorted
    by width alone in a project where 395 rows share one resolution, the
    database is free to return ties in any order it likes, so consecutive page
    requests can repeat some rows and omit others -- a bug that looks like data
    corruption and is maddening to trace back to a missing ORDER BY term.
    """
    column = SORT_COLUMNS.get(sort_by or "", SORT_COLUMNS[DEFAULT_SORT])
    primary = column.desc() if sort_desc else column.asc()
    # Tiebreak on (description, id), skipping description when it is already
    # the primary key so the clause does not repeat it.
    tiebreaks = [models.Task.id.asc()]
    if column is not models.Task.description:
        tiebreaks.insert(0, models.Task.description.asc())
    return query.order_by(primary, *tiebreaks)


def page_rows(query, offset: int, limit: int) -> List:
    """One page, projected to just the columns the report renders.

    `with_entities` is the narrow projection required by rule 1 above: without
    it this drags annotations_legacy through the driver for every row.
    """
    return (
        query.with_entities(
            models.Task.id,
            models.Task.description,
            models.Task.image_width,
            models.Task.image_height,
            models.Task.status,
        )
        .offset(offset)
        .limit(limit)
        .all()
    )


def image_file_path(image_path: Optional[str]) -> Optional[str]:
    """Absolute path for a stored image reference.

    Mirrors `formats.common.image_size()`: image_path is stored relative to
    DATA_DIR as "uploads/<name>" with a forward slash regardless of platform
    (see projects._save_upload), so it is split on "/" and rejoined with the
    OS separator rather than passed through as-is.
    """
    if not image_path:
        return None
    return os.path.join(DATA_DIR, *image_path.split("/"))


def file_size(image_path: Optional[str]) -> Optional[int]:
    """Size in bytes, or None if the file cannot be read.

    None, never 0. A missing original reported as "0 B" is a plausible-looking
    wrong number in a report about sizes; a dash is the honest rendering.

    Catches OSError specifically (missing file, permission denied, unmounted
    volume) rather than bare-excepting. The caller counts these and logs once
    per request -- see `collect_sizes`.
    """
    path = image_file_path(image_path)
    if not path:
        return None
    try:
        return os.path.getsize(path)
    except OSError:
        return None


def collect_sizes(db: Session, query, task_ids: Optional[List[int]] = None
                  ) -> Tuple[Dict[int, Optional[int]], int, int]:
    """Sizes for a set of rows, plus counts of missing files and total bytes.

    Returns (sizes_by_task_id, missing_count, total_bytes).

    Missing files are logged ONCE per call, not once per row. If the storage
    volume is unmounted every row misses, and per-row logging would write
    thousands of lines for a single page view. The count is the useful signal
    and the summary already surfaces it to the reader.
    """
    rows = query.with_entities(models.Task.id, models.Task.image_path)
    if task_ids is not None:
        rows = rows.filter(models.Task.id.in_(task_ids))

    sizes: Dict[int, Optional[int]] = {}
    missing = 0
    total = 0
    for task_id, image_path in rows.all():
        size = file_size(image_path)
        sizes[task_id] = size
        if size is None:
            missing += 1
        else:
            total += size

    if missing:
        logger.warning(
            "Image inventory: %d image file(s) referenced by tasks are missing "
            "from disk under %s", missing, DATA_DIR,
        )
    return sizes, missing, total


def summarize_categories(query) -> Tuple[List[dict], int]:
    """Per-category counts over the whole filtered set, plus the row total.

    Computed as a single grouped aggregate, not by walking rows: there are only
    a handful of distinct resolutions in any project (18 across the entire
    production database), so this returns a few rows no matter how many it
    aggregated. The few rows are then bucketed into categories in Python, which
    keeps the vocabulary in one place instead of re-encoding it as SQL CASE
    arms.

    Every category appears, including those with a zero count.
    """
    counts = {name: 0 for name in IMAGE_SIZE_CATEGORIES}
    total = 0
    grouped = (
        query.with_entities(
            models.Task.image_width,
            models.Task.image_height,
            func.count(models.Task.id),
        )
        .group_by(models.Task.image_width, models.Task.image_height)
        .all()
    )
    for width, height, count in grouped:
        counts[categorize_image_size(width, height)] += count
        total += count

    ordered_counts = [
        {"category": name, "count": counts[name]} for name in IMAGE_SIZE_CATEGORIES
    ]
    return ordered_counts, total

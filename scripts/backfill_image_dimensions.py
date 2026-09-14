"""
Measure and store the pixel dimensions of tasks that have none.

`tasks.image_width` / `image_height` are written at upload time
(`api/routers/projects.py`), but rows created before migration b2c3d4e5f6a7, or
through an import path that never measured the file, carry NULL. Those tasks
show as **Unknown** in the Images Info view and are invisible to a Full/Half
filter — so an inventory whose whole job is to be trusted about sizes quietly
under-reports.

This repairs them. It exists as a script rather than as lazy repair inside the
endpoint for two reasons: CLAUDE.md rule 4 forbids a GET writing to the
database, and a table that silently fixed rows as you paged would make the
Unknown count depend on where you had browsed.

House style, matching backfill_annotations.py:

  * dry-run by default; --commit is required to write anything,
  * batched commits, so an interrupt leaves whole batches behind rather than
    losing the whole run,
  * idempotent: a task that already has both dimensions is skipped unless
    --force, so it is safe to re-run after a bulk import,
  * never fails the run on one unreadable file — it counts them and reports
    them at the end, because a missing image is information, not an error.

Reads only the image header (Pillow's `.size`), never the pixels, so a project
of 5184x3888 originals costs kilobytes of I/O per file rather than megabytes.

Usage:

    python scripts/backfill_image_dimensions.py                  # dry run
    python scripts/backfill_image_dimensions.py --commit
    python scripts/backfill_image_dimensions.py --project 12 --commit
    python scripts/backfill_image_dimensions.py --force --commit  # re-measure all
"""

import argparse
import logging
import os
import sys

# Repo root on the path, so this runs as
# `python scripts/backfill_image_dimensions.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import models  # noqa: E402
from config import DATA_DIR  # noqa: E402
from database import SessionLocal, commit_with_retry  # noqa: E402
from formats.common import measure_image  # noqa: E402
from formats.image_sizes import categorize  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("backfill-dimensions")

#: Tasks per commit. Small enough that an interrupt loses little, large enough
#: that a 20,000-task project is not 20,000 transactions.
BATCH_SIZE = 200


def _needs_measuring(task) -> bool:
    """True when this task has no usable dimensions.

    Zero counts as missing, not as measured: `formats.common.image_size()`
    writes (0, 0) for a file it could not read, and a row carrying that is in
    exactly the same state as one carrying NULL.
    """
    return not task.image_width or not task.image_height


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", action="store_true",
                        help="Actually write. Without it, nothing is changed.")
    parser.add_argument("--project", type=int, default=None,
                        help="Limit to one project id.")
    parser.add_argument("--force", action="store_true",
                        help="Re-measure tasks that already have dimensions.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Narrow projection, not the Task entity: loading entities here would
        # drag the deferred legacy annotation blob through the driver for every
        # row (models.py measured 103-154 ms per row on the big production
        # tasks). The write below re-fetches only the rows that need changing.
        query = db.query(
            models.Task.id, models.Task.image_path,
            models.Task.image_width, models.Task.image_height,
        )
        if args.project is not None:
            query = query.filter(models.Task.project_id == args.project)

        candidates = [
            rec for rec in query.all()
            if args.force or not rec.image_width or not rec.image_height
        ]

        logger.info("%s task(s) to measure%s.", len(candidates),
                    "" if args.project is None else f" in project {args.project}")
        if not candidates:
            return 0

        measured = 0
        unreadable = 0
        no_path = 0
        by_category: dict = {}
        pending = 0

        for rec in candidates:
            if not rec.image_path:
                no_path += 1
                continue

            # image_path is stored relative to DATA_DIR with a forward slash
            # regardless of platform (projects._save_upload).
            path = os.path.join(DATA_DIR, *rec.image_path.split("/"))
            width, height = measure_image(path)

            if not width or not height:
                unreadable += 1
                logger.warning("  task %s: could not read %s", rec.id, path)
                continue

            measured += 1
            category = categorize(width, height)
            by_category[category] = by_category.get(category, 0) + 1

            if not args.commit:
                continue

            db.query(models.Task).filter(models.Task.id == rec.id).update(
                {"image_width": width, "image_height": height},
                # The rows were never loaded as entities, and nothing else in
                # this process holds them, so there is no identity map to keep
                # in step.
                synchronize_session=False,
            )
            pending += 1
            if pending >= BATCH_SIZE:
                commit_with_retry(db)
                pending = 0
                logger.info("  committed %s/%s…", measured, len(candidates))

        if args.commit and pending:
            commit_with_retry(db)

        logger.info("")
        logger.info("Measured:   %s", measured)
        for name, count in sorted(by_category.items()):
            logger.info("  %-8s %s", name, count)
        if no_path:
            logger.info("No image:   %s (task has no image_path)", no_path)
        if unreadable:
            logger.info("Unreadable: %s (file missing or not an image)", unreadable)
        if not args.commit:
            logger.info("")
            logger.info("DRY RUN — nothing was written. Re-run with --commit.")

        # Unreadable files are reported, not fatal: a missing image is a fact
        # about the deployment worth surfacing, and failing the run would stop
        # the thousands of rows that measured fine from being written.
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

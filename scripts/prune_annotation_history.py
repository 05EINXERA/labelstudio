"""Trim task_annotation_history to the retention limit, retroactively.

The limit (`ANNOTATION_HISTORY_KEEP`) is enforced when a task is *saved*, so
lowering it leaves every task that nobody has touched since sitting at the old
count. On the production box that meant 66 tasks holding 50 rows each after the
limit dropped to 20, with the table at 1.2 GB — 87% of the database.

    python scripts/prune_annotation_history.py                # dry run
    python scripts/prune_annotation_history.py --apply
    python scripts/prune_annotation_history.py --apply --keep 20 --vacuum

Always prints what it would delete and waits for --apply. This is the one
sanctioned DELETE on the table (it drops rows that have aged out, never
rewrites what happened), but it is still deleting the only remaining copy of
superseded annotation work — so the dry run is the default and the summary is
worth reading before confirming.

Newest rows are kept, ordered by id: history is append-only and the id is
monotonic, so "newest N by id" and "newest N by created_at" agree, and id
avoids depending on clock behaviour.

The space itself does not come back until the table is vacuumed, because the
blobs live in TOAST. --vacuum runs VACUUM FULL, which takes an ACCESS EXCLUSIVE
lock: saves block for its duration, so run it off-hours (or use pg_repack, which
does not). Without --vacuum the pages are merely marked reusable, which still
stops the table growing but does not shrink the database on disk.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import text  # noqa: E402

import config  # noqa: E402
from database import SessionLocal, engine  # noqa: E402


def _fmt_bytes(value):
    value = float(value or 0)
    for unit in ("B", "kB", "MB", "GB"):
        if abs(value) < 1024.0:
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"


def _survey(db, keep):
    """Per-task counts and the byte cost of what is over the limit.

    pg_column_size gives the stored (compressed, pre-TOAST-overhead) size, so
    the reported figure is an estimate of reclaimable bytes rather than an
    exact promise about the file on disk.
    """
    rows = db.execute(text("""
        WITH ranked AS (
            SELECT id, task_id,
                   pg_column_size(annotations) AS bytes,
                   ROW_NUMBER() OVER (PARTITION BY task_id ORDER BY id DESC) AS rn
            FROM task_annotation_history
        )
        SELECT task_id,
               count(*)                                  AS total,
               count(*) FILTER (WHERE rn > :keep)         AS over,
               COALESCE(sum(bytes) FILTER (WHERE rn > :keep), 0) AS over_bytes
        FROM ranked
        GROUP BY task_id
        HAVING count(*) FILTER (WHERE rn > :keep) > 0
        ORDER BY count(*) FILTER (WHERE rn > :keep) DESC
    """), {"keep": keep}).fetchall()
    return rows


def _table_size(db):
    return db.execute(text(
        "SELECT pg_total_relation_size('task_annotation_history')"
    )).scalar() or 0


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", type=int, default=config.ANNOTATION_HISTORY_KEEP,
                        help="rows to keep per task (default: ANNOTATION_HISTORY_KEEP)")
    parser.add_argument("--apply", action="store_true",
                        help="actually delete; without this nothing is written")
    parser.add_argument("--vacuum", action="store_true",
                        help="VACUUM FULL afterwards to return the space to the OS "
                             "(takes an exclusive lock — run off-hours)")
    parser.add_argument("--task-id", type=int, default=None,
                        help="limit to one task, for testing")
    args = parser.parse_args()

    if args.keep < 1:
        parser.error("--keep must be at least 1; history is the recovery net.")

    if config.IS_SQLITE:
        print("This script targets the Postgres deployment "
              "(pg_column_size / VACUUM FULL are Postgres-specific).")
        return 1

    db = SessionLocal()
    try:
        size_before = _table_size(db)
        print(f"Database : {config.DATABASE_URL.rsplit('@', 1)[-1]}")
        print(f"Table    : {_fmt_bytes(size_before)}")
        print(f"Keep     : {args.keep} rows per task\n")

        rows = _survey(db, args.keep)
        if args.task_id is not None:
            rows = [r for r in rows if r.task_id == args.task_id]

        if not rows:
            print("Nothing over the limit. No rows to delete.")
            return 0

        total_over = sum(r.over for r in rows)
        total_bytes = sum(r.over_bytes for r in rows)

        print(f"{'task':>8}  {'rows':>6}  {'delete':>7}  {'reclaim':>10}")
        for r in rows[:20]:
            print(f"{r.task_id:>8}  {r.total:>6}  {r.over:>7}  {_fmt_bytes(r.over_bytes):>10}")
        if len(rows) > 20:
            print(f"  ... and {len(rows) - 20} more tasks")

        print(f"\n{len(rows)} task(s), {total_over} row(s), ~{_fmt_bytes(total_bytes)} of blob data.")

        if not args.apply:
            print("\nDry run. Re-run with --apply to delete.")
            return 0

        params = {"keep": args.keep}
        task_filter = ""
        if args.task_id is not None:
            task_filter = "WHERE task_id = :task_id"
            params["task_id"] = args.task_id

        deleted = db.execute(text(f"""
            DELETE FROM task_annotation_history
            WHERE id IN (
                SELECT id FROM (
                    SELECT id, ROW_NUMBER() OVER (
                        PARTITION BY task_id ORDER BY id DESC
                    ) AS rn
                    FROM task_annotation_history
                    {task_filter}
                ) ranked
                WHERE rn > :keep
            )
        """), params).rowcount
        db.commit()
        print(f"\nDeleted {deleted} row(s).")

        if args.vacuum:
            # VACUUM cannot run inside a transaction block.
            db.close()
            print("Running VACUUM FULL (holds an exclusive lock)...")
            # VACUUM must not run inside a transaction, so the connection has
            # to be in autocommit. psycopg3 exposes that as a property on the
            # driver connection (`set_isolation_level(0)` is psycopg2-era and
            # raises ValueError here), and SQLAlchemy hands back a pooled proxy
            # whose `.driver_connection` is the real one.
            raw = engine.raw_connection()
            try:
                driver = getattr(raw, "driver_connection", raw)
                previous = driver.autocommit
                driver.autocommit = True
                try:
                    cur = raw.cursor()
                    try:
                        cur.execute("VACUUM FULL task_annotation_history")
                    finally:
                        cur.close()
                finally:
                    driver.autocommit = previous
            finally:
                raw.close()

            db2 = SessionLocal()
            try:
                size_after = _table_size(db2)
                print(f"Table: {_fmt_bytes(size_before)} -> {_fmt_bytes(size_after)} "
                      f"({_fmt_bytes(size_before - size_after)} returned)")
            finally:
                db2.close()
        else:
            print("Space is reusable but not returned to the OS. "
                  "Re-run with --vacuum (off-hours) to shrink the file.")
        return 0
    finally:
        try:
            db.close()
        except Exception:  # noqa: BLE001 - already closed by the vacuum path
            pass


if __name__ == "__main__":
    sys.exit(main())

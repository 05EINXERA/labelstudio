"""
Copy every task's annotation blob into the normalised `annotations` table.

Part of the annotation normalisation (Fix 2). The blob in `tasks.annotations`
stays untouched and authoritative: this only *populates* the new table, so it
is safe to run against a live server during Phases A and B. Nothing reads the
table until the Phase C cutover.

Deliberately conservative, in the house style of restore_task_annotations.py:

  * dry-run by default; --commit is required to write anything,
  * one transaction per task, so an interrupt leaves whole tasks, never halves,
  * idempotent: a task that already has rows is skipped unless --force,
  * verifies every task by converting its rows straight back and comparing them
    to the source blob; a task that does not round-trip is rolled back and
    reported rather than half-migrated,
  * writes a manifest recording per-task counts, which is the artifact that
    proves the migration lost nothing.

Usage:

    python scripts/backfill_annotations.py                  # dry run, all tasks
    python scripts/backfill_annotations.py --commit
    python scripts/backfill_annotations.py --task 713 --commit
    python scripts/backfill_annotations.py --reverse --commit   # Phase C rollback

--reverse re-serialises the table back into the blob column. It exists because
after the Phase C cutover the table is the only copy of new work, so a rollback
needs a way home. Write it before you need it, not at 2am.
"""

import argparse
import datetime
import json
import logging
import os
import sys

# Repo root on the path, so this runs as `python scripts/backfill_annotations.py`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import models  # noqa: E402
from database import SessionLocal, commit_with_retry  # noqa: E402
from formats.annotation_rows import dict_to_row_kwargs, rows_to_dicts  # noqa: E402

logger = logging.getLogger("backfill_annotations")


def _parse_blob(raw):
    """The blob as a list of dicts, or (None, reason) if it cannot be used."""
    if not raw or not raw.strip():
        return [], None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError) as exc:
        return None, f"unparseable: {exc}"
    if not isinstance(parsed, list):
        return None, f"not-a-list: {type(parsed).__name__}"
    return parsed, None


def _comparable(ann: dict) -> dict:
    """One annotation reduced to what storage can faithfully represent.

    Storage coerces coordinate strings to floats and `order` to an int, so a
    verbatim comparison would report those as losses when they are deliberate
    normalisations. Everything else must match exactly.
    """
    out = dict(ann)
    for key in ("x", "y", "width", "height"):
        if key in out:
            try:
                out[key] = float(out[key])
            except (TypeError, ValueError):
                out.pop(key)
    if "order" in out:
        try:
            out["order"] = int(out["order"])
        except (TypeError, ValueError):
            out.pop("order")
    return out


def _verify(source: list, rows) -> str:
    """None if the rows reproduce `source`, else a description of the drift."""
    got = rows_to_dicts(rows)
    if len(got) != len(source):
        return f"count {len(source)} -> {len(got)}"

    # Compare by id: the relationship's order_by is (order, id), which is not
    # the blob's array order, and reordering is not a loss.
    by_id = {d["id"]: d for d in got}
    for original in source:
        ident = original.get("id")
        if ident is None:
            # Minted during conversion; it cannot be looked up, so only the
            # count above can be checked for these. Four such rows exist.
            continue
        if ident not in by_id:
            return f"annotation {ident} missing after conversion"
        expected = _comparable(original)
        actual = by_id[ident]
        if expected != actual:
            differing = sorted(
                set(expected) | set(actual),
                key=str,
            )
            diff = [k for k in differing if expected.get(k) != actual.get(k)]
            return f"annotation {ident} differs on {diff}"
    return None


def backfill(db, task_ids=None, commit=False, force=False, limit=None):
    # Every label that actually exists. The blob had no foreign key, so a
    # `labelId` could outlive its label: 656 real annotations across 9 tasks
    # name labels that are gone. The new FK rejects those, so they are stored
    # with a NULL label_id and the original preserved in extra -- see
    # formats.annotation_rows.dict_to_row_kwargs.
    known_label_ids = {row[0] for row in db.query(models.Label.id).all()}
    logger.info("Loaded %d known label ids", len(known_label_ids))

    query = db.query(models.Task.id, models.Task.annotations).order_by(models.Task.id)
    if task_ids:
        query = query.filter(models.Task.id.in_(task_ids))
    if limit:
        query = query.limit(limit)

    stats = {"tasks": 0, "skipped": 0, "converted": 0, "annotations": 0,
             "minted": 0, "failed": 0, "empty": 0, "orphaned_labels": 0}
    manifest = []
    failures = []

    for task_id, raw in query.all():
        stats["tasks"] += 1

        existing = (
            db.query(models.Annotation)
            .filter(models.Annotation.task_id == task_id)
            .count()
        )
        if existing and not force:
            stats["skipped"] += 1
            continue

        source, reason = _parse_blob(raw)
        if source is None:
            stats["failed"] += 1
            failures.append((task_id, reason))
            logger.warning("Task %s: %s -- left on the legacy blob", task_id, reason)
            continue

        usable = [a for a in source if isinstance(a, dict)]
        if len(usable) != len(source):
            dropped = len(source) - len(usable)
            stats["failed"] += 1
            failures.append((task_id, f"{dropped} non-dict entries"))
            logger.warning("Task %s: %d non-dict entries -- left on the legacy blob",
                           task_id, dropped)
            continue

        if not usable:
            stats["empty"] += 1
            manifest.append({"task_id": task_id, "before": 0, "after": 0})
            continue

        try:
            if existing:
                db.query(models.Annotation).filter(
                    models.Annotation.task_id == task_id
                ).delete(synchronize_session=False)
                db.flush()

            minted = 0
            orphaned = 0
            for ann in usable:
                if not ann.get("id"):
                    minted += 1
                label_id = ann.get("labelId")
                if label_id is not None and label_id not in known_label_ids:
                    orphaned += 1
                db.add(models.Annotation(
                    **dict_to_row_kwargs(ann, task_id, known_label_ids)
                ))
            db.flush()

            rows = (
                db.query(models.Annotation)
                .filter(models.Annotation.task_id == task_id)
                .all()
            )
            problem = _verify(usable, rows)
            if problem:
                db.rollback()
                stats["failed"] += 1
                failures.append((task_id, problem))
                logger.error("Task %s did NOT round-trip (%s) -- rolled back, "
                             "left on the legacy blob", task_id, problem)
                continue

            if commit:
                commit_with_retry(db)
            else:
                db.rollback()

            stats["converted"] += 1
            stats["annotations"] += len(usable)
            stats["minted"] += minted
            stats["orphaned_labels"] += orphaned
            if orphaned:
                logger.info("Task %s: %d annotation(s) name a label that no "
                            "longer exists; stored with a null label_id and "
                            "the original kept in extra", task_id, orphaned)
            manifest.append({"task_id": task_id, "before": len(usable),
                             "after": len(rows), "minted": minted,
                             "orphaned_labels": orphaned})

        except Exception as exc:  # noqa: BLE001 - reported per task, never fatal
            db.rollback()
            stats["failed"] += 1
            failures.append((task_id, f"{type(exc).__name__}: {exc}"))
            logger.exception("Task %s failed to convert", task_id)

    return stats, manifest, failures


def reverse(db, task_ids=None, commit=False):
    """Re-serialise the table back into `tasks.annotations`.

    The Phase C rollback path: once the cutover has happened, work written
    after it exists only as rows, so going back means writing it into the blob
    column the old code reads.
    """
    query = db.query(models.Task).order_by(models.Task.id)
    if task_ids:
        query = query.filter(models.Task.id.in_(task_ids))

    stats = {"tasks": 0, "written": 0, "annotations": 0}
    for task in query.all():
        stats["tasks"] += 1
        rows = (
            db.query(models.Annotation)
            .filter(models.Annotation.task_id == task.id)
            .order_by(models.Annotation.order, models.Annotation.id)
            .all()
        )
        if not rows:
            continue
        task.annotations = json.dumps(rows_to_dicts(rows))
        stats["written"] += 1
        stats["annotations"] += len(rows)

    if commit:
        commit_with_retry(db)
    else:
        db.rollback()
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", action="store_true",
                        help="actually write. Without it, nothing is persisted.")
    parser.add_argument("--task", type=int, action="append", dest="tasks",
                        help="limit to this task id; repeatable")
    parser.add_argument("--force", action="store_true",
                        help="re-convert tasks that already have rows")
    parser.add_argument("--limit", type=int, help="stop after N tasks (for a trial run)")
    parser.add_argument("--reverse", action="store_true",
                        help="re-serialise rows back into the blob column")
    parser.add_argument("--manifest", help="write the per-task manifest here as JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    db = SessionLocal()
    try:
        if args.reverse:
            stats = reverse(db, args.tasks, args.commit)
            print(f"\ntasks scanned : {stats['tasks']}")
            print(f"blobs written : {stats['written']}")
            print(f"annotations   : {stats['annotations']}")
            failures = []
        else:
            stats, manifest, failures = backfill(
                db, args.tasks, args.commit, args.force, args.limit
            )
            print(f"\ntasks scanned    : {stats['tasks']}")
            print(f"  converted      : {stats['converted']}")
            print(f"  already done   : {stats['skipped']}")
            print(f"  empty          : {stats['empty']}")
            print(f"  FAILED         : {stats['failed']}")
            print(f"annotations      : {stats['annotations']}")
            print(f"  ids minted     : {stats['minted']}")
            print(f"  orphaned labels: {stats['orphaned_labels']}")

            if args.manifest:
                with open(args.manifest, "w", encoding="utf-8") as fh:
                    json.dump(
                        {
                            "generated_at": datetime.datetime.now(
                                datetime.timezone.utc
                            ).isoformat(),
                            "committed": args.commit,
                            "stats": stats,
                            "tasks": manifest,
                            "failures": [
                                {"task_id": t, "reason": r} for t, r in failures
                            ],
                        },
                        fh,
                        indent=2,
                    )
                print(f"manifest         : {args.manifest}")

        if failures:
            print(f"\n{len(failures)} task(s) need attention:")
            for task_id, reason in failures[:20]:
                print(f"  task {task_id}: {reason}")
            if len(failures) > 20:
                print(f"  ... and {len(failures) - 20} more")

        if not args.commit:
            print("\nDRY RUN -- nothing was written. Re-run with --commit.")
        return 1 if failures else 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

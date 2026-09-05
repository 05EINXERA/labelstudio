"""
Compare every task's annotation rows against its legacy blob, and report drift.

The gate on the Phase C cutover. Through Phases A and B both representations
are written on every save, so they must agree; any task where they do not is a
bug in the mirror, and finding it here -- while the blob is still authoritative
and rollback is still a redeploy -- is the entire reason those phases are
separate from the cutover.

Read-only. It never writes, so it is safe to run against production at any time.

    python scripts/reconcile_annotations.py
    python scripts/reconcile_annotations.py --verbose --report drift.json

Exit status is 1 when any task has drifted, so it can gate a deploy.

A task that has no rows but a non-empty blob is reported as `not-backfilled`
rather than as drift: that is the expected state before the backfill has run,
and conflating the two would make the report useless during the migration.
"""

import argparse
import datetime
import json
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import models  # noqa: E402
from database import SessionLocal  # noqa: E402
from formats.annotation_rows import rows_to_dicts  # noqa: E402

logger = logging.getLogger("reconcile_annotations")


def _blob_dicts(raw):
    if not raw or not raw.strip():
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, list) else None


def _comparable(ann: dict) -> dict:
    """One annotation reduced to what storage can faithfully represent.

    Mirrors backfill_annotations._comparable: coordinates are stored as floats
    and `order` as an int, so a numeric string in the blob is a deliberate
    normalisation rather than drift.
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


def _compare(blob: list, rows: list):
    """None if the two agree, else a short description of the first difference."""
    from_rows = {a["id"]: a for a in rows}
    from_blob = {}
    for ann in blob:
        if isinstance(ann, dict) and ann.get("id"):
            from_blob[ann["id"]] = _comparable(ann)

    # Ids the blob carries without one cannot be matched; compare on count only.
    unidentified = sum(
        1 for a in blob if isinstance(a, dict) and not a.get("id")
    )

    if len(from_blob) + unidentified != len(from_rows):
        return f"count blob={len(blob)} rows={len(from_rows)}"

    missing = set(from_blob) - set(from_rows)
    if missing:
        return f"{len(missing)} in blob but not in rows, e.g. {sorted(missing)[:3]}"

    extra = set(from_rows) - set(from_blob)
    if extra and not unidentified:
        return f"{len(extra)} in rows but not in blob, e.g. {sorted(extra)[:3]}"

    for ident, expected in from_blob.items():
        actual = from_rows[ident]
        if expected != actual:
            keys = sorted(set(expected) | set(actual), key=str)
            differing = [k for k in keys if expected.get(k) != actual.get(k)]
            return f"annotation {ident} differs on {differing}"
    return None


def reconcile(db, task_ids=None, verbose=False):
    query = db.query(models.Task.id, models.Task.annotations).order_by(models.Task.id)
    if task_ids:
        query = query.filter(models.Task.id.in_(task_ids))

    stats = {"tasks": 0, "agree": 0, "drifted": 0,
             "not_backfilled": 0, "unreadable_blob": 0, "both_empty": 0}
    drift = []

    for task_id, raw in query.all():
        stats["tasks"] += 1

        rows = rows_to_dicts(
            db.query(models.Annotation)
            .filter(models.Annotation.task_id == task_id)
            .order_by(models.Annotation.order, models.Annotation.id)
            .all()
        )
        blob = _blob_dicts(raw)

        if blob is None:
            stats["unreadable_blob"] += 1
            drift.append((task_id, "blob is unreadable"))
            continue

        if not blob and not rows:
            stats["both_empty"] += 1
            continue

        if blob and not rows:
            # Expected before the backfill; not a mirror bug.
            stats["not_backfilled"] += 1
            if verbose:
                logger.info("Task %s: %d annotations, no rows yet", task_id, len(blob))
            continue

        problem = _compare(blob, rows)
        if problem:
            stats["drifted"] += 1
            drift.append((task_id, problem))
            logger.warning("Task %s DRIFTED: %s", task_id, problem)
        else:
            stats["agree"] += 1
            if verbose:
                logger.info("Task %s: %d annotations agree", task_id, len(rows))

    return stats, drift


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--task", type=int, action="append", dest="tasks",
                        help="limit to this task id; repeatable")
    parser.add_argument("--report", help="write the drift report here as JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    db = SessionLocal()
    try:
        stats, drift = reconcile(db, args.tasks, args.verbose)

        print(f"\ntasks compared    : {stats['tasks']}")
        print(f"  agree           : {stats['agree']}")
        print(f"  both empty      : {stats['both_empty']}")
        print(f"  not backfilled  : {stats['not_backfilled']}")
        print(f"  unreadable blob : {stats['unreadable_blob']}")
        print(f"  DRIFTED         : {stats['drifted']}")

        if args.report:
            with open(args.report, "w", encoding="utf-8") as fh:
                json.dump({
                    "generated_at": datetime.datetime.now(
                        datetime.timezone.utc
                    ).isoformat(),
                    "stats": stats,
                    "drift": [{"task_id": t, "reason": r} for t, r in drift],
                }, fh, indent=2)
            print(f"report            : {args.report}")

        if drift:
            print(f"\n{len(drift)} task(s) drifted:")
            for task_id, reason in drift[:20]:
                print(f"  task {task_id}: {reason}")
            if len(drift) > 20:
                print(f"  ... and {len(drift) - 20} more")
            print("\nThe cutover must NOT proceed until these are explained.")
            return 1

        if stats["not_backfilled"]:
            print(f"\n{stats['not_backfilled']} task(s) still have no rows -- "
                  "run scripts/backfill_annotations.py --commit before cutover.")
        else:
            print("\nEvery task agrees. The blob and the rows are interchangeable.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

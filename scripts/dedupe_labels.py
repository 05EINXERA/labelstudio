"""
Merge duplicate classes — several `labels` rows in one project whose names
normalise to the same thing — down to one row each, moving their annotations.

Project 410 accumulated six classes named "object" on 2026-09-08, created by
nobody: the canvas mints a fresh uuid for any class it has not seen
(`ensureLabel`), and POST /api/labels resolved by *id only*, so a fresh uuid
never matched an existing row and the endpoint always inserted. Four of the six
landed inside 37 seconds of reloading one task.
See .devnotes/fix-class-creation/01_AUDIT.md.

**Run this only after the Phase 1 server fix is deployed.** Until POST
/api/labels resolves by name, clients are still minting duplicates and this
script is bailing water. It is also the prerequisite for the unique index on
(project_id, name), which cannot be created while duplicates exist.

Do not confuse this with `scripts/repair_orphaned_labels.py`. That one treats
the opposite defect — annotations whose `label_id` is NULL because the class
could not be resolved — and repairs *annotations*. This one merges *label rows*.
The two incidents share a symptom ("everything shows as Object") and nothing
else.

How a duplicate group is resolved:

    all rows in one project whose normalize_label_name(name) matches
        -> the row with the most annotations wins  (ties: lowest id)
        -> every other row's annotations are moved onto the winner
        -> the losing rows are deleted
        -> the winner's name is rewritten to its canonical form

The winner is the most-referenced row rather than the oldest because that is
the one whose disappearance would cost the most if anything went wrong.

It is deliberately conservative:
  * dry run by default; --commit is required to write,
  * writes a rollback JSON of every row it will touch before writing,
  * **never deletes an annotation** — this is a merge, not a purge. Contrast
    `purge_annotations_for_labels` in api/routers/labels.py, which is the
    destructive sibling and must not be reused here,
  * never creates a class,
  * bumps `tasks.updated_at` and stamps `last_client_id` on every task whose
    annotations moved, so a tab holding one open takes a 409 and reloads rather
    than writing the old label ids back.

Usage (dry run first, always):
    # survey the whole database without touching it
    python scripts/dedupe_labels.py --all

    # one project
    python scripts/dedupe_labels.py --project 410

    # then, having read the report:
    python scripts/dedupe_labels.py --project 410 --commit
"""
import argparse
import datetime
import json
import os
import pathlib
import sys
from collections import defaultdict

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import models  # noqa: E402
from database import SessionLocal, commit_with_retry  # noqa: E402
from formats.common import normalize_label_name  # noqa: E402

# Stamped as the last writer of every task whose annotations were remapped.
# Mirrors the sentinel repair_orphaned_labels.py and the move endpoint use, for
# the same reason: a browser tab holding this task open still has the old label
# ids in memory and must be made to reload rather than save over the merge.
# No real client id can collide — tabs use a uuid4.
DEDUPE_CLIENT_SENTINEL = "server:deduped"


def _groups(db, project_ids):
    """{(project_id, canonical_name): [label, ...]} for duplicated names only."""
    query = db.query(models.Label)
    if project_ids:
        query = query.filter(models.Label.project_id.in_(project_ids))

    buckets = defaultdict(list)
    for label in query.all():
        # `labels.project_id` is nullable, and the dev database has such rows.
        # They are skipped rather than bucketed: every one of them would group
        # under a single (None, name) key, so a merge would collapse classes
        # from unrelated projects into one. A label belonging to no project is
        # its own problem and not one this script should guess at.
        if label.project_id is None:
            continue
        buckets[(label.project_id, normalize_label_name(label.name))].append(label)

    return {key: rows for key, rows in buckets.items() if len(rows) > 1}


def _usage(db, label_ids):
    """{label_id: annotation count}, counted in SQL.

    Never in Python: counting annotations by parsing them is what stalled the
    server (CLAUDE.md rule 11b, .devnotes/performance-fixes/).
    """
    if not label_ids:
        return {}
    from sqlalchemy import func

    rows = (
        db.query(models.Annotation.label_id, func.count(models.Annotation.id))
        .filter(models.Annotation.label_id.in_(list(label_ids)))
        .group_by(models.Annotation.label_id)
        .all()
    )
    return {label_id: count for label_id, count in rows}


def _plan(db, groups):
    """Decide, per duplicate group, which row survives and what moves.

    Returns a list of dicts, one per group, each carrying the winner, the
    losers, and how many annotations each loser contributes.
    """
    all_ids = {label.id for rows in groups.values() for label in rows}
    usage = _usage(db, all_ids)

    plans = []
    for (project_id, name), rows in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        # Most-referenced wins; ties broken by lowest id so the choice is
        # deterministic and a dry run predicts exactly what --commit will do.
        ordered = sorted(rows, key=lambda l: (-usage.get(l.id, 0), l.id))
        winner, losers = ordered[0], ordered[1:]
        plans.append({
            "project_id": project_id,
            "name": name,
            "winner": winner,
            "winner_usage": usage.get(winner.id, 0),
            "losers": [(l, usage.get(l.id, 0)) for l in losers],
            "moving": sum(usage.get(l.id, 0) for l in losers),
        })
    return plans


def _affected_task_ids(db, label_ids):
    """Tasks holding an annotation that is about to be remapped."""
    if not label_ids:
        return set()
    return {
        task_id
        for (task_id,) in db.query(models.Annotation.task_id)
        .filter(models.Annotation.label_id.in_(list(label_ids)))
        .distinct()
    }


def _write_rollback(plans, path):
    """Every row this run will touch, before it touches any of them."""
    payload = {
        "written_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "groups": [
            {
                "project_id": p["project_id"],
                "name": p["name"],
                "winner": {"id": p["winner"].id, "name": p["winner"].name,
                           "color": p["winner"].color, "annotations": p["winner_usage"]},
                "losers": [
                    {"id": l.id, "name": l.name, "color": l.color, "annotations": n}
                    for l, n in p["losers"]
                ],
            }
            for p in plans
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _report(plans):
    if not plans:
        print("No duplicate classes found.")
        return

    total_rows = sum(len(p["losers"]) for p in plans)
    total_moving = sum(p["moving"] for p in plans)
    by_project = defaultdict(list)
    for p in plans:
        by_project[p["project_id"]].append(p)

    for project_id in sorted(by_project):
        print(f"\nProject {project_id}")
        for p in by_project[project_id]:
            print(f'  "{p["name"]}" — {len(p["losers"]) + 1} rows -> 1')
            print(f'    keep   {p["winner"].id}  ({p["winner_usage"]} annotations)'
                  f'  name={p["winner"].name!r}')
            for label, count in p["losers"]:
                moved = f"{count} annotations move" if count else "unused"
                print(f'    merge  {label.id}  ({moved})  name={label.name!r}')

    print(f"\n{total_rows} duplicate rows would be merged away; "
          f"{total_moving} annotations would be remapped.")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--project", type=int, action="append",
                       help="project id (repeatable)")
    scope.add_argument("--all", action="store_true",
                       help="every project in the database")
    parser.add_argument("--commit", action="store_true",
                        help="actually write; without this it is a dry run")
    parser.add_argument("--rollback-dir", default="backups",
                        help="where the rollback JSON is written (default: backups/)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        groups = _groups(db, None if args.all else args.project)
        plans = _plan(db, groups)
        _report(plans)

        if not plans:
            return 0
        if not args.commit:
            print("\nDry run — nothing was written. Re-run with --commit to apply.")
            return 0

        loser_ids = [l.id for p in plans for l, _ in p["losers"]]
        task_ids = _affected_task_ids(db, loser_ids)

        # The rollback file is written before anything changes, and a failure to
        # write it aborts the run: a merge with no record of what it merged is
        # not recoverable.
        rollback_dir = pathlib.Path(args.rollback_dir)
        rollback_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        rollback = rollback_dir / f"dedupe_labels-{stamp}.json"
        _write_rollback(plans, rollback)
        print(f"\nRollback written to {rollback}")

        remapped = 0
        for p in plans:
            for label, _count in p["losers"]:
                remapped += (
                    db.query(models.Annotation)
                    .filter(models.Annotation.label_id == label.id)
                    .update({models.Annotation.label_id: p["winner"].id},
                            synchronize_session=False)
                )
                db.delete(label)
            # The matching key, so the unique index in the Phase 3 revision can
            # be created and a later save cannot reintroduce a variant. The
            # *display* name is left as the winner's own: it carries the casing
            # its author chose, which the COCO and FastLabel exports round-trip.
            p["winner"].name_key = p["name"]

        if task_ids:
            # Same reasoning as the move endpoint: a tab with this task open
            # holds the old label ids and must take a 409 and reload.
            db.query(models.Task).filter(models.Task.id.in_(task_ids)).update(
                {
                    models.Task.updated_at: datetime.datetime.now(datetime.timezone.utc),
                    models.Task.last_client_id: DEDUPE_CLIENT_SENTINEL,
                },
                synchronize_session=False,
            )

        commit_with_retry(db)
        print(f"Merged {len(loser_ids)} rows, remapped {remapped} annotations, "
              f"touched {len(task_ids)} tasks.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())

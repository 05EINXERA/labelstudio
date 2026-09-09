"""
Re-resolve annotations whose class was orphaned, by matching the lost class
name against the classes of the project the task is in now.

An orphaned annotation is one with `label_id = NULL` carrying the id it lost in
`extra._orphanedLabelId`. The canvas cannot resolve such a shape to a class, so
it renders as an unnamed "Object".

Written for the 2026-09-09 incident on task 1229
(.devnotes/move-task-feature/07_DRAFT_STALENESS.md): a stale localStorage draft,
written before the task was moved between projects, carried the *source*
project's label ids. Restoring it discarded the correctly-remapped set the tab
had just fetched, and the next autosave sent the stale ids back — which the
server could only orphan. The frontend fix is in frontend/js/state.js +
components/workspace.js; this script is the data-recovery half.

**A move cannot repair this by itself, and never will.** `build_label_map`
considers only annotations with a non-NULL `label_id`
(`formats/label_reconcile.used_label_ids`), so an already-orphaned row is
invisible to every future move — moving the task back and forth changes
nothing. It only *looks* correct in the original project because
`row_to_dict` restores `_orphanedLabelId` to `labelId` on the wire and those
ids are valid there; the stored row is NULL either way.

How a row is repaired:

    extra._orphanedLabelId  ->  the labels row it names  ->  that label's NAME
                            ->  the same name in the task's CURRENT project
                            ->  that label's id

Name matching is case-insensitive, the same rule `formats/label_reconcile.py`
and the class importer use, so this agrees with what a move would have done had
the row not been orphaned.

It is deliberately conservative:
  * dry run by default; --commit is required to write,
  * writes a rollback JSON of every row it will touch before writing,
  * repairs only rows it can resolve to exactly one destination class,
  * never creates a class, never renames one, never deletes an annotation,
  * leaves `_orphanedLabelId` in place as provenance unless --clear-provenance,
  * bumps `tasks.updated_at` and stamps `last_client_id`, so any tab holding a
    repaired task takes a 409 and reloads rather than writing the old state back.

Usage (dry run first, always):
    # everything repairable in one project
    python scripts/repair_orphaned_labels.py --project 285

    # one task
    python scripts/repair_orphaned_labels.py --task 1229

    # survey the whole database without touching it
    python scripts/repair_orphaned_labels.py --all

    # then, having read the report:
    python scripts/repair_orphaned_labels.py --task 1229 --commit
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

# Stamped as the last writer of a repaired task. Mirrors the sentinel the move
# endpoint uses (api/routers/tasks.MOVE_CLIENT_SENTINEL) and exists for the same
# reason: a browser tab holding this task open still has the orphaned set in
# memory, and must be made to reload rather than allowed to save over the
# repair. No real client id can collide — tabs use a uuid4.
REPAIR_CLIENT_SENTINEL = "server:repaired"


def _orphaned_id(row) -> str | None:
    """The label id this annotation lost, or None if it is not an orphan."""
    if row.label_id is not None or not row.extra:
        return None
    try:
        extra = json.loads(row.extra)
    except (ValueError, TypeError):
        return None
    if not isinstance(extra, dict):
        return None
    value = extra.get("_orphanedLabelId")
    return value if isinstance(value, str) and value else None


def _labels_by_project(db):
    """{project_id: {lowercased name: [label, ...]}}.

    A list per name rather than one label: two classes in a project can differ
    only by case, and a name that resolves ambiguously must be reported and
    skipped rather than resolved by luck of iteration order.
    """
    out = defaultdict(lambda: defaultdict(list))
    for label in db.query(models.Label).all():
        out[label.project_id][(label.name or "").strip().lower()].append(label)
    return out


def _plan(db, rows_by_task, labels_by_project, tasks):
    """Decide, per orphaned row, what it should be repaired to.

    Returns (repairs, skips) where a repair is
    (row, task, old_id, class_name, new_label) and a skip is (row, task, why).
    """
    all_labels = {l.id: l for l in db.query(models.Label).all()}
    repairs, skips = [], []

    for task_id, rows in sorted(rows_by_task.items()):
        task = tasks[task_id]
        by_name = labels_by_project.get(task.project_id, {})

        for row in rows:
            old_id = _orphaned_id(row)
            source = all_labels.get(old_id)
            if source is None:
                # The class it named is gone entirely, so there is no name to
                # match on. Nothing here can recover it; a human has to say
                # what it should have been.
                skips.append((row, task, f"lost class {old_id} no longer exists"))
                continue

            name = (source.name or "").strip()
            candidates = by_name.get(name.lower(), [])
            if not candidates:
                skips.append((
                    row, task,
                    f'project {task.project_id} has no class named "{name}"',
                ))
                continue
            if len(candidates) > 1:
                skips.append((
                    row, task,
                    f'"{name}" is ambiguous in project {task.project_id} '
                    f"({len(candidates)} classes share it)",
                ))
                continue

            target = candidates[0]
            if target.id == old_id:
                # Already correct for this project: the row was orphaned while
                # the task lived somewhere else and has since come home. The
                # stored label_id is still NULL, so this is a real repair.
                pass
            repairs.append((row, task, old_id, name, target))

    return repairs, skips


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    scope = parser.add_mutually_exclusive_group(required=True)
    scope.add_argument("--task", type=int, help="repair one task")
    scope.add_argument("--project", type=int, help="repair every task in one project")
    scope.add_argument("--all", action="store_true",
                       help="survey every project (use with no --commit to size the damage)")
    parser.add_argument("--commit", action="store_true",
                        help="actually write (default: dry run)")
    parser.add_argument("--clear-provenance", action="store_true",
                        help="also remove _orphanedLabelId once the row is repaired "
                             "(default: keep it, so the history stays readable)")
    parser.add_argument("--rollback-dir", default="backups",
                        help="where to write the pre-repair copy of the affected rows")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        # Scope the task set first, then pull only those tasks' annotations.
        task_query = db.query(models.Task)
        if args.task is not None:
            task_query = task_query.filter(models.Task.id == args.task)
        elif args.project is not None:
            task_query = task_query.filter(models.Task.project_id == args.project)
        tasks = {t.id: t for t in task_query.all()}

        if args.task is not None and not tasks:
            print(f"ERROR: task {args.task} does not exist.")
            return 1
        if not tasks:
            print("No tasks in scope.")
            return 0

        # Only NULL-labelled rows can be orphans, so the scan never touches a
        # healthy annotation. `extra` is read for exactly these.
        candidates = (
            db.query(models.Annotation)
            .filter(
                models.Annotation.task_id.in_(list(tasks)),
                models.Annotation.label_id.is_(None),
            )
            .all()
        )

        rows_by_task = defaultdict(list)
        for row in candidates:
            if _orphaned_id(row):
                rows_by_task[row.task_id].append(row)

        if not rows_by_task:
            print("No orphaned annotations found in scope. Nothing to do.")
            return 0

        repairs, skips = _plan(db, rows_by_task, _labels_by_project(db), tasks)

        # --- report ---------------------------------------------------------
        total = sum(len(v) for v in rows_by_task.values())
        print(f"Found {total} orphaned annotation(s) across {len(rows_by_task)} task(s).\n")

        by_task = defaultdict(list)
        for row, task, old_id, name, target in repairs:
            by_task[task.id].append((name, target))
        for task_id in sorted(rows_by_task):
            task = tasks[task_id]
            fixable = by_task.get(task_id, [])
            print(f"task {task_id} ({task.description}) in project {task.project_id}: "
                  f"{len(rows_by_task[task_id])} orphaned, {len(fixable)} repairable")
            counts = defaultdict(int)
            for name, target in fixable:
                counts[(name, target.id)] += 1
            for (name, target_id), n in sorted(counts.items()):
                print(f'    {n:>4}  ->  "{name}"  ({target_id})')

        if skips:
            print(f"\n{len(skips)} row(s) cannot be repaired automatically:")
            seen = defaultdict(int)
            for row, task, why in skips:
                seen[(task.id, why)] += 1
            for (task_id, why), n in sorted(seen.items()):
                print(f"    task {task_id}: {n} row(s) — {why}")

        if not repairs:
            print("\nNothing repairable. No changes made.")
            return 0

        if not args.commit:
            print(f"\nDRY RUN — nothing written. {len(repairs)} row(s) would be repaired.")
            print("Re-run with --commit to apply.")
            return 0

        # --- rollback -------------------------------------------------------
        # Written before the first mutation, and the run aborts if it cannot be
        # written: a repair with no way back is not a repair.
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
        rollback_dir = pathlib.Path(args.rollback_dir)
        rollback_path = rollback_dir / f"orphan-repair-{stamp}.json"
        payload = [
            {
                "annotation_id": row.id,
                "task_id": row.task_id,
                "project_id": task.project_id,
                "label_id": row.label_id,
                "extra": row.extra,
                "repaired_to": target.id,
                "class_name": name,
            }
            for row, task, old_id, name, target in repairs
        ]
        try:
            rollback_dir.mkdir(parents=True, exist_ok=True)
            rollback_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError as exc:
            print(f"ERROR: could not write rollback file {rollback_path}: {exc}")
            print("Refusing to repair without one.")
            return 1
        print(f"\nRollback written: {rollback_path}")

        # --- apply ----------------------------------------------------------
        touched_tasks = set()
        for row, task, old_id, name, target in repairs:
            row.label_id = target.id
            touched_tasks.add(task.id)

            if args.clear_provenance:
                try:
                    extra = json.loads(row.extra) if row.extra else {}
                except (ValueError, TypeError):
                    extra = {}
                if isinstance(extra, dict):
                    extra.pop("_orphanedLabelId", None)
                    row.extra = json.dumps(extra) if extra else None

        now = datetime.datetime.now(datetime.timezone.utc)
        (
            db.query(models.Task)
            .filter(models.Task.id.in_(touched_tasks))
            .update(
                {
                    # Task.updated_at carries no onupdate (models.py), so a write
                    # that does not assign it leaves the task looking untouched —
                    # and leaves every open tab holding a token that no longer
                    # describes the stored annotations.
                    models.Task.updated_at: now,
                    models.Task.last_client_id: REPAIR_CLIENT_SENTINEL,
                },
                synchronize_session=False,
            )
        )

        commit_with_retry(db)
        print(f"Repaired {len(repairs)} annotation(s) across {len(touched_tasks)} task(s).")
        print("Open tabs on these tasks will 409 on their next save and reload, "
              "which is intended.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

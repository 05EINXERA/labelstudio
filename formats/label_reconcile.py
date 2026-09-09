"""Class reconciliation for a cross-project task move.

`labels` rows are per project: `Label.id` is a globally unique uuid, but every
read is scoped `WHERE project_id = ?`, so project A and project AA each hold
their *own* row for the class "car". `annotations.label_id` is a foreign key to
`labels.id` with no project in the key.

That combination is what makes a naive move dangerous. Moving a task with
`UPDATE tasks SET project_id` leaves the foreign key **satisfied** — the label
row it names still exists, it just belongs to the project the task left. There
is no database error, and the damage surfaces later and somewhere else:

- the destination's Classes page and the canvas cannot resolve the id, so every
  shape renders as an unknown class;
- `sync_task_annotations_for_project` resolves `known_label_ids` from the task's
  *current* project, so the **first autosave after the move** treats every
  labelId as unknown, NULLs it and stashes the value in
  `extra["_orphanedLabelId"]` — it looks like the annotator destroyed their own
  work.

So a move has to remap ids into the destination. This module owns that: it is
where the matching rule, the label creation and the two write statements live.
It takes a Session but imports nothing from `api/` (docs/ARCHITECTURE.md § 2.1),
so it is testable without a server.

Nothing here commits. The caller owns the transaction, so a later failure rolls
the remap back with the move that caused it.

See `.devnotes/move-task-feature/02_DESIGN.md` § 3.
"""
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

import models

logger = logging.getLogger(__name__)

# Strategy values, mirrored by the `Literal` in schemas.MoveTasks.
MATCH_OR_CREATE = "match_or_create"
MATCH_ONLY = "match_only"
STRATEGIES = (MATCH_OR_CREATE, MATCH_ONLY)


@dataclass
class LabelPlan:
    """What `build_label_map` decided, before anything is written.

    `mapping` covers every label id referenced by the moving annotations:
    old id -> new id in the destination, or **None** meaning "no counterpart,
    orphan it" (only produced by `match_only`).
    """

    mapping: Dict[str, Optional[str]] = field(default_factory=dict)
    created: int = 0
    matched: int = 0
    # Names that could not be placed in the destination (match_only only).
    unmatched_names: List[str] = field(default_factory=list)
    # Source class names that collapsed onto one destination label because they
    # differ only by case. Reported so the owner is told the two merged.
    merged_names: List[str] = field(default_factory=list)

    @property
    def orphaned(self) -> List[str]:
        """Label ids that will be NULLed rather than remapped."""
        return [old for old, new in self.mapping.items() if new is None]


def used_label_ids(db, task_ids: Sequence[int]) -> List[str]:
    """The distinct label ids the given tasks' annotations actually reference.

    Deliberately *not* the source project's whole class set. Moving three tasks
    must not import forty unused classes into the destination, and the owner
    who wanted the whole set can copy it explicitly.

    One indexed query: `annotations.task_id` and `annotations.label_id` are both
    indexed, and this never touches the annotation payloads.
    """
    if not task_ids:
        return []
    rows = (
        db.query(models.Annotation.label_id)
        .filter(
            models.Annotation.task_id.in_(task_ids),
            models.Annotation.label_id.isnot(None),
        )
        .distinct()
        .all()
    )
    return [row[0] for row in rows]


def build_label_map(
    db,
    task_ids: Sequence[int],
    target_project_id: int,
    strategy: str = MATCH_OR_CREATE,
) -> LabelPlan:
    """Decide, for each class the moving annotations use, its destination id.

    Matching is **by name, case-insensitively** — the rule the class importer
    already uses (`api/routers/labels.py:import_labels`) and the one the Classes
    UI enforces, so a move and an import agree about what "the same class" means.

    Colour is not part of the identity. A matched class keeps the *destination's*
    colour, because that is what the destination's annotators already read.

    `match_or_create` creates a destination label for anything unmatched, so no
    class assignment is ever lost. `match_only` leaves it unmatched (mapping to
    None) for a destination whose class set is deliberately curated.

    New rows are `db.add`ed but not committed; they are flushed so the caller's
    UPDATE can reference their ids.
    """
    if strategy not in STRATEGIES:
        raise ValueError(f"Unknown class strategy {strategy!r}")

    plan = LabelPlan()
    old_ids = used_label_ids(db, task_ids)
    if not old_ids:
        return plan

    source_labels = (
        db.query(models.Label).filter(models.Label.id.in_(old_ids)).all()
    )
    # An id with no label row cannot normally exist — the FK is ON DELETE SET
    # NULL — but a row written before the FK existed could. Leave it alone
    # rather than inventing a class for it.
    found = {l.id for l in source_labels}
    for missing in set(old_ids) - found:
        logger.warning(
            "Moving annotations reference label %s, which has no row; leaving as is",
            missing,
        )

    target_by_name = {
        (l.name or "").strip().lower(): l
        for l in db.query(models.Label)
        .filter(models.Label.project_id == target_project_id)
        .all()
    }

    # Destination labels created during *this* call, so two source classes that
    # normalise to the same name reuse one new row instead of inserting twice
    # and violating the name uniqueness the Classes UI assumes (M-08).
    created_by_name: Dict[str, models.Label] = {}
    claimed_by_name: Dict[str, str] = {}

    for label in source_labels:
        # Must agree with formats.common.normalize_label_name, which is the
        # form actually stored and what the unique index on (project_id, name)
        # is taken over. Inlined rather than imported: this module deliberately
        # depends on `models` alone (see the header), and common.py drags in
        # PIL and config. tests/test_label_reconcile.py pins the agreement.
        key = " ".join((label.name or "").replace("_", " ").strip().lower().split()) or "object"

        existing = target_by_name.get(key) or created_by_name.get(key)
        if existing is not None:
            plan.mapping[label.id] = existing.id
            plan.matched += 1
            if key in claimed_by_name and claimed_by_name[key] != label.id:
                plan.merged_names.append(label.name)
            claimed_by_name.setdefault(key, label.id)
            continue

        if strategy == MATCH_ONLY:
            plan.mapping[label.id] = None
            plan.unmatched_names.append(label.name)
            continue

        new_label = models.Label(
            id=uuid.uuid4().hex,
            name=label.name,
            # The matching key the unique index compares. Same expression as
            # `key` above and as formats.common.normalize_label_name.
            name_key=key,
            color=label.color,
            project_id=target_project_id,
        )
        db.add(new_label)
        created_by_name[key] = new_label
        claimed_by_name[key] = label.id
        plan.mapping[label.id] = new_label.id
        plan.created += 1

    if plan.created:
        # The UPDATE in apply_label_map names these ids, so the rows have to
        # exist in the database before it runs. Flush, not commit: one
        # transaction (CLAUDE.md rule 10 applies to the caller's commit).
        db.flush()

    return plan


def apply_label_map(db, task_ids: Sequence[int], plan: LabelPlan) -> int:
    """Rewrite the moving annotations' label ids. Returns rows touched.

    **The `task_id IN (...)` scope is the whole safety property of this
    function.** A statement keyed on `label_id` alone would rewrite annotations
    on every task in the source project that uses the same class, including the
    ones staying behind. That is the single most dangerous statement in the
    feature, and `tests/test_label_reconcile.py` pins it by name.

    One UPDATE per mapped class, not per task, so the cost scales with the size
    of the class set rather than the size of the batch.
    """
    if not task_ids or not plan.mapping:
        return 0

    touched = 0
    for old_id, new_id in plan.mapping.items():
        if new_id is None or new_id == old_id:
            continue
        touched += (
            db.query(models.Annotation)
            .filter(
                models.Annotation.task_id.in_(task_ids),
                models.Annotation.label_id == old_id,
            )
            .update({models.Annotation.label_id: new_id}, synchronize_session=False)
        )

    touched += _orphan_unmatched(db, task_ids, plan)
    return touched


def _orphan_unmatched(db, task_ids: Sequence[int], plan: LabelPlan) -> int:
    """NULL the label ids that have no destination, preserving the original.

    The original id goes into `extra["_orphanedLabelId"]` — exactly the
    representation `formats.annotation_rows.dict_to_row_kwargs` produces for a
    label it cannot resolve, so `row_to_dict` restores it to `labelId` and the
    wire format is unchanged. The fact stays recoverable instead of being
    silently dropped (06_PROGRESS.md D5 established this convention).

    Done in Python rather than SQL because `extra` is a JSON text column and a
    JSON expression would have to be written twice, once for SQLite and once for
    Postgres. The row count here is small by construction: only classes the
    destination genuinely lacks, under `match_only`.
    """
    orphans = plan.orphaned
    if not orphans:
        return 0

    rows = (
        db.query(models.Annotation)
        .filter(
            models.Annotation.task_id.in_(task_ids),
            models.Annotation.label_id.in_(orphans),
        )
        .all()
    )

    for row in rows:
        extra = {}
        if row.extra:
            try:
                parsed = json.loads(row.extra)
                if isinstance(parsed, dict):
                    extra = parsed
            except (ValueError, TypeError):
                logger.warning(
                    "Annotation %s on task %s has unparseable extra; replacing it",
                    row.id, row.task_id,
                )
        # setdefault, not assignment: a row that was *already* orphaned before
        # this move carries the id it lost then, and that provenance is older
        # and more informative than the one we are about to discard.
        extra.setdefault("_orphanedLabelId", row.label_id)
        row.extra = json.dumps(extra)
        row.label_id = None

    return len(rows)

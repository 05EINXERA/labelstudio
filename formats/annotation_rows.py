"""Conversion between annotation dicts and `Annotation` rows.

The wire format is unchanged by the normalisation: clients still send and
receive a JSON array of annotation objects. Only *storage* changed, from one
blob per task to one row per shape. This module is the single boundary where
those two representations meet, so the mapping is defined once and tested
without a database or a server.

Why the mapping is not simply "one column per key": the real data carries eight
fields the schema does not model (`label`, `visible`, `promptPoints`,
`promptLabels`, `source`, `author`, `w`, `h`), some client depends on each of
them, and the canvas will add more. Those ride in `extra`, so the conversion is
lossless without the schema having to predict what gets added next.

See .devnotes/performance-fixes/03_NORMALIZE_ANNOTATIONS.md and 06_PROGRESS.md
D1 for the data survey the column set is derived from.
"""

import json
import logging
import uuid
from typing import Any, Optional

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

import models

logger = logging.getLogger(__name__)

# The keys that have their own column. Everything else in an annotation object
# is preserved in `extra`.
#
# `id` and `task_id` are excluded deliberately: they are the primary key, set
# from the row itself rather than from the payload.
MODELLED_KEYS = frozenset({
    "id", "type", "labelId", "points",
    "x", "y", "width", "height",
    "text", "color", "order", "groupId",
})


def _json_or_none(value: Any) -> Optional[str]:
    """Serialise a value for an opaque JSON column, or None if there is none.

    An unserialisable value is dropped with a warning rather than raising: one
    bad field must not fail a save that is otherwise fine, and the alternative
    (letting TypeError escape) would make this conversion the cause of the data
    loss the whole normalisation exists to prevent.
    """
    if value is None:
        return None
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        logger.warning("Dropping unserialisable annotation field: %r", value)
        return None


def _float_or_none(value: Any) -> Optional[float]:
    """Coerce to float, or None. Numeric strings are accepted.

    The real data holds coordinates as both numbers and strings (`'981.75'`),
    because they have passed through JSON round-trips written by several
    generations of client. A string that will not parse becomes NULL rather
    than raising, matching how every existing reader treats a bad coordinate.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> Optional[int]:
    """Coerce to int, or None. Floats truncate; bools are not integers here."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def dict_to_row_kwargs(ann: dict, task_id: int, known_label_ids=None, seq=None) -> dict:
    """The `Annotation(**kwargs)` for one annotation dict.

    `id` is minted when absent — four annotations in the real data have no id,
    and the save path already mints one for id-less incoming shapes, so this
    matches existing behaviour rather than introducing a new rule.

    `known_label_ids`, when given, is the set of label ids that actually exist.
    A `labelId` outside it is stored as NULL with the original value preserved
    in `extra["_orphanedLabelId"]`.

    Why this is needed at all: the blob had no foreign key, so a `labelId`
    could outlive the label it named. 656 real annotations (4.6% of those
    carrying a labelId, across 9 tasks) reference 9 labels that no longer
    exist. The new FK would reject every one of them, which would abort the
    backfill of otherwise-perfectly-good tasks.

    Dropping the value silently was rejected: `purge_annotations_for_labels`
    deletes annotations when a class is deleted, so a shape that still names a
    dead label is one that *survived* deletion — real work, whose provenance is
    the only clue to what it used to be. NULL matches what the FK's
    `ondelete="SET NULL"` would have done had it always existed, and the
    preserved id keeps the fact recoverable.
    """
    extra = {k: v for k, v in ann.items() if k not in MODELLED_KEYS and k != "extra"}
    # An `extra` already present in the payload (a round-tripped row) is merged
    # rather than nested, so a value cannot gain a level of wrapping each time
    # it passes through storage.
    nested = ann.get("extra")
    if isinstance(nested, dict):
        extra.update(nested)

    label_id = ann.get("labelId")
    if known_label_ids is not None and label_id is not None and label_id not in known_label_ids:
        extra["_orphanedLabelId"] = label_id
        label_id = None

    return {
        "id": str(ann.get("id") or uuid.uuid4()),
        "task_id": task_id,
        "label_id": label_id,
        "type": ann.get("type"),
        "points": _json_or_none(ann.get("points")),
        "x": _float_or_none(ann.get("x")),
        "y": _float_or_none(ann.get("y")),
        "width": _float_or_none(ann.get("width")),
        "height": _float_or_none(ann.get("height")),
        "text": ann.get("text"),
        "color": ann.get("color"),
        "order": _int_or_none(ann.get("order")),
        "seq": seq,
        "group_id": ann.get("groupId"),
        "extra": _json_or_none(extra) if extra else None,
    }


def row_to_dict(row: "models.Annotation") -> dict:
    """One row back as the annotation dict a client expects.

    Only keys that are actually present are emitted. The blob format is sparse
    — most annotations carry no `text`, `order` or `groupId` — and emitting
    them as explicit nulls would change what every reader and export sees.
    """
    out: dict = {"id": row.id}

    if row.type is not None:
        out["type"] = row.type
    if row.label_id is not None:
        out["labelId"] = row.label_id
    if row.points is not None:
        try:
            out["points"] = json.loads(row.points)
        except (ValueError, TypeError):
            logger.warning("Annotation %s on task %s has unparseable points",
                           row.id, row.task_id)
    for attr, key in (("x", "x"), ("y", "y"), ("width", "width"), ("height", "height")):
        value = getattr(row, attr)
        if value is not None:
            out[key] = value
    if row.text is not None:
        out["text"] = row.text
    if row.color is not None:
        out["color"] = row.color
    if row.order is not None:
        out["order"] = row.order
    if row.group_id is not None:
        out["groupId"] = row.group_id

    if row.extra:
        try:
            extra = json.loads(row.extra)
            if isinstance(extra, dict):
                extra = dict(extra)
                # A labelId whose label was deleted before the FK existed. It
                # is restored to `labelId` so readers and exports see exactly
                # what the blob held; the FK cannot store it, but the wire
                # format is unchanged by that.
                orphaned = extra.pop("_orphanedLabelId", None)
                if orphaned is not None and "labelId" not in out:
                    out["labelId"] = orphaned
                # Unmodelled fields sit at the top level, which is where they
                # were before storage — `extra` is a storage detail, not part
                # of the wire format.
                out.update(extra)
        except (ValueError, TypeError):
            logger.warning("Annotation %s on task %s has unparseable extra",
                           row.id, row.task_id)

    return out


def rows_to_dicts(rows) -> list:
    """A task's rows as the annotation list the wire format uses."""
    return [row_to_dict(r) for r in rows]


# The columns `sync_task_annotations` compares and assigns. `id` and `task_id`
# are excluded: they identify the row rather than describe it.
_SYNC_COLUMNS = (
    "label_id", "type", "points", "x", "y", "width", "height",
    "text", "color", "order", "seq", "group_id", "extra",
)


def sync_task_annotations(db, task, incoming: list, known_label_ids=None) -> bool:
    """Make `task`'s annotation rows match `incoming`. Returns True if anything changed.

    **This is the change that removes the slowdown.** The blob path rewrote the
    task's entire annotation set on every save — 15.6 MB through Postgres for a
    one-shape edit, plus a second copy into the history table. Here, moving one
    shape writes one row: rows absent from the payload are deleted, rows whose
    columns are unchanged are left strictly alone (SQLAlchemy emits UPDATEs only
    for genuinely dirty rows), and only new ids are inserted.

    The caller commits. Nothing here flushes, so the whole save stays one
    transaction and a later failure rolls the annotations back with it.

    `incoming` is the already-parsed payload — parsing is the caller's job, and
    doing it here would reintroduce the duplicate parse that
    .devnotes/server-issue-diagnosis/evidence/07_REMAINING_COSTS.md measured at
    121 ms per save.
    """
    existing = {row.id: row for row in task.annotation_rows}

    seen: set = set()
    changed = False
    # Rows that are new to this session, applied as an upsert after the loop so
    # a concurrent save that inserted the same id first cannot 500 this one.
    pending_upserts: list = []

    for position, ann in enumerate(incoming):
        if not isinstance(ann, dict):
            continue
        # `seq` is the payload position, which is what reproduces the JSON
        # array's implicit order. Reassigned on every save so a reordered
        # payload reorders the rows.
        kwargs = dict_to_row_kwargs(ann, task.id, known_label_ids, seq=position)
        ident = kwargs["id"]
        # A payload that repeats an id would otherwise collide on the primary
        # key. Dropping the later copy is deliberate, and differs from minting
        # a fresh id for it: a minted id is not in the *next* payload either,
        # so every autosave would mint another one and the task would grow an
        # orphan row per save forever. The blob path kept both copies, but the
        # canvas cannot address two shapes by one id anyway, so the second was
        # already unreachable.
        if ident in seen:
            continue
        seen.add(ident)

        row = existing.get(ident)
        if row is None:
            # UPSERT, not INSERT.
            #
            # Two saves of the same task can overlap -- one browser tab writes
            # from the debounced autosave, the visibilitychange beacon and the
            # 30s timer drain, and on a large task a save takes long enough
            # that the next one starts before it finishes (task 713 measured
            # 36s and 39s saves overlapping in production). Both sessions load
            # the same rows, both see the new shape as absent, and both INSERT
            # it -- the second violating annotations_pkey and 500ing the save.
            #
            # The blob path could not hit this: a whole-column overwrite has no
            # per-row constraint to violate. Per-row storage introduced it, so
            # per-row storage has to answer for it.
            #
            # ON CONFLICT DO UPDATE is the fix rather than a pre-SELECT: the
            # check-then-insert race is exactly what fails here, and only the
            # database can settle it atomically. Last writer wins, which is the
            # same resolution the blob path had.
            pending_upserts.append(kwargs)
            changed = True
            continue

        # Assign only what actually differs. Assigning every column would mark
        # the row dirty even when nothing changed, and SQLAlchemy would then
        # UPDATE all of them — which is the whole cost this function exists to
        # avoid.
        for column in _SYNC_COLUMNS:
            value = kwargs[column]
            if getattr(row, column) != value:
                setattr(row, column, value)
                changed = True

    for ident, row in existing.items():
        if ident not in seen:
            # delete-orphan on the relationship would also catch this, but the
            # explicit delete keeps the intent visible and works whether or not
            # the collection has been loaded.
            task.annotation_rows.remove(row)
            db.delete(row)
            changed = True

    if pending_upserts:
        # Flush the deletes and updates first: an id can legitimately be
        # removed and re-added in one payload, and the upsert must land after
        # the delete, not race it.
        db.flush()
        # Both dialects spell ON CONFLICT the same way; the constructor differs.
        # Postgres is production, SQLite is dev and the test suite.
        maker = sqlite_insert if db.bind.dialect.name == "sqlite" else pg_insert
        # Chunked: one row binds 15 parameters, and SQLite caps a statement at
        # 999 (older builds) -- a first-save of a few thousand shapes blew
        # straight past it. 500 rows x 15 = 7,500 params is fine on Postgres
        # (limit 65,535) and is split further below for SQLite.
        chunk = 60 if db.bind.dialect.name == "sqlite" else 500
        for start in range(0, len(pending_upserts), chunk):
            batch = pending_upserts[start:start + chunk]
            stmt = maker(models.Annotation.__table__).values(batch)
            db.execute(stmt.on_conflict_do_update(
                index_elements=["id", "task_id"],
                set_={c: stmt.excluded[c] for c in _SYNC_COLUMNS},
            ))
        # The rows were written behind the ORM's back, so the collection it
        # holds is stale. Expire it rather than leaving the caller with a task
        # whose annotation_rows disagree with the database.
        db.expire(task, ["annotation_rows"])

    return changed


def sync_task_annotations_for_project(db, task, incoming: list) -> bool:
    """`sync_task_annotations`, having first looked up the project's label ids.

    The convenience wrapper the routers use. It is here rather than in a router
    so that both the save path and the import path share one definition and
    neither has to import the other.

    The label lookup is a few hundred short rows on one indexed column, scoped
    to the task's own project. It is needed because `annotations.label_id` has
    a foreign key the blob never had: annotations naming a since-deleted label
    exist in the real data, and without this filter each would raise
    IntegrityError and abort the write. The orphaned value is preserved in
    `extra` rather than dropped (06_PROGRESS.md D5).
    """
    known_label_ids = {
        row[0]
        for row in db.query(models.Label.id)
        .filter(models.Label.project_id == task.project_id)
        .all()
    }
    return sync_task_annotations(db, task, incoming, known_label_ids)

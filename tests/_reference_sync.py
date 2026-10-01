"""The ORM-based diff `sync_task_annotations` used before the Core row load.

Kept verbatim (renamed) as the reference implementation for
tests/test_core_row_sync.py: the Core version must leave the table in exactly the
state this one does, for any sequence of payloads. Not used by the application.
"""
import models
from formats.annotation_rows import (
    _SYNC_COLUMNS, _SYNC_SCALAR_COLUMNS, _json_or_none, dict_to_row_kwargs,
    points_equal, pg_insert, sqlite_insert,
)


def reference_sync_task_annotations(db, task, incoming: list, known_label_ids=None) -> bool:
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
        kwargs = dict_to_row_kwargs(
            ann, task.id, known_label_ids, seq=position, with_points=False
        )
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
            kwargs["points"] = _json_or_none(ann.get("points"))
            pending_upserts.append(kwargs)
            changed = True
            continue

        # Assign only what actually differs. Assigning every column would mark
        # the row dirty even when nothing changed, and SQLAlchemy would then
        # UPDATE all of them — which is the whole cost this function exists to
        # avoid.
        for column in _SYNC_SCALAR_COLUMNS:
            value = kwargs[column]
            if getattr(row, column) != value:
                setattr(row, column, value)
                changed = True

        # `points` last, and by value. This is the line the live profile
        # pointed at: re-serialising every polygon's vertices to compare text
        # was 38% of all server CPU, to learn that ~95% of shapes had not moved.
        # Only a shape whose points really differ is serialised (and then
        # compared as text once more, so a value that is unequal only because
        # of how it parses -- NaN, say -- is not rewritten on every save).
        incoming_points = ann.get("points")
        if not points_equal(row.points, incoming_points):
            new_points = _json_or_none(incoming_points)
            if new_points != row.points:
                row.points = new_points
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

    # A set emptied to zero rows must also empty the legacy blob.
    #
    # `annotation_dicts()` (formats/common.py) reads the rows, and falls back to
    # `Task.annotations` when a task has none -- the path that still serves
    # tasks the row conversion could not handle. The blob stopped being written
    # at the cutover, so on a task that predates it the blob still holds the
    # pre-cutover set. Deleting every shape removed the rows, the next read fell
    # through to that stale blob, and the old annotations reappeared on reload
    # and were saved straight back (dev task 1370, 2026-09-24). Partial deletes
    # were unaffected: any remaining row keeps the reader off the blob.
    #
    # Writing `[]` rather than NULL keeps the column's meaning as the rollback
    # copy: the task is genuinely empty, so its rollback copy is too.
    if not seen and task.annotations not in (None, "", "[]", "null"):
        task.annotations = "[]"
        changed = True

    return changed



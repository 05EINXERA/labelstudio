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


def dict_to_row_kwargs(ann: dict, task_id: int) -> dict:
    """The `Annotation(**kwargs)` for one annotation dict.

    `id` is minted when absent — four annotations in the real data have no id,
    and the save path already mints one for id-less incoming shapes, so this
    matches existing behaviour rather than introducing a new rule.
    """
    extra = {k: v for k, v in ann.items() if k not in MODELLED_KEYS and k != "extra"}
    # An `extra` already present in the payload (a round-tripped row) is merged
    # rather than nested, so a value cannot gain a level of wrapping each time
    # it passes through storage.
    nested = ann.get("extra")
    if isinstance(nested, dict):
        extra.update(nested)

    return {
        "id": str(ann.get("id") or uuid.uuid4()),
        "task_id": task_id,
        "label_id": ann.get("labelId"),
        "type": ann.get("type"),
        "points": _json_or_none(ann.get("points")),
        "x": _float_or_none(ann.get("x")),
        "y": _float_or_none(ann.get("y")),
        "width": _float_or_none(ann.get("width")),
        "height": _float_or_none(ann.get("height")),
        "text": ann.get("text"),
        "color": ann.get("color"),
        "order": _int_or_none(ann.get("order")),
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

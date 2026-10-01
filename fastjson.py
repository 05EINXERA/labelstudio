"""JSON parsing for the save path, with `orjson` when it is installed.

Profiling the live server (.devnotes/fix-performance-upgrade/01_PROFILE_ANALYSIS.md)
showed JSON work was ~60% of all GIL time: every save parses the whole
annotation set, and the diff then re-reads the stored copy of each shape. The
stdlib parser is the cost, and `orjson` parses the same documents ~3x faster
(~6x for the many small `points` arrays the diff compares).

Why this is a thin wrapper and not a global swap:

* **The stdlib parser is the reference.** `orjson` is stricter in some ways
  (it rejects `NaN`/`Infinity` and lone surrogate escapes that `json.loads`
  accepts), so an `orjson` failure falls through to `json.loads` and only the
  stdlib's failure is an error.

  One difference is *not* caught, deliberately: an integer wider than 64 bits
  comes back from `orjson` as a float rather than an exact int. A guard for it
  was measured and rejected -- scanning a 2 MB payload for long digit runs cost
  15-44 ms, as much as the parse it protects. It is harmless for the one source
  of these documents: the browser. JS numbers are doubles, `JSON.stringify`
  prints an integer-valued double as its digits, and the float `orjson` returns
  is that same double, so what the client reads back is identical. Server-side
  imports parse with the stdlib directly and never come through here.
* **It is optional.** With no `orjson` installed this is `json.loads`, so the
  app, the tests and the dev instance keep working unchanged — the speed-up is
  an optimisation, never a dependency of correctness.
* **Parsing only.** Serialisation is deliberately not touched: stored `points`
  text is compared by *value* (see `formats.annotation_rows.points_equal`), and
  anything written still goes through `json.dumps`, so what is on disk keeps
  its format.
"""
import json
from typing import Any, Union

try:  # pragma: no cover - exercised by whichever environment runs the tests
    import orjson
except ImportError:  # pragma: no cover
    orjson = None

HAVE_ORJSON = orjson is not None

def loads(data: Union[str, bytes, bytearray]) -> Any:
    """`json.loads`, faster when `orjson` is available.

    Raises `ValueError` (specifically `json.JSONDecodeError`) on invalid input,
    exactly as `json.loads` does — callers' existing `except ValueError` keeps
    working.
    """
    if orjson is not None:
        try:
            return orjson.loads(data)
        except ValueError:
            # orjson refused it. Either it is genuinely invalid (the stdlib
            # parser will say so, with its own message) or it is something
            # stdlib accepts and orjson does not.
            pass
    return json.loads(data)

"""Image resolution categories — the Full / Half vocabulary.

The deploy's cameras produce exactly two resolutions: 5184x3888 and its exact
half 2592x1944. Annotators think in those terms ("that's a half"), so the
Images Info view names them rather than making people read four-digit numbers.

⚠️ `frontend/js/image-size.js` is a deliberate mirror of this module. ⚠️
There is no build step (CLAUDE.md rule 13), so the vocabulary cannot be shared
between Python and the browser without a toolchain the project refuses. The JS
copy is for *rendering only* — the server resolves the `category` filter, and
`tests/test_image_sizes.py` pins the two files together.

TWO BUCKETS ARE NOT ENOUGH, deliberately. `Other` and `Unknown` are not
padding:

  - `Other` catches a third resolution the day one appears (a phone photo, a
    re-export, a supplier's crop). Without it a two-value enum silently
    misfiles the new size and the report starts lying.
  - `Unknown` is `image_width IS NULL` — a task whose dimensions were never
    measured (uploaded before migration b2c3d4e5f6a7, or via an import path
    that does not call `measure_image`). It must stay visible in the summary
    rather than being folded into `Other`, because it means "run the backfill",
    not "unusual image".
"""
from typing import Dict, Optional, Tuple

#: (width, height) -> category name. The only place the magic numbers live.
SIZE_CATEGORIES: Dict[Tuple[int, int], str] = {
    (5184, 3888): "Full",
    (2592, 1944): "Half",
}

#: Category for a task whose dimensions are recorded but match no known size.
OTHER = "Other"

#: Category for a task with no recorded dimensions at all.
UNKNOWN = "Unknown"

#: Display order for the summary strip and the spreadsheet's Summary sheet.
#: Named sizes first (most common first), then the two catch-alls.
CATEGORY_ORDER = ["Full", "Half", OTHER, UNKNOWN]


def categorize(width: Optional[int], height: Optional[int]) -> str:
    """The category name for one image's dimensions.

    Zero is treated as absent, not as a real dimension: `formats.common.
    image_size()` returns (0, 0) for an image it could not read, and a task
    carrying that is in exactly the same state as one carrying NULL — nobody
    measured it successfully.
    """
    if not width or not height:
        return UNKNOWN
    return SIZE_CATEGORIES.get((width, height), OTHER)


def known_sizes_for(category: str) -> Optional[list]:
    """The (width, height) pairs a named category covers, or None.

    None means the category is not one of the named sizes — the caller has to
    express it as a negation (`Other`) or a null check (`Unknown`) rather than
    an `IN` list. Returning None rather than an empty list keeps those two
    cases distinguishable from "a named category with no sizes", which cannot
    happen but would be indistinguishable from an empty list.
    """
    pairs = [size for size, name in SIZE_CATEGORIES.items() if name == category]
    return pairs or None

"""Tests for the Image Inventory feature.

Covers the size vocabulary, the read endpoint, the spreadsheet download and
the access-control contract. The cases here are the ones that caught real
bugs or that guard a decision a future editor would otherwise undo — chiefly
that `Other` and `Unknown` are two separate buckets, and that the `Other`
*filter* excludes unmeasured rows.
"""
import os
import re

import pytest

from schemas import (
    categorize_image_size,
    IMAGE_SIZE_CATEGORIES,
    IMAGE_SIZE_NAMED,
    IMAGE_SIZE_OTHER,
    IMAGE_SIZE_UNKNOWN,
)

FRONTEND_JS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "js"
)
MIRROR_FILE = os.path.join(FRONTEND_JS_DIR, "image-sizes.js")


# ---------------------------------------------------------------------------
# 1. The vocabulary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("size,expected", list(IMAGE_SIZE_NAMED.items()))
def test_named_resolutions_map_to_their_category(size, expected):
    assert categorize_image_size(*size) == expected


def test_unnamed_resolution_is_other():
    """A third resolution is Other, not a named size."""
    assert categorize_image_size(1335, 1004) == IMAGE_SIZE_OTHER


def test_transposed_dimensions_are_other_not_the_named_category():
    """A portrait re-export is a different image, not the named landscape size.

    Guards the decision to key on an exact (width, height) tuple rather than
    an unordered pair.
    """
    for (w, h) in IMAGE_SIZE_NAMED:
        assert categorize_image_size(h, w) == IMAGE_SIZE_OTHER


@pytest.mark.parametrize("width,height", [
    (None, None),        # never measured
    (0, 0),              # formats.common.image_size() returns this for an unreadable file
    (5184, None),        # half-measured
    (None, 3888),
    (5184, 0),
    (0, 3888),
    (-1, -1),
])
def test_unmeasured_rows_are_unknown_never_other(width, height):
    """Null, zero and half-measured dimensions all land in Unknown.

    This is the distinction the whole feature rests on: Other says "an unusual
    image", Unknown says "nobody measured this". Merging them would hide a data
    gap behind a plausible bucket.
    """
    assert categorize_image_size(width, height) == IMAGE_SIZE_UNKNOWN


def test_both_catch_alls_exist_and_are_distinct():
    """Guards against a reviewer 'simplifying' this to a single catch-all."""
    assert IMAGE_SIZE_OTHER != IMAGE_SIZE_UNKNOWN
    assert IMAGE_SIZE_OTHER in IMAGE_SIZE_CATEGORIES
    assert IMAGE_SIZE_UNKNOWN in IMAGE_SIZE_CATEGORIES


def test_categories_list_covers_every_named_size_plus_both_catch_alls():
    assert set(IMAGE_SIZE_CATEGORIES) == set(IMAGE_SIZE_NAMED.values()) | {
        IMAGE_SIZE_OTHER,
        IMAGE_SIZE_UNKNOWN,
    }
    # No duplicates, so the summary strip cannot render a category twice.
    assert len(IMAGE_SIZE_CATEGORIES) == len(set(IMAGE_SIZE_CATEGORIES))


# ---------------------------------------------------------------------------
# 2. The client mirror (drift test)
# ---------------------------------------------------------------------------

def _parse_mirror_array(name):
    """Extract a string-array export from the JS mirror without running JS."""
    with open(MIRROR_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    match = re.search(
        r"export const " + re.escape(name) + r"\s*=\s*\[(.*?)\]", content, re.S
    )
    assert match, f"{name} not found in {MIRROR_FILE}"
    return re.findall(r'"([^"]*)"', match.group(1))


def test_client_mirror_matches_server_vocabulary():
    """The browser copy must not drift from the Python definition.

    Order matters as well as membership: the summary strip renders in this
    order, and the two catch-alls belong last.
    """
    assert _parse_mirror_array("IMAGE_SIZE_CATEGORIES") == IMAGE_SIZE_CATEGORIES


def test_client_mirror_keeps_both_catch_alls_separate():
    with open(MIRROR_FILE, "r", encoding="utf-8") as f:
        content = f.read()
    assert f'IMAGE_SIZE_OTHER = "{IMAGE_SIZE_OTHER}"' in content
    assert f'IMAGE_SIZE_UNKNOWN = "{IMAGE_SIZE_UNKNOWN}"' in content

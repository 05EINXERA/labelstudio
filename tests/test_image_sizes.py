"""The image-resolution vocabulary, and its two copies.

`formats/image_sizes.py` names the deploy's two camera resolutions; the browser
needs the same names to draw pills and fill the category select, and with no
build step it gets them from a hand-maintained mirror. A drift there does not
crash anything — it silently relabels images in a report whose whole purpose is
to be trusted about sizes — so the two files are pinned together here.
"""
import re
from pathlib import Path

from formats.image_sizes import (
    CATEGORY_ORDER,
    OTHER,
    SIZE_CATEGORIES,
    UNKNOWN,
    categorize,
    known_sizes_for,
)

MIRROR = Path(__file__).parent.parent / "frontend" / "js" / "image-size.js"


# --- the server-side vocabulary ---------------------------------------------

def test_known_sizes_are_pinned_to_literals():
    """Pinned so adding a third resolution is a deliberate act, reviewed
    against the real data rather than added because one image turned up."""
    assert SIZE_CATEGORIES == {
        (5184, 3888): "Full",
        (2592, 1944): "Half",
    }


def test_half_is_exactly_half_of_full():
    """The names are only honest while this holds. If a future 'Half' is not
    half, it needs a different name, not a different constant."""
    (fw, fh), = [s for s, n in SIZE_CATEGORIES.items() if n == "Full"]
    (hw, hh), = [s for s, n in SIZE_CATEGORIES.items() if n == "Half"]
    assert (fw / 2, fh / 2) == (hw, hh)


def test_categorize_known_sizes():
    assert categorize(5184, 3888) == "Full"
    assert categorize(2592, 1944) == "Half"


def test_categorize_unknown_size_is_other_not_a_crash():
    """A third resolution must land somewhere visible rather than raising or
    being silently misfiled as one of the two named sizes."""
    assert categorize(4032, 3024) == OTHER
    assert categorize(1, 1) == OTHER
    # Transposed dimensions are a different image, not the same category.
    assert categorize(3888, 5184) == OTHER


def test_missing_dimensions_are_unknown_not_other():
    """Unknown means 'never measured' and is the signal to run the backfill.
    Folding it into Other would hide that behind a plausible-looking bucket."""
    assert categorize(None, None) == UNKNOWN
    assert categorize(0, 0) == UNKNOWN
    # Zero is what formats.common.image_size() returns for an unreadable file,
    # and a half-measured row is no more measured than an unmeasured one.
    assert categorize(5184, 0) == UNKNOWN
    assert categorize(None, 3888) == UNKNOWN


def test_category_order_covers_every_category_exactly_once():
    """The summary strip and the spreadsheet's Summary sheet both render from
    this list; a category missing from it would never be displayed."""
    produced = set(SIZE_CATEGORIES.values()) | {OTHER, UNKNOWN}
    assert set(CATEGORY_ORDER) == produced
    assert len(CATEGORY_ORDER) == len(set(CATEGORY_ORDER))


def test_known_sizes_for():
    assert known_sizes_for("Full") == [(5184, 3888)]
    assert known_sizes_for("Half") == [(2592, 1944)]
    # None, not [], so the caller must handle these two as a negation and a
    # null check rather than building an `IN ()` that matches nothing.
    assert known_sizes_for(OTHER) is None
    assert known_sizes_for(UNKNOWN) is None
    assert known_sizes_for("nonsense") is None


# --- the client mirror -------------------------------------------------------

def _js_object(name: str) -> dict:
    """Read an `export const NAME = { "k": "v", ... }` map out of the mirror."""
    source = MIRROR.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \{{(.*?)\}};", source, re.S)
    assert match, f"{name} not found in {MIRROR}"
    return dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', match.group(1)))


def _js_array(name: str) -> list:
    """Read an `export const NAME = [...]` array out of the mirror.

    Entries may be string literals or references to the OTHER/UNKNOWN consts,
    which are resolved so the list compares equal to the Python one.
    """
    source = MIRROR.read_text(encoding="utf-8")
    match = re.search(rf"export const {name} = \[(.*?)\];", source, re.S)
    assert match, f"{name} not found in {MIRROR}"
    consts = {"OTHER": OTHER, "UNKNOWN": UNKNOWN}
    out = []
    for raw in match.group(1).split(","):
        token = raw.strip()
        if not token:
            continue
        if token.startswith('"'):
            out.append(token.strip('"'))
        else:
            assert token in consts, f"unresolvable entry {token!r} in {name}"
            out.append(consts[token])
    return out


def test_client_mirror_knows_the_same_sizes():
    """Compares the actual client file against the actual server constant, so
    the pair cannot drift by both being edited to a new but different set."""
    assert _js_object("SIZE_CATEGORIES") == {
        f"{w}x{h}": name for (w, h), name in SIZE_CATEGORIES.items()
    }


def test_client_mirror_orders_categories_the_same_way():
    assert _js_array("CATEGORY_ORDER") == CATEGORY_ORDER


def test_client_mirror_uses_the_same_catch_all_names():
    """A different spelling of 'Unknown' on the client would send a category
    filter value the server does not recognise."""
    source = MIRROR.read_text(encoding="utf-8")
    assert f'export const OTHER = "{OTHER}";' in source
    assert f'export const UNKNOWN = "{UNKNOWN}";' in source

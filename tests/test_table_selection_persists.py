"""Selection survives a server-side refetch in the paged task table.

Background: an owner selecting several tasks, then typing in the search box to
find more, lost the original selection -- the searched-for row ended up as the
only one selected. The cause was `setServerData` in data-table.js pruning the
selected set down to the rows present in the freshly-fetched page. In
server-paged mode that page is a *slice* of the results, so a selected row
scrolling out of view via search or pagination looked identical to a row that
had been deleted.

These are source-level assertions, the convention already used by
test_frontend_modules.py: the table is browser code and is not reachable from
pytest, so what is pinned here is the shape of the fix, not its runtime
behaviour. The selection-across-search gesture still needs a manual check in
the app.
"""

import os
import re

import pytest

FRONTEND_JS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "frontend", "js"
)
DATA_TABLE = os.path.join(FRONTEND_JS_DIR, "components", "data-table.js")
TASKS_PAGE = os.path.join(FRONTEND_JS_DIR, "pages", "project", "tasks.js")


def _read(path):
    assert os.path.isfile(path), f"Expected file not found: {path}"
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _body_of(source, member):
    """Return the source of an object-literal method, e.g. `setServerData`.

    Brace-matches from the member's opening `{` so the extracted body stops at
    the method's own end rather than at the first `}` in a nested block.
    """
    start = source.index(f"{member}(")
    open_brace = source.index("{", start)
    depth = 0
    for index in range(open_brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[open_brace : index + 1]
    raise AssertionError(f"Unbalanced braces while reading {member}")


def test_set_server_data_does_not_prune_selection():
    """The regression itself: a server-paged refetch must not drop selections.

    `setRows` (client-paged) legitimately still prunes, because there the rows
    it is handed *are* the whole dataset -- so this asserts against the
    server-paged method specifically rather than the file as a whole.
    """
    body = _body_of(_read(DATA_TABLE), "setServerData")

    assert "state.selected" not in body, (
        "setServerData must not touch state.selected: pruning it to the fetched "
        "page is what made a search wipe the owner's existing selection."
    )
    assert "state.rows" in body and "state.totalRows" in body, (
        "setServerData should still install the fetched rows and total."
    )


def test_set_rows_still_prunes_for_client_paged_tables():
    """Client-paged callers keep the stale-id guard.

    `setRows` receives the complete dataset, so an id missing from it really is
    gone -- dropping the guard there would let a deleted row stay selected.
    """
    body = _body_of(_read(DATA_TABLE), "setRows")

    assert "state.selected.delete" in body, (
        "setRows must keep pruning: unlike setServerData its rows are the whole "
        "dataset, so an absent id means deleted rather than off-page."
    )


def test_select_all_checkbox_is_scoped_to_the_visible_slice():
    """Select-all must stay a page-level control once selections outlive a page.

    With off-page selections retained, a select-all that reasoned over the whole
    selected set would render as checked on a page whose rows are not selected.
    """
    source = _read(DATA_TABLE)

    assert re.search(
        r"all\.checked\s*=\s*slice\.length\s*>\s*0\s*&&\s*slice\.every", source
    ), "The select-all checkbox's checked state should be derived from `slice`."


def test_bulk_bar_reports_selected_rows_that_are_off_screen():
    """The count must not silently contradict the checkboxes on screen.

    Retaining off-page selections means "3 selected" can appear above a table
    with nothing ticked; the count names the hidden remainder so the number
    stays verifiable.
    """
    source = _read(TASKS_PAGE)

    assert "not shown" in source, (
        "updateBulkBar should distinguish selected-but-hidden rows, otherwise "
        "the count contradicts the visible checkboxes after a search."
    )
    assert "bulkClearBtn" in source, (
        "The bulk bar needs an explicit Clear selection control: filtering rows "
        "out of view no longer abandons the selection."
    )


def test_bulk_bar_is_refreshed_after_a_refetch():
    """A refetch changes the hidden count without any checkbox being touched.

    `onSelectionChange` only fires on checkbox input, so without this call the
    "(n not shown)" figure would describe the previous page.
    """
    source = _read(TASKS_PAGE)
    fetch_start = source.index("table.setServerData(data.items, data.total)")
    window = source[fetch_start : fetch_start + 400]

    assert "updateBulkBar" in window, (
        "updateBulkBar should be called after setServerData installs a new page, "
        "so the off-screen count reflects the rows now displayed."
    )


def test_bulk_actions_clear_the_selection_on_success():
    """The guard that replaces pruning.

    Pruning incidentally stopped a moved/deleted id being re-submitted. That
    now rests on each bulk action clearing the selection once the server has
    accepted it, so this pins all three call sites.
    """
    source = _read(TASKS_PAGE)

    assert source.count("table.clearSelection()") >= 3, (
        "Bulk move, delete and assign must each clear the selection on success "
        "-- with setServerData no longer pruning, this is what keeps a stale id "
        "out of the next bulk action."
    )


@pytest.mark.parametrize(
    "importer",
    [
        os.path.join(FRONTEND_JS_DIR, "pages", "project", "tasks.js"),
        os.path.join(FRONTEND_JS_DIR, "pages", "project", "classes.js"),
        os.path.join(FRONTEND_JS_DIR, "pages", "project", "images.js"),
        os.path.join(FRONTEND_JS_DIR, "pages", "projects-list.js"),
        os.path.join(FRONTEND_JS_DIR, "pages", "teams.js"),
    ],
)
def test_every_data_table_importer_agrees_on_the_version_pin(importer):
    """Module pins are cache keys; a partial bump ships a mixed set of modules.

    Every importer of data-table.js must request the same version, or some
    pages keep serving the pruning build from cache.
    """
    pins = re.findall(r"data-table\.js\?v=(\d+)", _read(importer))

    assert pins, f"{os.path.basename(importer)} should import data-table.js with a ?v= pin"
    assert all(pin == "3" for pin in pins), (
        f"{os.path.basename(importer)} pins data-table.js at v={pins[0]}, expected v=3; "
        "all importers must be bumped together."
    )

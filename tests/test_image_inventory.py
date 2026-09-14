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

import models
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


# ---------------------------------------------------------------------------
# 3. The read endpoint
# ---------------------------------------------------------------------------

def _new_project(client, auth, name="inv-proj"):
    res = client.post(
        "/api/projects", json={"name": name, "slug": name, "creator": "ignored"}, headers=auth
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _member(client, auth, name):
    """Ensure a TeamMember row exists; get_current_annotator returns None without one."""
    client.post("/api/team/ping", headers={**auth, "X-Annotator-Name": name})
    return name


def _seed(project_id, rows):
    """Insert (description, width, height[, image_path[, status]]) rows directly.

    Written straight to the DB rather than through the upload endpoint so a
    test can create dimension states -- NULL, zero, half-measured -- that the
    upload path deliberately never produces.
    """
    import models
    from database import SessionLocal
    ids = []
    with SessionLocal() as db:
        for row in rows:
            description, width, height = row[0], row[1], row[2]
            image_path = row[3] if len(row) > 3 else None
            status = row[4] if len(row) > 4 else "New"
            task = models.Task(
                project_id=project_id, description=description, status=status,
                image_path=image_path, image_width=width, image_height=height,
            )
            db.add(task)
            db.flush()
            ids.append(task.id)
        db.commit()
    return ids


def _inventory(client, auth, pid, **params):
    return client.get(f"/api/projects/{pid}/image-inventory", params=params, headers=auth)


FULL = (5184, 3888)
HALF = (2592, 1944)


def test_inventory_lists_rows_with_categories(client, alice):
    pid = _new_project(client, alice)
    _seed(pid, [
        ("full.jpg", 5184, 3888),
        ("half.jpg", 2592, 1944),
        ("odd.jpg", 1335, 1004),
        ("unmeasured.jpg", None, None),
    ])
    res = _inventory(client, alice, pid)
    assert res.status_code == 200, res.text
    body = res.json()
    by_name = {r["filename"]: r for r in body["items"]}
    assert by_name["full.jpg"]["category"] == "Full"
    assert by_name["half.jpg"]["category"] == "Half"
    assert by_name["odd.jpg"]["category"] == IMAGE_SIZE_OTHER
    assert by_name["unmeasured.jpg"]["category"] == IMAGE_SIZE_UNKNOWN
    assert body["total"] == 4


def test_other_filter_excludes_unmeasured_rows(client, alice):
    """The naive "not in the known sizes" negation sweeps up NULL rows.

    This is the single most important filter test: written without an explicit
    has-dimensions condition, `Other` silently absorbs every unmeasured row and
    the Unknown bucket reads as empty -- which is exactly the under-reporting
    the two catch-alls exist to prevent.
    """
    pid = _new_project(client, alice)
    _seed(pid, [
        ("full.jpg", 5184, 3888),
        ("odd.jpg", 1335, 1004),
        ("null.jpg", None, None),
        ("zero.jpg", 0, 0),
        ("half-measured.jpg", 5184, None),
    ])
    res = _inventory(client, alice, pid, category=IMAGE_SIZE_OTHER)
    assert res.status_code == 200, res.text
    names = sorted(r["filename"] for r in res.json()["items"])
    assert names == ["odd.jpg"], f"Other must contain only measured oddities, got {names}"


def test_unknown_filter_catches_null_zero_and_half_measured(client, alice):
    pid = _new_project(client, alice)
    _seed(pid, [
        ("full.jpg", 5184, 3888),
        ("null.jpg", None, None),
        ("zero.jpg", 0, 0),
        ("half-measured.jpg", 5184, None),
    ])
    res = _inventory(client, alice, pid, category=IMAGE_SIZE_UNKNOWN)
    assert res.status_code == 200, res.text
    names = sorted(r["filename"] for r in res.json()["items"])
    assert names == ["half-measured.jpg", "null.jpg", "zero.jpg"]


@pytest.mark.parametrize("category,expected", [
    ("Full", ["full.jpg"]),
    ("Half", ["half.jpg"]),
])
def test_named_category_filters(client, alice, category, expected):
    pid = _new_project(client, alice)
    _seed(pid, [("full.jpg", 5184, 3888), ("half.jpg", 2592, 1944), ("null.jpg", None, None)])
    res = _inventory(client, alice, pid, category=category)
    assert sorted(r["filename"] for r in res.json()["items"]) == expected


def test_unrecognised_category_is_rejected_with_a_clear_message(client, alice):
    pid = _new_project(client, alice)
    res = _inventory(client, alice, pid, category="Enormous")
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert "Enormous" in detail and "Full" in detail


def test_search_and_category_filters_compose(client, alice):
    pid = _new_project(client, alice)
    _seed(pid, [
        ("north-full.jpg", 5184, 3888),
        ("north-half.jpg", 2592, 1944),
        ("south-full.jpg", 5184, 3888),
    ])
    res = _inventory(client, alice, pid, search="north", category="Full")
    assert sorted(r["filename"] for r in res.json()["items"]) == ["north-full.jpg"]
    assert res.json()["total"] == 1


def test_summary_describes_the_filtered_set_not_the_page(client, alice):
    """The whole point of the summary: it must not change as the user pages."""
    pid = _new_project(client, alice)
    _seed(pid, [(f"f{i}.jpg", 5184, 3888) for i in range(10)]
              + [(f"h{i}.jpg", 2592, 1944) for i in range(5)])

    res = _inventory(client, alice, pid, limit=3)
    body = res.json()
    assert len(body["items"]) == 3, "page should hold 3 rows"
    counts = {c["category"]: c["count"] for c in body["summary"]["categories"]}
    assert counts["Full"] == 10
    assert counts["Half"] == 5
    assert body["summary"]["total"] == 15


def test_summary_lists_every_category_including_zeroes(client, alice):
    """A stable shape as filters change -- and Unknown always visible."""
    pid = _new_project(client, alice)
    _seed(pid, [("full.jpg", 5184, 3888)])
    body = _inventory(client, alice, pid).json()
    listed = [c["category"] for c in body["summary"]["categories"]]
    assert listed == IMAGE_SIZE_CATEGORIES
    counts = {c["category"]: c["count"] for c in body["summary"]["categories"]}
    assert counts[IMAGE_SIZE_UNKNOWN] == 0
    assert counts[IMAGE_SIZE_OTHER] == 0


def test_empty_project_returns_zeroed_summary(client, alice):
    """Five projects in production have no tasks at all."""
    pid = _new_project(client, alice)
    body = _inventory(client, alice, pid).json()
    assert body["items"] == []
    assert body["total"] == 0
    assert [c["count"] for c in body["summary"]["categories"]] == [0] * len(IMAGE_SIZE_CATEGORIES)


def test_missing_file_reports_null_size_not_zero(client, alice):
    """Zero is a plausible-looking wrong number; None renders as a dash."""
    pid = _new_project(client, alice)
    _seed(pid, [("gone.jpg", 5184, 3888, "uploads/definitely-not-there.jpg")])
    body = _inventory(client, alice, pid).json()
    assert body["items"][0]["file_size"] is None
    assert body["summary"]["missing_files"] == 1


def test_present_file_reports_its_real_size(client, alice):
    import config
    pid = _new_project(client, alice)
    uploads = os.path.join(config.DATA_DIR, "uploads")
    os.makedirs(uploads, exist_ok=True)
    name = "inventory-present.bin"
    with open(os.path.join(uploads, name), "wb") as f:
        f.write(b"x" * 1234)
    _seed(pid, [("present.jpg", 5184, 3888, f"uploads/{name}")])
    body = _inventory(client, alice, pid).json()
    assert body["items"][0]["file_size"] == 1234
    assert body["summary"]["missing_files"] == 0
    assert body["summary"]["total_size"] == 1234
    assert body["summary"]["total_size_is_complete"] is True


def test_paging_is_stable_when_every_row_ties_on_the_sort_column(client, alice):
    """Without a tiebreak the database may return ties in any order, so pages
    can repeat and omit rows between requests.

    Caveat worth knowing before trusting this test: SQLite (what the suite
    runs on) happens to return ties in rowid order, so this passes even with
    the ORDER BY tiebreak removed. The bug it guards is a Postgres one -- the
    deployment database is free to reorder ties between queries, especially
    once a plan switches to a parallel or bitmap scan. Treat this as a
    regression guard on the intended ordering, not as proof of stability.
    """
    pid = _new_project(client, alice)
    _seed(pid, [(f"tie-{i:02d}.jpg", 5184, 3888) for i in range(20)])

    seen = []
    for offset in range(0, 20, 5):
        body = _inventory(client, alice, pid, sort_by="width", limit=5, offset=offset).json()
        seen.extend(r["filename"] for r in body["items"])

    assert len(seen) == 20
    assert len(set(seen)) == 20, "a row was repeated or omitted across pages"


def test_unknown_sort_key_falls_back_instead_of_erroring(client, alice):
    """A stale bookmark carrying an old sort key should render, not 422."""
    pid = _new_project(client, alice)
    _seed(pid, [("b.jpg", 5184, 3888), ("a.jpg", 2592, 1944)])
    res = _inventory(client, alice, pid, sort_by="annotations_legacy")
    assert res.status_code == 200
    assert [r["filename"] for r in res.json()["items"]] == ["a.jpg", "b.jpg"]


def test_page_size_is_capped(client, alice):
    pid = _new_project(client, alice)
    res = _inventory(client, alice, pid, limit=50000)
    assert res.status_code == 422


def test_rows_from_other_projects_never_appear(client, alice):
    mine = _new_project(client, alice, "mine")
    other = _new_project(client, alice, "other")
    _seed(mine, [("mine.jpg", 5184, 3888)])
    _seed(other, [("theirs.jpg", 5184, 3888)])
    body = _inventory(client, alice, mine).json()
    assert [r["filename"] for r in body["items"]] == ["mine.jpg"]
    assert body["total"] == 1


def test_read_does_not_write_including_rows_it_could_have_measured(client, alice):
    """A GET must not repair data as a side effect.

    Beyond the rule against writing GETs, a self-altering report would make the
    Unknown count depend on which pages somebody happened to browse.
    """
    import config
    import models
    from database import SessionLocal
    from PIL import Image

    pid = _new_project(client, alice)
    uploads = os.path.join(config.DATA_DIR, "uploads")
    os.makedirs(uploads, exist_ok=True)
    # A real, measurable PNG whose task row deliberately carries no dimensions.
    name = "inventory-measurable.png"
    Image.new("RGB", (64, 48)).save(os.path.join(uploads, name))
    _seed(pid, [("measurable.png", None, None, f"uploads/{name}")])

    body = _inventory(client, alice, pid).json()
    assert body["items"][0]["category"] == IMAGE_SIZE_UNKNOWN

    with SessionLocal() as db:
        row = db.query(models.Task).filter(models.Task.project_id == pid).first()
        assert row.image_width is None, "the read measured and saved a row"
        assert row.image_height is None


# ---------------------------------------------------------------------------
# 4. Access control -- owner or appointed reviewer, uniform across endpoints
# ---------------------------------------------------------------------------

def test_owner_can_read_inventory(client, alice):
    pid = _new_project(client, alice)
    assert _inventory(client, alice, pid).status_code == 200


def test_appointed_reviewer_can_read_inventory(client, alice, bob):
    """A reviewer must be a separate account: is_project_creator checks
    owner_id unconditionally, so alice's token is always the owner."""
    pid = _new_project(client, alice)
    _member(client, bob, "Rev")
    assert client.post(
        f"/api/projects/{pid}/reviewers", json={"member_name": "Rev"}, headers=alice
    ).status_code == 200

    res = client.get(
        f"/api/projects/{pid}/image-inventory",
        headers={**bob, "X-Annotator-Name": "Rev"},
    )
    assert res.status_code == 200, res.text


def test_unrelated_user_gets_404_not_403(client, alice, bob):
    """404 so project ids cannot be enumerated."""
    pid = _new_project(client, alice)
    assert _inventory(client, bob, pid).status_code == 404


def test_annotator_with_project_access_but_no_role_is_forbidden(client, alice, bob):
    """Can see the project, but not aggregate it into a downloadable report."""
    pid = _new_project(client, alice)
    _member(client, bob, "Ann")
    ids = _seed(pid, [("a.jpg", 5184, 3888)])
    # Assign bob a task, which grants project access via get_owned_project.
    client.patch(f"/api/tasks/{ids[0]}?projectId={pid}", json={"assignee": "Ann"}, headers=alice)

    res = client.get(
        f"/api/projects/{pid}/image-inventory",
        headers={**bob, "X-Annotator-Name": "Ann"},
    )
    assert res.status_code == 403, res.text
    assert "reviewer" in res.json()["detail"]


def test_inventory_requires_authentication(client, alice):
    pid = _new_project(client, alice)
    res = client.get(f"/api/projects/{pid}/image-inventory")
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# 5. The performance rules (structural, not timing-based)
# ---------------------------------------------------------------------------

# tasks.annotations_legacy is a Text column holding up to 3.45 MB per row
# (avg 561 KB across 55 non-null rows in production, 30.9 MB total). Loading
# full Task entities would drag it through the driver for every row of every
# page. These tests assert the generated SQL never names it, so the endpoint is
# structurally immune rather than accidentally fast -- a timing assertion would
# be flaky and would not say *why* it got slow.
LARGE_COLUMN = "annotations_legacy"


def test_page_query_does_not_select_the_large_column():
    from database import SessionLocal
    from app import image_inventory

    with SessionLocal() as db:
        query = image_inventory.ordered(
            image_inventory.base_query(db, 1), "filename", False
        )
        sql = str(
            query.with_entities(
                models.Task.id,
                models.Task.description,
                models.Task.image_width,
                models.Task.image_height,
                models.Task.status,
            )
        )
    assert LARGE_COLUMN not in sql, f"narrow projection lost; SQL was:\n{sql}"


def test_base_query_is_narrow_even_before_projection():
    """Guards the next caller, not this one.

    Every consumer re-projects today, so a wide base_query would not show up
    as a bug -- until somebody adds a caller that executes it directly.
    """
    from database import SessionLocal
    from app import image_inventory

    with SessionLocal() as db:
        sql = str(image_inventory.base_query(db, 1))
    assert LARGE_COLUMN not in sql, f"base_query selects the large column:\n{sql}"


def test_summary_and_size_queries_do_not_select_the_large_column():
    from database import SessionLocal
    from app import image_inventory
    from sqlalchemy import func

    with SessionLocal() as db:
        base = image_inventory.base_query(db, 1)
        summary_sql = str(
            base.with_entities(
                models.Task.image_width, models.Task.image_height,
                func.count(models.Task.id),
            ).group_by(models.Task.image_width, models.Task.image_height)
        )
        size_sql = str(base.with_entities(models.Task.id, models.Task.image_path))
    assert LARGE_COLUMN not in summary_sql
    assert LARGE_COLUMN not in size_sql


def test_sort_keys_are_whitelisted_not_attribute_lookups():
    """A query-string value must never reach ORDER BY by attribute lookup.

    The task list endpoint next door still does `getattr(models.Task, sort_by)`,
    which lets a caller order by any column on the model. New code uses an
    explicit map; this pins that choice.
    """
    from app import image_inventory

    assert LARGE_COLUMN not in image_inventory.SORT_COLUMNS
    assert image_inventory.DEFAULT_SORT in image_inventory.SORT_COLUMNS
    for key, column in image_inventory.SORT_COLUMNS.items():
        assert column is not None, key


def test_size_total_is_flagged_partial_above_the_bound(client, alice, monkeypatch):
    """A partial total must never be presented silently as a complete one."""
    from app import image_inventory as ii

    pid = _new_project(client, alice)
    _seed(pid, [(f"f{i}.jpg", 5184, 3888) for i in range(6)])

    # Force the threshold below the row count rather than seeding thousands of
    # rows: the behaviour under test is the flag, not the bound's value.
    monkeypatch.setattr(
        "api.routers.projects.IMAGE_INVENTORY_FULL_SIZE_MAX_ROWS", 3
    )
    body = _inventory(client, alice, pid, limit=2).json()
    assert body["summary"]["total"] == 6
    assert body["summary"]["total_size_is_complete"] is False


def test_size_total_is_complete_below_the_bound(client, alice):
    pid = _new_project(client, alice)
    _seed(pid, [(f"f{i}.jpg", 5184, 3888) for i in range(6)])
    body = _inventory(client, alice, pid, limit=2).json()
    assert body["summary"]["total_size_is_complete"] is True


# ---------------------------------------------------------------------------
# 6. Frontend wiring (nav gating, route registration, version pins)
# ---------------------------------------------------------------------------

def _read(*parts):
    path = os.path.join(os.path.dirname(FRONTEND_JS_DIR), *parts)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def test_nav_item_is_gated_to_owner_or_reviewer():
    """Rendering only, but it must agree with the endpoint's minimum."""
    nav = _read("js", "components", "project-nav.js")
    assert '"images"' in nav or "route: \"images\"" in nav
    # The gate names both flags the API accepts, and no others.
    match = re.search(r'route:\s*"images".*?\}', nav, re.S)
    assert match, "images nav item not found"
    item = match.group(0)
    assert "is_owner" in item and "is_reviewer" in item


def test_route_resolution_is_gated_by_the_same_list_as_the_nav():
    """Hiding a nav link does nothing about a typed or bookmarked URL."""
    router = _read("js", "pages", "project", "router.js")
    assert "visibleNavItems" in router, "router must derive permitted routes from the nav list"
    assert "permittedRoutes" in router
    # routeFromHash must consult the gated set, not the ungated VALID_ROUTES.
    match = re.search(r"function routeFromHash\(\).*?\}", router, re.S)
    assert match and "permittedRoutes()" in match.group(0)


def test_images_view_is_registered_as_a_route():
    router = _read("js", "pages", "project", "router.js")
    assert re.search(r'images:\s*\(\)\s*=>\s*import\("\./images\.js\?v=\d+"\)', router)


def test_module_version_pins_are_consistent_across_import_sites():
    """A partial bump ships clients a mixture of old and new modules.

    Every import of a given module must carry the same ?v=, including the page
    entry point in project.html.
    """
    pins = {}
    roots = [FRONTEND_JS_DIR, os.path.dirname(FRONTEND_JS_DIR)]
    seen = set()
    for base in roots:
        for dirpath, _, filenames in os.walk(base):
            if "node_modules" in dirpath:
                continue
            for name in filenames:
                if not name.endswith((".js", ".html")):
                    continue
                path = os.path.join(dirpath, name)
                if path in seen:
                    continue
                seen.add(path)
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                for module, version in re.findall(r'([\w./-]+\.js)\?v=(\d+)', content):
                    key = os.path.basename(module)
                    pins.setdefault(key, {}).setdefault(version, []).append(
                        os.path.relpath(path, os.path.dirname(FRONTEND_JS_DIR))
                    )

    # Only assert on the modules this feature touched; the repo has pre-existing
    # inconsistencies elsewhere that are not this change's to fix.
    for module in ("project-nav.js", "router.js", "image-sizes.js", "images.js"):
        versions = pins.get(module)
        if not versions:
            continue
        assert len(versions) == 1, (
            f"{module} is pinned at multiple versions across import sites: "
            f"{ {v: sites for v, sites in versions.items()} }"
        )


def test_client_mirror_is_imported_by_the_view():
    view = _read("js", "pages", "project", "images.js")
    assert "image-sizes.js" in view
    assert "IMAGE_SIZE_CATEGORIES" in view


def test_search_is_debounced():
    """A request per keystroke is self-inflicted load; data-table has no
    debounce of its own, so the view must add one."""
    view = _read("js", "pages", "project", "images.js")
    assert "SEARCH_DEBOUNCE_MS" in view
    assert "setTimeout" in view


def test_size_pill_styles_exist_and_muted_catch_alls_share_one():
    css = _read("styles.css")
    assert ".pill.is-size-full" in css
    assert ".pill.is-size-half" in css
    # Other and Unknown share one neutral style deliberately.
    assert ".pill.is-size-muted" in css
    assert ".pill.is-size-other" not in css
    assert ".pill.is-size-unknown" not in css

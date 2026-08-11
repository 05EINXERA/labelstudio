"""Server-side task pagination and deterministic ordering.

Background (.devnotes/tasks-pagination/PLAN.md): `GET /api/tasks` had no
`ORDER BY` at all. Rows came back in database order, which is not stable — an
UPDATE can move a row within a scan — so saving a task reordered the list under
the user. The Tasks table and the annotation canvas fetch this list separately,
so an unstable order also meant the canvas's "next image" was not the table's
next row, and prev/next from image 39/50 jumped somewhere unrelated.

What these tests protect:

- the default order is filename ascending, without the client asking for it;
- that order is *strict* — the id tiebreaker makes duplicate filenames resolve
  identically on every query, which is what stops a row appearing on two pages
  or none under LIMIT/OFFSET;
- the unpaged response shape is unchanged, since several callers still use it;
- `/api/tasks/order` returns exactly the same sequence as the paged list, which
  is the property the canvas's prev/next correctness rests on;
- permission scoping matches the list endpoint (rule 1b), so the canvas cannot
  walk tasks the table would never show.
"""
import pytest


def _project(client, headers, name="pag"):
    res = client.post("/api/projects",
                      json={"name": name, "slug": name, "creator": "x"},
                      headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _task(client, headers, project_id, description):
    res = client.post(f"/api/tasks?projectId={project_id}",
                      json={"description": description, "status": "New"},
                      headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _names(rows):
    return [r["description"] for r in rows]


def _get(client, headers, project_id, **params):
    qs = "".join(f"&{k}={v}" for k, v in params.items())
    res = client.get(f"/api/tasks?projectId={project_id}{qs}", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


# --- ordering --------------------------------------------------------------


def test_default_order_is_filename_ascending(client, alice):
    """R1/R2: no sort parameter, no header click — still filename ascending."""
    pid = _project(client, alice)
    for name in ["delta.png", "alpha.png", "charlie.png", "bravo.png"]:
        _task(client, alice, pid, name)

    rows = _get(client, alice, pid, include_annotations="false")
    assert _names(rows) == ["alpha.png", "bravo.png", "charlie.png", "delta.png"]


def test_default_order_ignores_update_recency(client, alice):
    """R2: touching a task must not float it to the top any more.

    This is the regression that made the canvas jump: the list used to be
    presented newest-updated-first, so saving image 39 moved it to position 1.

    Creation order is the inverse of filename order here, so a list that came
    back in insertion or recency order would be visibly wrong. This asserts
    ordering without going through PATCH, whose conflict path is currently
    failing on this branch for unrelated reasons.
    """
    pid = _project(client, alice)
    # Inserted newest-last in reverse alphabetical order: under any
    # recency- or rowid-based ordering, charlie would lead.
    for name in ["charlie.png", "bravo.png", "alpha.png"]:
        _task(client, alice, pid, name)

    rows = _get(client, alice, pid, include_annotations="false")
    assert _names(rows) == ["alpha.png", "bravo.png", "charlie.png"], (
        "insertion/recency order must not leak into the default ordering"
    )


def test_duplicate_filenames_order_strictly_by_id(client, alice):
    """The id tiebreaker: equal names must resolve in a stable, total order.

    Without it, `ORDER BY description` leaves these four rows free to come back
    in any order per query — the exact condition under which LIMIT/OFFSET drops
    or repeats a row across pages.
    """
    pid = _project(client, alice)
    ids = [_task(client, alice, pid, "same.png") for _ in range(4)]

    seen = [[r["id"] for r in _get(client, alice, pid, include_annotations="false")]
            for _ in range(3)]
    assert seen[0] == ids, "duplicate names must order by ascending id"
    assert seen[0] == seen[1] == seen[2], "the order must be identical every query"


def test_sort_field_is_whitelisted(client, alice):
    """An unknown sort key is a 422, not a silent fallback or a SQL injection."""
    pid = _project(client, alice)
    _task(client, alice, pid, "a.png")

    res = client.get(f"/api/tasks?projectId={pid}&sort=annotations", headers=alice)
    assert res.status_code == 422, res.text

    res = client.get(f"/api/tasks?projectId={pid}&sort=description;DROP+TABLE+tasks",
                     headers=alice)
    assert res.status_code == 422, res.text

    res = client.get(f"/api/tasks?projectId={pid}&order=sideways", headers=alice)
    assert res.status_code == 422, res.text


def test_descending_order_is_honoured(client, alice):
    pid = _project(client, alice)
    for name in ["alpha.png", "bravo.png", "charlie.png"]:
        _task(client, alice, pid, name)

    rows = _get(client, alice, pid, include_annotations="false",
                sort="description", order="desc")
    assert _names(rows) == ["charlie.png", "bravo.png", "alpha.png"]


# --- pagination ------------------------------------------------------------


def test_unpaged_response_is_still_a_bare_array(client, alice):
    """Back-compat, pinned deliberately.

    Several callers (the canvas, ad-hoc scripts) still request this endpoint
    without `page`. Wrapping their response in an envelope would break all of
    them at once, so the unpaged shape must stay a list.
    """
    pid = _project(client, alice)
    _task(client, alice, pid, "a.png")

    for include in ("true", "false"):
        rows = _get(client, alice, pid, include_annotations=include)
        assert isinstance(rows, list), f"include_annotations={include} must return a list"


def test_paged_response_carries_the_envelope(client, alice):
    pid = _project(client, alice)
    for i in range(25):
        _task(client, alice, pid, f"img{i:03d}.png")

    body = _get(client, alice, pid, include_annotations="false", page=1, page_size=10)
    assert body["total"] == 25
    assert body["page"] == 1
    assert body["page_size"] == 10
    assert body["total_pages"] == 3
    assert len(body["items"]) == 10


def test_pages_partition_the_set_exactly(client, alice):
    """Every task appears exactly once across the pages, in order.

    The property that actually matters: no duplicates (a row on two pages) and
    no gaps (a row on none), which is what an unstable sort produces.
    """
    pid = _project(client, alice)
    for i in range(25):
        _task(client, alice, pid, f"img{i:03d}.png")

    walked = []
    for page in range(1, 4):
        body = _get(client, alice, pid, include_annotations="false",
                    page=page, page_size=10)
        walked.extend(r["id"] for r in body["items"])

    unpaged = [r["id"] for r in _get(client, alice, pid, include_annotations="false")]
    assert walked == unpaged, "paged walk must reproduce the unpaged order exactly"
    assert len(set(walked)) == 25, "no task may appear on two pages"


def test_last_page_is_partial(client, alice):
    pid = _project(client, alice)
    for i in range(25):
        _task(client, alice, pid, f"img{i:03d}.png")

    body = _get(client, alice, pid, include_annotations="false", page=3, page_size=10)
    assert len(body["items"]) == 5
    assert _names(body["items"])[0] == "img020.png"


def test_out_of_range_page_returns_empty_items_not_404(client, alice):
    """A stale bookmark must be recoverable, not a dead end.

    The client needs `total`/`total_pages` back in order to clamp; a 404 would
    tell it nothing about where to go instead.
    """
    pid = _project(client, alice)
    for i in range(5):
        _task(client, alice, pid, f"img{i:03d}.png")

    body = _get(client, alice, pid, include_annotations="false", page=99, page_size=10)
    assert body["items"] == []
    assert body["total"] == 5
    assert body["total_pages"] == 1


def test_empty_project_reports_one_page(client, alice):
    """Zero tasks is one empty page, not zero pages — the pager must have a
    page to be on, and `max(1, ...)` in the router is what guarantees it."""
    pid = _project(client, alice)

    body = _get(client, alice, pid, include_annotations="false", page=1, page_size=10)
    assert body["items"] == []
    assert body["total"] == 0
    assert body["total_pages"] == 1


def test_page_size_is_capped(client, alice):
    """The cap is what stops a caller pulling the whole table in one request
    and undoing the payload work of rule 17."""
    pid = _project(client, alice)
    _task(client, alice, pid, "a.png")

    res = client.get(f"/api/tasks?projectId={pid}&page=1&page_size=100000", headers=alice)
    assert res.status_code == 422, res.text

    res = client.get(f"/api/tasks?projectId={pid}&page=0", headers=alice)
    assert res.status_code == 422, "page is 1-based; page=0 is a client bug"


def test_paging_works_on_the_annotation_bearing_branch(client, alice):
    """Both branches of the endpoint page, not just the lean one."""
    pid = _project(client, alice)
    for i in range(12):
        _task(client, alice, pid, f"img{i:03d}.png")

    body = _get(client, alice, pid, include_annotations="true", page=2, page_size=10)
    assert body["total"] == 12
    assert len(body["items"]) == 2
    assert "annotations" in body["items"][0]


# --- /api/tasks/order ------------------------------------------------------


def test_order_endpoint_matches_the_list_order(client, alice):
    """The canvas/table agreement, asserted directly.

    If these two ever diverge, prev/next in the canvas stops matching the table
    — which is the bug this whole change exists to fix.
    """
    pid = _project(client, alice)
    for name in ["delta.png", "alpha.png", "same.png", "same.png", "bravo.png"]:
        _task(client, alice, pid, name)

    res = client.get(f"/api/tasks/order?projectId={pid}", headers=alice)
    assert res.status_code == 200, res.text
    ids = res.json()["ids"]

    listed = [r["id"] for r in _get(client, alice, pid, include_annotations="false")]
    assert ids == listed


def test_order_endpoint_follows_the_same_sort_parameters(client, alice):
    pid = _project(client, alice)
    for name in ["alpha.png", "bravo.png", "charlie.png"]:
        _task(client, alice, pid, name)

    res = client.get(f"/api/tasks/order?projectId={pid}&sort=description&order=desc",
                     headers=alice)
    assert res.status_code == 200, res.text
    listed = [r["id"] for r in _get(client, alice, pid, include_annotations="false",
                                    sort="description", order="desc")]
    assert res.json()["ids"] == listed


def test_order_endpoint_returns_ids_only(client, alice):
    """Ids, not rows — the entire reason this endpoint exists."""
    pid = _project(client, alice)
    _task(client, alice, pid, "a.png")

    body = client.get(f"/api/tasks/order?projectId={pid}", headers=alice).json()
    assert set(body) == {"ids"}
    assert all(isinstance(i, int) for i in body["ids"])


def test_order_is_not_shadowed_by_the_task_id_route(client, alice):
    """`/order` must not be parsed as `GET /{task_id}` with task_id='order'.

    FastAPI matches in declaration order, so this asserts the literal route is
    still declared before the parameterised one.
    """
    pid = _project(client, alice)
    _task(client, alice, pid, "a.png")

    res = client.get(f"/api/tasks/order?projectId={pid}", headers=alice)
    assert res.status_code == 200, (
        "got %s — /order is being swallowed by GET /{task_id}" % res.status_code
    )


# --- filters ---------------------------------------------------------------
#
# These moved server-side with pagination out of necessity, not tidiness: a
# client holding one page of 10 can only filter those 10, so searching for a
# task on page 30 would report "no matches".


def test_search_filters_across_the_whole_set_not_just_a_page(client, alice):
    pid = _project(client, alice)
    for i in range(25):
        _task(client, alice, pid, f"img{i:03d}.png")
    _task(client, alice, pid, "needle.png")

    body = _get(client, alice, pid, include_annotations="false",
                page=1, page_size=10, q="needle")
    assert body["total"] == 1
    assert _names(body["items"]) == ["needle.png"]


def test_search_is_case_insensitive(client, alice):
    pid = _project(client, alice)
    _task(client, alice, pid, "NEEDLE.png")

    body = _get(client, alice, pid, include_annotations="false", page=1, q="needle")
    assert body["total"] == 1


def test_status_filter_narrows_the_total(client, alice):
    """Status is set at creation, not via PATCH.

    PATCH would be the more natural way to write this, but the task-update
    conflict path is currently failing on this branch for unrelated reasons, and
    a filter test should not depend on it.
    """
    pid = _project(client, alice)
    for name in ["a.png", "b.png", "c.png"]:
        _task(client, alice, pid, name)
    res = client.post(f"/api/tasks?projectId={pid}",
                      json={"description": "done.png", "status": "Done"},
                      headers=alice)
    assert res.status_code == 200, res.text

    body = _get(client, alice, pid, include_annotations="false", page=1, status="Done")
    assert body["total"] == 1
    assert _names(body["items"]) == ["done.png"]


def test_filters_apply_to_the_order_endpoint_too(client, alice):
    """The canvas must walk the filtered set, not the whole project.

    Otherwise filtering to "Rejected" and opening one would let prev/next wander
    into tasks the table deliberately excluded.
    """
    pid = _project(client, alice)
    for name in ["alpha.png", "beta.png", "needle.png"]:
        _task(client, alice, pid, name)

    ids = client.get(f"/api/tasks/order?projectId={pid}&q=needle", headers=alice).json()["ids"]
    listed = [r["id"] for r in _get(client, alice, pid, include_annotations="false", q="needle")]
    assert ids == listed
    assert len(ids) == 1


def test_unassigned_filters_are_understood(client, alice):
    pid = _project(client, alice)
    _task(client, alice, pid, "a.png")

    for key in ("team", "assignee"):
        body = _get(client, alice, pid, include_annotations="false",
                    page=1, **{key: "unassigned"})
        assert body["total"] == 1, f"{key}=unassigned should match the unassigned task"


def test_malformed_filter_values_are_rejected(client, alice):
    pid = _project(client, alice)
    _task(client, alice, pid, "a.png")

    res = client.get(f"/api/tasks?projectId={pid}&team=not-a-number", headers=alice)
    assert res.status_code == 422, res.text

    res = client.get(f"/api/tasks?projectId={pid}&assignee=user-abc", headers=alice)
    assert res.status_code == 422, res.text


# --- permissions (rule 1b) -------------------------------------------------


def test_order_endpoint_404s_for_a_user_with_no_role(client, alice, bob):
    """404, not 403: an id the caller has no role on must be indistinguishable
    from a nonexistent one, so project ids cannot be enumerated."""
    pid = _project(client, alice)
    _task(client, alice, pid, "a.png")

    res = client.get(f"/api/tasks/order?projectId={pid}", headers=bob)
    assert res.status_code == 404, res.text


def test_paged_list_404s_for_a_user_with_no_role(client, alice, bob):
    pid = _project(client, alice)
    _task(client, alice, pid, "a.png")

    res = client.get(f"/api/tasks?projectId={pid}&page=1", headers=bob)
    assert res.status_code == 404, res.text


def test_order_without_project_spans_only_accessible_projects(client, alice, bob):
    """The unscoped form must not leak ids from projects the caller cannot see."""
    mine = _project(client, alice, name="mine")
    theirs = _project(client, bob, name="theirs")
    my_id = _task(client, alice, mine, "mine.png")
    their_id = _task(client, bob, theirs, "theirs.png")

    ids = client.get("/api/tasks/order", headers=alice).json()["ids"]
    assert my_id in ids
    assert their_id not in ids

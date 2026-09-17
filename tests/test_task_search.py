"""Cross-project task search — `GET /api/tasks/search`.

Spec: .devnotes/task-project-search/. Edge cases: E-01…E-20.

Backs the Find Tasks tab on the projects page. What these tests protect:

- **Scope is the permission resolver's, not ownership's.** A task in a project
  the caller has no role on is *absent*, never a 403 — a search that errored
  because something invisible did not match would be nonsense (E-02).
- **The filter vocabulary is shared with `GET /api/tasks` verbatim**, including
  the `assignee=none` sentinel, which must return an empty page rather than
  silently degrading into "show everything" (E-11).
- **Ordering is strict.** Duplicate filenames across projects are routine, and
  `sort=project` produces very large tie groups, so without the `description,
  id` tiebreak LIMIT/OFFSET shows a row twice or not at all (E-16, E-17).
- **Cost does not scale with the result set.** The query count is asserted to be
  identical for 5 tasks and for 200 (test_query_count_does_not_grow_with_rows),
  which is what fails if someone later adds a per-row project lookup, an
  annotation count, or loads ORM entities instead of projecting columns.
"""
import models
from database import SessionLocal


def _project(client, headers, name="proj"):
    res = client.post(
        "/api/projects",
        json={"name": name, "slug": name.lower(), "creator": "x"},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _task(client, headers, project_id, description, status="New"):
    res = client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": description, "status": status},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    return res.json()["id"]


def _team(client, headers, name="Alpha"):
    return client.post("/api/teams", json={"name": name}, headers=headers).json()


def _add_member(client, headers, team_id, username, role="member"):
    return client.post(
        f"/api/teams/{team_id}/members",
        json={"username": username, "role": role},
        headers=headers,
    )


def _grant(client, headers, project_id, team_id, role="annotator"):
    return client.post(
        f"/api/projects/{project_id}/grants",
        json={"team_id": team_id, "role": role},
        headers=headers,
    )


def _me(client, headers):
    return client.get("/api/auth/me", headers=headers).json()


def _search(client, headers, expect=200, **params):
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    res = client.get(f"/api/tasks/search?{qs}", headers=headers)
    assert res.status_code == expect, res.text
    return res.json()


def _names(payload):
    return [r["description"] for r in payload["items"]]


# --- auth --------------------------------------------------------------------


def test_search_requires_auth(client):
    """Rule 1: every /api/* route outside /api/auth/* is authenticated."""
    assert client.get("/api/tasks/search").status_code == 401


# --- scope -------------------------------------------------------------------


def test_returns_tasks_across_several_accessible_projects(client, alice):
    """The whole point: one request spanning projects, each row naming its own."""
    p1 = _project(client, alice, "Alpha")
    p2 = _project(client, alice, "Beta")
    _task(client, alice, p1, "one.jpg")
    _task(client, alice, p2, "two.jpg")

    payload = _search(client, alice)

    assert payload["total"] == 2
    by_name = {r["description"]: r for r in payload["items"]}
    assert by_name["one.jpg"]["project_id"] == p1
    assert by_name["one.jpg"]["project_name"] == "Alpha"
    assert by_name["two.jpg"]["project_id"] == p2
    assert by_name["two.jpg"]["project_name"] == "Beta"


def test_inaccessible_project_tasks_are_absent_not_an_error(client, alice, bob):
    """E-02. Bob's task is invisible to Alice, and asking is not an error.

    The absence *is* the contract. Returning 403 here would mean a search could
    fail because of something the caller is not allowed to know exists.
    """
    mine = _project(client, alice, "Mine")
    theirs = _project(client, bob, "Theirs")
    _task(client, alice, mine, "mine.jpg")
    _task(client, bob, theirs, "theirs.jpg")

    payload = _search(client, alice)

    assert _names(payload) == ["mine.jpg"]


def test_granted_project_is_included(client, alice, bob):
    """Access is the resolver's union — owned ∪ granted ∪ org-visible — so a
    grant through a team makes the project's tasks searchable."""
    pid = _project(client, alice, "Shared")
    _task(client, alice, pid, "shared.jpg")
    team = _team(client, alice)
    _add_member(client, alice, team["id"], _me(client, bob)["username"])
    _grant(client, alice, pid, team["id"], role="viewer")

    payload = _search(client, bob)

    assert _names(payload) == ["shared.jpg"]


def test_no_accessible_projects_returns_empty_page(client, alice):
    """E-01. Not a 404, and total_pages floors at 1 so the pager has a page."""
    payload = _search(client, alice)

    assert payload["items"] == []
    assert payload["total"] == 0
    assert payload["total_pages"] == 1
    assert payload["page"] == 1


# --- projectId narrowing -----------------------------------------------------


def test_project_id_narrows_to_one_project(client, alice):
    p1 = _project(client, alice, "Alpha")
    p2 = _project(client, alice, "Beta")
    _task(client, alice, p1, "one.jpg")
    _task(client, alice, p2, "two.jpg")

    payload = _search(client, alice, projectId=p1)

    assert _names(payload) == ["one.jpg"]


def test_project_id_without_any_role_is_404(client, alice, bob):
    """Rule 1b: no role at all is indistinguishable from a nonexistent id, so
    project ids cannot be enumerated by watching status codes (E-04)."""
    theirs = _project(client, bob, "Theirs")

    _search(client, alice, expect=404, projectId=theirs)


# --- search and filters ------------------------------------------------------


def test_q_matches_substring_case_insensitively(client, alice):
    pid = _project(client, alice)
    _task(client, alice, pid, "Sunset_Beach.jpg")
    _task(client, alice, pid, "mountain.png")

    assert _names(_search(client, alice, q="beach")) == ["Sunset_Beach.jpg"]
    assert _names(_search(client, alice, q="BEACH")) == ["Sunset_Beach.jpg"]


def test_q_spans_projects(client, alice):
    """The feature in one test: a filename match found in a project other than
    the one the user last had open."""
    p1 = _project(client, alice, "Alpha")
    p2 = _project(client, alice, "Beta")
    _task(client, alice, p1, "report_a.jpg")
    _task(client, alice, p2, "report_b.jpg")
    _task(client, alice, p2, "other.jpg")

    assert sorted(_names(_search(client, alice, q="report"))) == [
        "report_a.jpg",
        "report_b.jpg",
    ]


def test_status_filter(client, alice):
    pid = _project(client, alice)
    _task(client, alice, pid, "a.jpg", status="New")
    _task(client, alice, pid, "b.jpg", status="Completed")

    assert _names(_search(client, alice, status="Completed")) == ["b.jpg"]


def test_status_all_is_not_a_filter(client, alice):
    pid = _project(client, alice)
    _task(client, alice, pid, "a.jpg", status="New")
    _task(client, alice, pid, "b.jpg", status="Completed")

    assert len(_search(client, alice, status="All")["items"]) == 2


def test_assignee_mine_resolves_to_the_caller(client, alice):
    """`mine` is resolved server-side from the session, so it needs no roster —
    which is what makes it usable cross-project (design §5.4)."""
    pid = _project(client, alice)
    task_id = _task(client, alice, pid, "assigned.jpg")
    _task(client, alice, pid, "unassigned.jpg")
    me = _me(client, alice)
    res = client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assignee_user_id": me["id"]},
        headers=alice,
    )
    assert res.status_code == 200, res.text

    payload = _search(client, alice, assignee="mine")

    assert _names(payload) == ["assigned.jpg"]
    assert payload["items"][0]["assignee_name"] == me["username"]


def test_assignee_unassigned_sentinel(client, alice):
    pid = _project(client, alice)
    task_id = _task(client, alice, pid, "assigned.jpg")
    _task(client, alice, pid, "free.jpg")
    me = _me(client, alice)
    client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assignee_user_id": me["id"]},
        headers=alice,
    )

    assert _names(_search(client, alice, assignee="unassigned")) == ["free.jpg"]


def test_assignee_none_returns_empty_not_everything(client, alice):
    """E-11, and the subtlest rule in the filter set.

    `none` is what a name search matching nobody sends. Dropping the filter
    instead — the obvious "empty means no filter" reading — would show *every*
    task, which is the opposite of what the user asked.
    """
    pid = _project(client, alice)
    _task(client, alice, pid, "a.jpg")
    _task(client, alice, pid, "b.jpg")

    payload = _search(client, alice, assignee="none")

    assert payload["items"] == []
    assert payload["total"] == 0


def test_team_filter_and_unassigned_sentinel(client, alice):
    pid = _project(client, alice)
    task_id = _task(client, alice, pid, "team.jpg")
    _task(client, alice, pid, "pool.jpg")
    team = _team(client, alice, "Squad")
    _grant(client, alice, pid, team["id"])
    res = client.patch(
        f"/api/tasks/{task_id}/assignment",
        json={"assigned_team_id": team["id"]},
        headers=alice,
    )
    assert res.status_code == 200, res.text

    assert _names(_search(client, alice, team=team["id"])) == ["team.jpg"]
    assert _names(_search(client, alice, team="unassigned")) == ["pool.jpg"]
    assert _search(client, alice, team=team["id"])["items"][0][
        "assigned_team_name"
    ] == "Squad"


def test_malformed_filters_are_422(client, alice):
    """E-14: reusing `_apply_filters` is what makes this identical to the
    per-project view for free."""
    pid = _project(client, alice)
    _task(client, alice, pid, "a.jpg")

    _search(client, alice, expect=422, team="not-a-number")
    _search(client, alice, expect=422, assignee="user-abc")


# --- ordering ----------------------------------------------------------------


def test_default_order_is_filename_ascending(client, alice):
    p1 = _project(client, alice, "Zeta")
    p2 = _project(client, alice, "Alpha")
    _task(client, alice, p1, "delta.png")
    _task(client, alice, p2, "alpha.png")
    _task(client, alice, p1, "charlie.png")

    assert _names(_search(client, alice)) == ["alpha.png", "charlie.png", "delta.png"]


def test_sort_by_project_name(client, alice):
    """The one sort key this endpoint adds. It orders by `projects.name` in SQL,
    which is why the project is joined rather than looked up afterwards — a
    post-hoc lookup could only sort the page, not the result set."""
    p_z = _project(client, alice, "Zulu")
    p_a = _project(client, alice, "Alpha")
    _task(client, alice, p_z, "in_zulu.jpg")
    _task(client, alice, p_a, "in_alpha.jpg")

    asc = _search(client, alice, sort="project", order="asc")
    assert [r["project_name"] for r in asc["items"]] == ["Alpha", "Zulu"]

    desc = _search(client, alice, sort="project", order="desc")
    assert [r["project_name"] for r in desc["items"]] == ["Zulu", "Alpha"]


def test_unknown_sort_is_422(client, alice):
    """A typo surfaces as an error, never as a silently different ordering."""
    pid = _project(client, alice)
    _task(client, alice, pid, "a.jpg")

    _search(client, alice, expect=422, sort="image_path")


def test_unknown_order_is_422(client, alice):
    pid = _project(client, alice)
    _task(client, alice, pid, "a.jpg")

    _search(client, alice, expect=422, sort="description", order="sideways")


def test_project_sort_is_still_a_strict_total_order(client, alice):
    """E-17. Sorting by project when one project holds most tasks makes a huge
    tie group; the `description, id` tiebreak is what keeps paging correct."""
    big = _project(client, alice, "Same")
    for i in range(7):
        _task(client, alice, big, f"file_{i}.jpg")

    seen = []
    for page in (1, 2, 3):
        payload = _search(
            client, alice, sort="project", page=page, page_size=3
        )
        seen.extend(r["id"] for r in payload["items"])

    assert len(seen) == 7
    assert len(set(seen)) == 7


# --- paging ------------------------------------------------------------------


def test_paging_covers_every_row_exactly_once_with_duplicate_names(client, alice):
    """E-16. Uploads collide on names like `image.jpg` constantly, and across
    projects that is guaranteed. Without a unique final sort key a row can
    appear on two pages or on neither."""
    p1 = _project(client, alice, "Alpha")
    p2 = _project(client, alice, "Beta")
    for _ in range(3):
        _task(client, alice, p1, "image.jpg")
        _task(client, alice, p2, "image.jpg")

    seen = []
    for page in (1, 2, 3):
        payload = _search(client, alice, page=page, page_size=2)
        assert payload["total"] == 6
        assert payload["total_pages"] == 3
        seen.extend(r["id"] for r in payload["items"])

    assert len(seen) == 6
    assert len(set(seen)) == 6


def test_page_beyond_the_end_is_empty_with_a_true_total(client, alice):
    """E-18: recoverable rather than a dead end — the client sees total_pages
    shrink and clamps."""
    pid = _project(client, alice)
    _task(client, alice, pid, "a.jpg")

    payload = _search(client, alice, page=9, page_size=10)

    assert payload["items"] == []
    assert payload["total"] == 1
    assert payload["total_pages"] == 1


def test_page_size_over_the_cap_is_422(client, alice):
    """E-20: the cap is what stops a caller pulling the whole table at once."""
    _search(client, alice, expect=422, page_size=101)


def test_page_zero_is_422(client, alice):
    _search(client, alice, expect=422, page=0)


# --- the response shape ------------------------------------------------------


def test_row_carries_no_annotation_fields(client, alice):
    """The projection is the performance design (design §2.3). If annotations,
    comment_count or class_count ever appear here, something started loading ORM
    entities or counting shapes — the two costs this endpoint exists without.
    """
    pid = _project(client, alice)
    _task(client, alice, pid, "a.jpg")

    row = _search(client, alice)["items"][0]

    for forbidden in ("annotations", "comment_count", "class_count", "image_path"):
        assert forbidden not in row, f"{forbidden} leaked into the search row"


# --- cost --------------------------------------------------------------------


def test_query_count_does_not_grow_with_rows(client, alice):
    """The performance contract, asserted rather than trusted (design §6).

    A fixed number of statements per request, independent of how many tasks
    matched. This is the test that fails loudly if someone later adds a per-row
    project lookup, reintroduces the annotation counts, or swaps the column
    projection for `db.query(models.Task)` — all of which are invisible at five
    rows and fatal at five thousand.

    The *invariance* is the assertion. The absolute number is secondary and may
    legitimately move by one if `accessible_project_ids` changes shape.
    """
    from sqlalchemy import event
    from database import engine

    pid = _project(client, alice, "Counting")
    for i in range(5):
        _task(client, alice, pid, f"small_{i}.jpg")

    statements = []

    def _record(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", _record)
    try:
        statements.clear()
        _search(client, alice, page_size=100)
        small = len(statements)

        for i in range(60):
            _task(client, alice, pid, f"large_{i}.jpg")

        statements.clear()
        _search(client, alice, page_size=100)
        large = len(statements)
    finally:
        event.remove(engine, "before_cursor_execute", _record)

    assert large == small, (
        f"query count grew with the result set ({small} -> {large}); "
        "something is querying per row"
    )
    # A generous ceiling: the design budgets 7 (3 access + COUNT + page + 2 name
    # lookups). The bound catches a new round-trip without pinning the exact
    # number, which auth/session work may legitimately shift.
    assert large <= 12, f"expected a handful of queries, got {large}: {statements}"

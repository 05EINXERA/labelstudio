"""Tests for the workspace-wide task search behind the projects page's
"Find tasks" view.

`GET /api/tasks` already returned tasks from every accessible project when
called without `projectId`; what the search needed on top was (a) each row
saying which project it came from, (b) a `projectIds` filter to narrow the
sweep, and (c) a sort key that cannot be steered into a non-column attribute.
These pin all three, plus the access scoping the feature leans on — a
cross-project sweep is the one call that would leak another user's tasks if
the accessible-project filter regressed.
"""
import models
from database import SessionLocal


def _make_project(client, headers, name, slug):
    res = client.post(
        "/api/projects",
        json={"name": name, "slug": slug, "creator": "tester"},
        headers=headers,
    )
    assert res.status_code in (200, 201), res.text
    return res.json()["id"]


def _seed_tasks(project_id, filenames, assignee=None, status="New"):
    """Create tasks directly; the read path is what's under test here."""
    ids = []
    with SessionLocal() as db:
        for name in filenames:
            task = models.Task(
                project_id=project_id,
                description=name,
                image_path=name,
                status=status,
                assignee=assignee,
                time_spent=0,
            )
            db.add(task)
            db.flush()
            ids.append(task.id)
        db.commit()
    return ids


def _search(client, headers, **params):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    res = client.get(f"/api/tasks?{query}" if query else "/api/tasks", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


def test_cross_project_search_labels_each_row_with_its_project(client, alice):
    """Without projectId, every row carries the project it belongs to.

    This is the column the whole view exists for: a filename alone does not
    tell the reader where to go and open it.
    """
    p1 = _make_project(client, alice, "Road Survey", "road-survey")
    p2 = _make_project(client, alice, "Warehouse QA", "warehouse-qa")
    _seed_tasks(p1, ["road_0001.jpg"])
    _seed_tasks(p2, ["wh_0001.jpg"])

    by_name = {
        item["description"]: item
        for item in _search(client, alice, limit=50)["items"]
    }

    assert by_name["road_0001.jpg"]["project_id"] == p1
    assert by_name["road_0001.jpg"]["project_name"] == "Road Survey"
    assert by_name["wh_0001.jpg"]["project_id"] == p2
    assert by_name["wh_0001.jpg"]["project_name"] == "Warehouse QA"


def test_per_project_listing_omits_the_project_columns(client, alice):
    """The project-scoped listing leaves them None.

    The project Tasks table already names its project in the page heading, so
    repeating it on every row would be noise — and the gallery poll that hits
    this endpoint every 30 s should not pay for a lookup it does not use.
    """
    p1 = _make_project(client, alice, "Road Survey", "road-survey")
    _seed_tasks(p1, ["road_0001.jpg"])

    items = _search(client, alice, projectId=p1, limit=50)["items"]

    assert len(items) == 1
    assert items[0]["project_id"] is None
    assert items[0]["project_name"] is None


def test_cross_project_search_excludes_another_users_tasks(client, alice, bob):
    """The sweep is scoped to projects the caller can reach."""
    mine = _make_project(client, alice, "Mine", "mine")
    theirs = _make_project(client, bob, "Theirs", "theirs")
    _seed_tasks(mine, ["mine_0001.jpg"])
    _seed_tasks(theirs, ["theirs_0001.jpg"])

    names = {item["description"] for item in _search(client, alice, limit=50)["items"]}

    assert "mine_0001.jpg" in names
    assert "theirs_0001.jpg" not in names


def test_search_term_matches_across_projects(client, alice):
    """One query reaches tasks in different projects."""
    p1 = _make_project(client, alice, "Road Survey", "road-survey")
    p2 = _make_project(client, alice, "Warehouse QA", "warehouse-qa")
    _seed_tasks(p1, ["target_a.jpg", "other_1.jpg"])
    _seed_tasks(p2, ["target_b.jpg", "other_2.jpg"])

    data = _search(client, alice, search="target", limit=50)

    assert data["total"] == 2
    assert {item["description"] for item in data["items"]} == {"target_a.jpg", "target_b.jpg"}


def test_project_ids_filter_narrows_the_sweep(client, alice):
    """`projectIds` restricts a cross-project search to chosen projects."""
    p1 = _make_project(client, alice, "Road Survey", "road-survey")
    p2 = _make_project(client, alice, "Warehouse QA", "warehouse-qa")
    _seed_tasks(p1, ["road_0001.jpg"])
    _seed_tasks(p2, ["wh_0001.jpg"])

    data = _search(client, alice, projectIds=p1, limit=50)

    assert {item["description"] for item in data["items"]} == {"road_0001.jpg"}


def test_project_ids_cannot_widen_access(client, alice, bob):
    """Naming an unreachable project filters everything out, never exposes it.

    `projectIds` is intersected with the accessible set rather than replacing
    it, so this stays empty instead of leaking bob's task.
    """
    theirs = _make_project(client, bob, "Theirs", "theirs")
    _seed_tasks(theirs, ["theirs_0001.jpg"])

    data = _search(client, alice, projectIds=theirs, limit=50)

    assert data["total"] == 0
    assert data["items"] == []


def test_status_and_assignee_filters_apply_across_projects(client, alice):
    p1 = _make_project(client, alice, "Road Survey", "road-survey")
    p2 = _make_project(client, alice, "Warehouse QA", "warehouse-qa")
    _seed_tasks(p1, ["done_a.jpg"], assignee="ravi", status="Completed")
    _seed_tasks(p1, ["new_a.jpg"], assignee="ravi", status="New")
    _seed_tasks(p2, ["done_b.jpg"], assignee="sam", status="Completed")

    by_status = _search(client, alice, status="Completed", limit=50)
    assert {i["description"] for i in by_status["items"]} == {"done_a.jpg", "done_b.jpg"}

    by_assignee = _search(client, alice, assignee="ravi", limit=50)
    assert {i["description"] for i in by_assignee["items"]} == {"done_a.jpg", "new_a.jpg"}


def test_my_tasks_filter_spans_projects(client, alice):
    """The "My Tasks" checkbox filters by assignee across every project.

    It sends the same `assignee` parameter the project Tasks tab's box does;
    what makes it worth its own test is that here the result must gather one
    person's work from *several* projects in one list.
    """
    p1 = _make_project(client, alice, "Road Survey", "road-survey")
    p2 = _make_project(client, alice, "Warehouse QA", "warehouse-qa")
    _seed_tasks(p1, ["mine_road.jpg"], assignee="ravi")
    _seed_tasks(p1, ["theirs_road.jpg"], assignee="sam")
    _seed_tasks(p2, ["mine_wh.jpg"], assignee="ravi")

    data = _search(client, alice, assignee="ravi", limit=50)

    assert data["total"] == 2
    assert {i["description"] for i in data["items"]} == {"mine_road.jpg", "mine_wh.jpg"}
    # Each still names its own project, so one list covers both.
    assert {i["project_name"] for i in data["items"]} == {"Road Survey", "Warehouse QA"}


def test_my_tasks_filter_combines_with_search_and_project(client, alice):
    """Assignee narrows alongside the other filters rather than replacing them."""
    p1 = _make_project(client, alice, "Road Survey", "road-survey")
    p2 = _make_project(client, alice, "Warehouse QA", "warehouse-qa")
    _seed_tasks(p1, ["road_mine.jpg"], assignee="ravi")
    _seed_tasks(p1, ["other_mine.jpg"], assignee="ravi")
    _seed_tasks(p1, ["road_theirs.jpg"], assignee="sam")
    _seed_tasks(p2, ["road_mine_elsewhere.jpg"], assignee="ravi")

    with_search = _search(client, alice, assignee="ravi", search="road", limit=50)
    assert {i["description"] for i in with_search["items"]} == {
        "road_mine.jpg", "road_mine_elsewhere.jpg"
    }

    with_project = _search(client, alice, assignee="ravi", search="road",
                           projectIds=p1, limit=50)
    assert {i["description"] for i in with_project["items"]} == {"road_mine.jpg"}


def test_unknown_sort_key_falls_back_instead_of_erroring(client, alice):
    """A non-column attribute name must not reach order_by.

    Clicking a column header supplies the sort key, so it is user input. A bare
    getattr fell back only for names Task has no attribute for at all —
    "annotations" resolved to a relationship and raised in order_by.
    """
    p1 = _make_project(client, alice, "Road Survey", "road-survey")
    _seed_tasks(p1, ["road_0001.jpg"])

    for key in ("annotations", "metadata", "not_a_column"):
        data = _search(client, alice, sort_by=key, limit=50)
        assert data["total"] == 1, f"sort_by={key} should fall back, not fail"


def test_sorting_by_a_whitelisted_column_orders_results(client, alice):
    p1 = _make_project(client, alice, "Road Survey", "road-survey")
    _seed_tasks(p1, ["c.jpg", "a.jpg", "b.jpg"])

    data = _search(client, alice, sort_by="description", sort_desc="false", limit=50)

    assert [i["description"] for i in data["items"]] == ["a.jpg", "b.jpg", "c.jpg"]


def test_pagination_reports_the_full_cross_project_total(client, alice):
    """`total` counts every match, not just the page — the pager depends on it."""
    p1 = _make_project(client, alice, "Road Survey", "road-survey")
    p2 = _make_project(client, alice, "Warehouse QA", "warehouse-qa")
    _seed_tasks(p1, [f"a_{i}.jpg" for i in range(3)])
    _seed_tasks(p2, [f"b_{i}.jpg" for i in range(3)])

    data = _search(client, alice, limit=2, offset=0)

    assert data["total"] == 6
    assert len(data["items"]) == 2
    # Every page of a cross-project sweep is labelled, not just the first.
    second = _search(client, alice, limit=2, offset=2)
    assert all(item["project_name"] for item in second["items"])

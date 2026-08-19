"""Completion statistics: what counts as "done".

Completion is measured on the **approved group** (Approved / Verified / Checked
/ Passed), not on 'Completed'. The distinction is the point: 'Completed' is the
annotator's own claim that they have finished, while an approved-group status is
a reviewer's verdict. Counting the former as done let a project read 100%
complete when not one task had been reviewed.

'Completed' is still reported, as `awaiting_review`, so the review queue stays
visible instead of being folded into progress nobody has checked.

Covers GET /api/projects/{id}/metrics, the same counts merged into
GET /api/projects, and the project status derived on task update.
"""
import pytest

import models
import schemas
from database import SessionLocal


def _project(client, headers, name="Metrics"):
    return client.post(
        "/api/projects",
        json={"name": name, "slug": name.lower(), "creator": "ignored"},
        headers=headers,
    ).json()["id"]


def _task(client, headers, project_id, status):
    return client.post(
        f"/api/tasks?projectId={project_id}",
        json={"description": f"{status}.jpg", "status": status},
        headers=headers,
    ).json()["id"]


def _metrics(client, headers, project_id):
    res = client.get(f"/api/projects/{project_id}/metrics", headers=headers)
    assert res.status_code == 200, res.text
    return res.json()


# --- what counts as complete -------------------------------------------------


@pytest.mark.parametrize("status", schemas.APPROVED_STATUSES)
def test_every_approved_status_counts_as_complete(client, alice, status):
    """The core of feature 2: a task approved into *any* batch is done. A batch
    missing from this count would make the week's progress appear to go
    backwards the moment the team switched batch names."""
    project_id = _project(client, alice)
    _task(client, alice, project_id, status)

    m = _metrics(client, alice, project_id)

    assert m["total"] == 1
    assert m["completed"] == 1
    assert m["progress"] == 100


def test_completed_is_not_counted_as_complete(client, alice):
    """'Completed' means submitted, not signed off."""
    project_id = _project(client, alice)
    _task(client, alice, project_id, "Completed")

    m = _metrics(client, alice, project_id)

    assert m["completed"] == 0
    assert m["awaiting_review"] == 1
    assert m["progress"] == 0


def test_mixed_batches_all_count_toward_one_percentage(client, alice):
    """Approving across several weeks under different batch names must still add
    up to one project-level number."""
    project_id = _project(client, alice)
    for status in ("Approved", "Verified", "Checked", "Passed"):
        _task(client, alice, project_id, status)
    _task(client, alice, project_id, "Completed")   # awaiting review
    _task(client, alice, project_id, "In Progress")
    _task(client, alice, project_id, "New")
    _task(client, alice, project_id, "Rejected")

    m = _metrics(client, alice, project_id)

    assert m["total"] == 8
    assert m["completed"] == 4
    assert m["awaiting_review"] == 1
    assert m["in_progress"] == 1
    assert m["progress"] == 50


def test_rejected_never_counts_as_complete(client, alice):
    """A rejection is a review verdict, but the opposite one."""
    project_id = _project(client, alice)
    _task(client, alice, project_id, "Rejected")

    m = _metrics(client, alice, project_id)

    assert m["completed"] == 0
    assert m["progress"] == 0


def test_empty_project_reports_zero_not_a_division_error(client, alice):
    project_id = _project(client, alice)

    m = _metrics(client, alice, project_id)

    assert (m["total"], m["completed"], m["progress"]) == (0, 0, 0)


# --- the same counts on the list endpoint ------------------------------------


def test_projects_list_uses_the_same_definition(client, alice):
    """The list page and the Overview page must not disagree about progress —
    they read the same aggregate, and this pins that."""
    project_id = _project(client, alice, name="Listed")
    _task(client, alice, project_id, "Verified")
    _task(client, alice, project_id, "Completed")

    rows = client.get("/api/projects", headers=alice).json()
    row = next(r for r in rows if r["id"] == project_id)

    assert row["completed"] == 1
    assert row["awaiting_review"] == 1
    assert row["progress"] == 50


# --- derived project status --------------------------------------------------


def test_project_status_derives_from_approval_not_submission(client, alice):
    """The status written on task update uses the same definition as the
    metrics. A project whose tasks are all merely 'Completed' is not done."""
    project_id = _project(client, alice)
    task_id = _task(client, alice, project_id, "New")

    client.post(
        "/api/tasks", json={"id": task_id, "status": "Completed"}, headers=alice
    )
    with SessionLocal() as db:
        assert db.get(models.Project, project_id).status != "Completed"

    client.post(
        f"/api/tasks/{task_id}/review", json={"action": "verified"}, headers=alice
    )
    with SessionLocal() as db:
        assert db.get(models.Project, project_id).status == "Completed"


def test_partially_approved_project_is_in_progress(client, alice):
    project_id = _project(client, alice)
    first = _task(client, alice, project_id, "New")
    _task(client, alice, project_id, "New")

    client.post(
        f"/api/tasks/{first}/review", json={"action": "checked"}, headers=alice
    )

    with SessionLocal() as db:
        assert db.get(models.Project, project_id).status == "In Progress"


# --- per-status detail (the dashboard tiles) ---------------------------------
#
# `completed` above is the whole approved *group*. `by_status` splits it back
# into its export batches so the Home dashboard can show one card per batch:
# collapsing Approved / Verified / Checked / Passed into a single number hid
# exactly the distinction those names exist to make (CLAUDE.md rule 11a).
# See .devnotes/dashboard-cards/PLAN.md.


def test_by_status_splits_the_approved_group(client, alice):
    """The regression this feature exists for. Four tasks, one per batch name:
    the group total is still 4, but each batch must report its own 1 rather than
    the dashboard showing a single undifferentiated "Approved: 4"."""
    project_id = _project(client, alice)
    for status in schemas.APPROVED_STATUSES:
        _task(client, alice, project_id, status)

    m = _metrics(client, alice, project_id)

    assert m["completed"] == 4
    assert m["progress"] == 100
    for status in schemas.APPROVED_STATUSES:
        assert m["by_status"][status] == 1, f"{status} lost its own count"


def test_by_status_counts_batches_independently(client, alice):
    """A lopsided week must not average out across the group."""
    project_id = _project(client, alice)
    for _ in range(3):
        _task(client, alice, project_id, "Approved")
    _task(client, alice, project_id, "Checked")

    m = _metrics(client, alice, project_id)

    assert m["by_status"]["Approved"] == 3
    assert m["by_status"]["Checked"] == 1
    assert m["by_status"]["Verified"] == 0
    assert m["completed"] == 4


def test_by_status_covers_the_whole_vocabulary(client, alice):
    """Every status is a key even at zero. An absent key would render as
    `undefined` in the dashboard tile rather than as 0."""
    project_id = _project(client, alice)
    _task(client, alice, project_id, "New")

    m = _metrics(client, alice, project_id)

    assert set(m["by_status"]) == set(schemas.TASK_STATUSES)
    assert m["by_status"]["New"] == 1
    assert all(m["by_status"][s] == 0 for s in schemas.TASK_STATUSES if s != "New")


def test_empty_project_reports_every_status_at_zero(client, alice):
    project_id = _project(client, alice)

    m = _metrics(client, alice, project_id)

    assert m["total"] == 0
    assert m["by_status"] == {s: 0 for s in schemas.TASK_STATUSES}


def test_by_status_cannot_drift_from_the_scalar_counts(client, alice):
    """The map and the scalars are two views of one loop; if they ever disagree
    the dashboard and the completion bar tell the user different stories."""
    project_id = _project(client, alice)
    for status in ("Approved", "Verified", "Completed", "Completed",
                   "In Progress", "New", "Rejected"):
        _task(client, alice, project_id, status)

    m = _metrics(client, alice, project_id)

    assert sum(m["by_status"].values()) == m["total"] == 7
    assert m["by_status"]["Completed"] == m["awaiting_review"] == 2
    assert m["by_status"]["In Progress"] == m["in_progress"] == 1
    assert sum(m["by_status"][s] for s in schemas.APPROVED_STATUSES) == m["completed"]


def test_by_status_does_not_leak_between_projects(client, alice):
    """`_aggregate_metrics` counts several projects in one query, so a shared
    accumulator would show one project's work on another's dashboard."""
    first = _project(client, alice, name="First")
    second = _project(client, alice, name="Second")
    _task(client, alice, first, "Approved")
    _task(client, alice, second, "Verified")
    _task(client, alice, second, "Verified")

    assert _metrics(client, alice, first)["by_status"]["Approved"] == 1
    assert _metrics(client, alice, first)["by_status"]["Verified"] == 0
    assert _metrics(client, alice, second)["by_status"]["Verified"] == 2
    assert _metrics(client, alice, second)["by_status"]["Approved"] == 0

    # The same counts must arrive on the projects list, which merges the very
    # same aggregate into each row.
    rows = {p["name"]: p for p in client.get("/api/projects", headers=alice).json()}
    assert rows["First"]["by_status"]["Approved"] == 1
    assert rows["Second"]["by_status"]["Verified"] == 2


def test_dashboard_count_matches_the_table_the_tile_opens(client, alice):
    """Feature 2's contract: a tile shows N and links to the tasks view filtered
    by that status, so the filtered list must return exactly N rows. If these
    two ever diverge the user clicks "Verified: 3" and counts 5."""
    project_id = _project(client, alice)
    for status in ("Approved", "Verified", "Verified", "Completed",
                   "In Progress", "New", "New", "New", "Rejected"):
        _task(client, alice, project_id, status)

    m = _metrics(client, alice, project_id)

    for status in schemas.TASK_STATUSES:
        res = client.get(
            f"/api/tasks?projectId={project_id}&status={status}&pageSize=100",
            headers=alice,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        rows = body["items"] if isinstance(body, dict) else body
        assert len(rows) == m["by_status"][status], (
            f"{status}: dashboard says {m['by_status'][status]}, table has {len(rows)}"
        )

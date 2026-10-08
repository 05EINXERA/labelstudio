"""`GET /api/projects` carries each project's per-status task counts.

The projects page sums these into its workspace-wide overview, so the field
must survive the ProjectSummary response model, and a NULL task status must be
reported as "New" rather than breaking the Dict[str, int] schema.
"""
import pytest

import models
from database import SessionLocal


def test_project_list_includes_status_counts(client, alice):
    res = client.post(
        "/api/projects",
        json={"name": "Status Counts", "slug": "status-counts", "creator": "x"},
        headers=alice,
    )
    assert res.status_code in (200, 201), res.text
    project_id = res.json()["id"]

    with SessionLocal() as db:
        for i, status in enumerate(["New", "Completed", "Completed", None]):
            db.add(models.Task(project_id=project_id, image_path=f"{i}.jpg", status=status, time_spent=0))
        db.commit()

    res = client.get("/api/projects", headers=alice)
    assert res.status_code == 200, res.text
    row = next(p for p in res.json() if p["id"] == project_id)
    assert row["status_counts"] == {"New": 2, "Completed": 2}
    assert row["total"] == 4
    assert row["completed"] == 2


def _project_with_tasks(client, auth, slug, statuses):
    res = client.post(
        "/api/projects",
        json={"name": slug, "slug": slug, "creator": "x"},
        headers=auth,
    )
    assert res.status_code in (200, 201), res.text
    project_id = res.json()["id"]
    with SessionLocal() as db:
        for i, status in enumerate(statuses):
            db.add(models.Task(project_id=project_id, image_path=f"{i}.jpg", status=status, time_spent=0))
        db.commit()
    return project_id


def test_progress_counts_review_outcomes_as_completed(client, alice):
    """Progress is done tasks / total, where done is more than 'Completed'."""
    done = ["Completed", "Approved", "Verified", "Monitored", "Passed", "Reviewed", "Checked"]
    not_done = ["New", "In Progress", "Declined"]
    project_id = _project_with_tasks(client, alice, "progress-done", done + not_done)

    row = next(p for p in client.get("/api/projects", headers=alice).json() if p["id"] == project_id)
    assert row["total"] == 10
    assert row["completed"] == 7
    assert row["progress"] == 70

    m = client.get(f"/api/projects/{project_id}/metrics", headers=alice).json()
    assert m["completed"] == 7
    assert m["progress"] == 70


@pytest.mark.parametrize("status", ["Approved", "Verified", "Monitored", "Passed", "Reviewed", "Checked"])
def test_review_outcome_on_last_task_completes_the_project(client, alice, status):
    """The save-path status rollup uses the same definition of done."""
    project_id = _project_with_tasks(client, alice, f"rollup-{status.lower()}", ["Completed", "New"])
    with SessionLocal() as db:
        task_id = db.query(models.Task.id).filter(
            models.Task.project_id == project_id, models.Task.status == "New"
        ).scalar()

    res = client.patch(f"/api/tasks/{task_id}", json={"status": status}, headers=alice)
    assert res.status_code == 200, res.text

    with SessionLocal() as db:
        assert db.query(models.Project.status).filter(models.Project.id == project_id).scalar() == "Completed"

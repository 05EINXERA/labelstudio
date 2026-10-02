"""`GET /api/tasks?status=...` matches what the Home status tiles count.

Each Home tile links to the task table filtered by its status. The metrics
count a NULL status as "New", so the "New" filter must include NULL rows or
the table would show fewer tasks than the tile reports.
"""
import models
from database import SessionLocal


def _project_with_tasks(client, headers, statuses):
    res = client.post(
        "/api/projects",
        json={"name": "Status Filter", "slug": "status-filter", "creator": "x"},
        headers=headers,
    )
    assert res.status_code in (200, 201), res.text
    project_id = res.json()["id"]
    with SessionLocal() as db:
        for i, status in enumerate(statuses):
            db.add(models.Task(project_id=project_id, image_path=f"{i}.jpg", status=status, time_spent=0))
        db.commit()
    return project_id


def test_new_filter_includes_null_status(client, alice):
    project_id = _project_with_tasks(client, alice, ["New", None, "Completed"])

    res = client.get(f"/api/tasks?projectId={project_id}&status=New", headers=alice)
    assert res.status_code == 200, res.text
    assert res.json()["total"] == 2


def test_other_status_filters_are_exact(client, alice):
    project_id = _project_with_tasks(client, alice, ["New", None, "Completed", "Completed"])

    res = client.get(f"/api/tasks?projectId={project_id}&status=Completed", headers=alice)
    assert res.status_code == 200, res.text
    assert res.json()["total"] == 2

    res = client.get(f"/api/tasks?projectId={project_id}&status=All", headers=alice)
    assert res.json()["total"] == 4

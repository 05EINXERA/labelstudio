"""`GET /api/projects` carries each project's per-status task counts.

The projects page sums these into its workspace-wide overview, so the field
must survive the ProjectSummary response model, and a NULL task status must be
reported as "New" rather than breaking the Dict[str, int] schema.
"""
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

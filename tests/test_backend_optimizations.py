PS C:\labelstudio> .\scripts\schedule-backup.ps1 -Dest "D:\annotation-backups" -Keep 7
Register-ScheduledTask: C:\labelstudio\scripts\schedule-backup.ps1:74:1
Line |
  74 |  Register-ScheduledTask `
     |  ~~~~~~~~~~~~~~~~~~~~~~~~
     | Access is denied. 
✓ Scheduled task 'AnnotationBackup' registered — runs daily at 17:00.
  Backup destination : D:\annotation-backups
  Snapshots to keep  : 7

Verify with:  Get-ScheduledTask -TaskName 'AnnotationBackup' | Get-ScheduledTaskInfo 
Test run  :   Start-ScheduledTask -TaskName 'AnnotationBackup'
PS C:\labelstudio> python scripts/create_user.py karuna --resetimport pytest
from sqlalchemy.orm import Session
from sqlalchemy import inspect

import models
from database import SessionLocal
from api.routers.projects import _aggregate_metrics


def test_aggregate_metrics_accuracy():
    """Verify optimized SQL GROUP BY aggregate metrics calculation."""
    db = SessionLocal()
    try:
        user = models.User(username="perf_user_metric", hashed_password="pw")
        db.add(user)
        db.commit()

        proj = models.Project(name="Perf Proj", slug="perf-proj", type="image", status="Active", owner_id=user.id)
        db.add(proj)
        db.commit()

        # Add tasks with varying statuses and times
        t1 = models.Task(project_id=proj.id, image_path="t1.jpg", status="Completed", time_spent=30)
        t2 = models.Task(project_id=proj.id, image_path="t2.jpg", status="Completed", time_spent=50)
        t3 = models.Task(project_id=proj.id, image_path="t3.jpg", status="In Progress", time_spent=20)
        t4 = models.Task(project_id=proj.id, image_path="t4.jpg", status="New", time_spent=0)
        db.add_all([t1, t2, t3, t4])

        # Add labels
        l1 = models.Label(id="lbl-perf-1", project_id=proj.id, name="Cat", color="#ff0000")
        l2 = models.Label(id="lbl-perf-2", project_id=proj.id, name="Dog", color="#00ff00")
        db.add_all([l1, l2])
        db.commit()

        metrics = _aggregate_metrics([proj.id], db)
        proj_metrics = metrics[proj.id]

        assert proj_metrics["total"] == 4
        assert proj_metrics["completed"] == 2
        assert proj_metrics["in_progress"] == 1
        assert proj_metrics["progress"] == 50  # 2/4 = 50%
        assert proj_metrics["total_time"] == 100
        assert proj_metrics["avg_time_per_task"] == 25  # 100/4 = 25
        assert proj_metrics["classes"] == 2
    finally:
        db.close()


def test_database_indexes_declared():
    """Verify performance indexes are declared on models."""
    assert models.Task.assignee.index is True
    assert models.Task.status.index is True
    assert models.Project.creator.index is True
    assert models.Project.team_id.index is True
    assert models.TaskLock.claimed_at.index is True
    assert models.AIJob.created_at.index is True

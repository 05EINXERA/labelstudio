with open("api/routers/tasks.py", "r", encoding="utf-8") as f:
    src = f.read()

old_func = """def _get_owned_task(task_id: int, user: models.User, db: Session, annotator: Optional[models.TeamMember] = None) -> models.Task:
    \"\"\"Return the task if it belongs to a project `user` can access, else 404.\"\"\"
    proj_ids = _accessible_project_ids(user, db, annotator)
    task = (
        db.query(models.Task)
        .filter(models.Task.id == task_id, models.Task.project_id.in_(proj_ids))
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
        
    return task"""

new_func = """def _get_owned_task(task_id: int, user: models.User, db: Session, annotator: Optional[models.TeamMember] = None) -> models.Task:
    \"\"\"Return the task if it belongs to a project `user` can access, else 404.\"\"\"
    proj_ids = _accessible_project_ids(user, db, annotator)
    task = (
        db.query(models.Task)
        .filter(models.Task.id == task_id, models.Task.project_id.in_(proj_ids))
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
        
    if annotator and task.assignee and task.assignee != annotator.name:
        raise HTTPException(status_code=403, detail="Task is assigned to another user")
        
    return task"""

src = src.replace(old_func, new_func)

with open("api/routers/tasks.py", "w", encoding="utf-8") as f:
    f.write(src)

with open("app/schemas.py", "r", encoding="utf-8") as f:
    src = f.read()

task_list_item = """class TaskListItem(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    description: Optional[str] = None
    assignee: Optional[str] = None
    image_path: Optional[str] = None
    status: Optional[str] = None
    time_spent: Optional[int] = None
    updated_at: Optional[datetime] = None
    comment_count: int = 0
    class_count: int = 0

class PaginatedTasks(BaseModel):
    items: List[TaskListItem]
    total: int
    limit: int
    offset: int"""

src = src.replace("""class PaginatedTasks(BaseModel):
    items: List[TaskDetail]
    total: int
    limit: int
    offset: int""", task_list_item)

with open("app/schemas.py", "w", encoding="utf-8") as f:
    f.write(src)

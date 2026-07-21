from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

# Upper bound on a single reported time delta. Clients sync far more often than
# once a day; anything larger is a bug or a forged payload. See
# docs/TIMER_AUDIT.md F9.
MAX_TIME_DELTA_SECONDS = 86400

class WorkspaceData(BaseModel):
    key: str
    value: str

class ProjectModel(BaseModel):
    name: str
    slug: str
    type: str = "Image - Polygon"
    creator: str
    assignee: Optional[str] = None

class ProjectUpdate(BaseModel):
    id: int
    name: Optional[str] = None
    status: Optional[str] = None
    assignee: Optional[str] = None

class TaskUpdate(BaseModel):
    id: Optional[int] = None
    assignee: Optional[str] = None
    status: Optional[str] = "New"
    description: Optional[str] = None
    time_spent_delta: Optional[int] = Field(0, ge=0, le=MAX_TIME_DELTA_SECONDS)
    annotations: Optional[str] = None
    updated_at: Optional[str] = None

class ProjectMetrics(BaseModel):
    total: int
    completed: int
    progress: int
    comments: int
    # Seconds aggregated from Task.time_spent. See docs/TIMER_AUDIT.md F12.
    total_time: int = 0
    avg_time_per_task: int = 0
    status: Optional[str] = None

class BulkDelete(BaseModel):
    ids: List[int]

class BulkUpdate(BaseModel):
    ids: List[int]
    assignee: Optional[str] = None
    status: Optional[str] = None

class TeamMemberModel(BaseModel):
    name: str

class TeamTime(BaseModel):
    name: str
    time_logged: int = Field(..., ge=0, le=MAX_TIME_DELTA_SECONDS)

class DetectPayload(BaseModel):
    image: str
    selection: Optional[dict] = None
    prompts: Optional[List[str]] = None
    model_size: Optional[str] = None
    confidence: Optional[float] = None
    nms_threshold: Optional[float] = None

class ClassifyPayload(BaseModel):
    image: str
    selection: Optional[dict] = None

class PointModel(BaseModel):
    x: float
    y: float

class SegmentPayload(BaseModel):
    image: str
    points: List[PointModel]
    labels: List[int]
    prompt: Optional[str] = None
    precision: Optional[float] = 0.001
    bbox: Optional[List[float]] = None
    sam_model: Optional[str] = None

class LabelStudioPayload(BaseModel):
    projectId: Optional[str] = None
    taskId: Optional[str] = None
    taskData: Optional[dict] = None
    result: Optional[list] = None

class LabelModel(BaseModel):
    id: str
    name: str
    color: str
    projectId: int

class UserCreate(BaseModel):
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

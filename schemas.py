from typing import Any, Dict, List, Optional

from pydantic import BaseModel


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
    time_spent_delta: Optional[int] = 0
    annotations: Optional[str] = None
    updated_at: Optional[str] = None


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
    time_logged: int


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


class UserCreate(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str

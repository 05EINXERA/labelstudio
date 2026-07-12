from typing import Optional, List, Dict, Any
from pydantic import BaseModel

class WorkspaceData(BaseModel):
    key: str
    value: str

class ProjectModel(BaseModel):
    name: str
    slug: str
    type: str = "Image - Polygon"
    creator: str

class TaskUpdate(BaseModel):
    id: Optional[int] = None
    assignee: Optional[str] = None
    status: Optional[str] = "New"
    description: Optional[str] = None
    time_spent_delta: Optional[int] = 0
    annotations: Optional[str] = None

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
    selection: Optional[List[dict]] = None

class ClassifyPayload(BaseModel):
    image: str

class PointModel(BaseModel):
    x: float
    y: float

class SegmentPayload(BaseModel):
    image: str
    point: PointModel
    prompt: Optional[str] = None

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

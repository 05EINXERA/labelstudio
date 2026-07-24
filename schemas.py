from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator

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
    # Optional: PATCH /api/projects/{id} takes the id from the path. The legacy
    # POST /api/projects/update requires it in the body.
    id: Optional[int] = None
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

class ProjectSummary(BaseModel):
    """A project plus its task metrics — one row of the projects list.

    Metrics are merged in so the list page needs a single request instead of
    pairing /api/projects with /api/projects/metrics/batch.
    """
    id: int
    name: Optional[str] = None
    slug: Optional[str] = None
    type: Optional[str] = None
    status: Optional[str] = None
    creator: Optional[str] = None
    assignee: Optional[str] = None
    created_at: Optional[datetime] = None
    total: int = 0
    completed: int = 0
    in_progress: int = 0
    progress: int = 0
    comments: int = 0
    classes: int = 0
    total_time: int = 0
    avg_time_per_task: int = 0

class ProjectMetrics(BaseModel):
    total: int
    completed: int
    progress: int
    comments: int
    # Seconds aggregated from Task.time_spent. See docs/TIMER_AUDIT.md F12.
    total_time: int = 0
    avg_time_per_task: int = 0
    status: Optional[str] = None
    in_progress: int = 0
    classes: int = 0

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

class LabelBulkUpsert(BaseModel):
    projectId: int
    labels: List[LabelModel]

class LabelBulkDelete(BaseModel):
    projectId: int
    ids: List[str]

class LabelBulkResult(BaseModel):
    status: str = "ok"
    created: int = 0
    updated: int = 0

class LabelImportResult(BaseModel):
    status: str = "ok"
    created: int = 0
    updated: int = 0
    skipped: int = 0
    labels: List[LabelModel] = Field(default_factory=list)

# Fixed task-status vocabulary shared by the export filter and the Tasks view.
# 'Approved' added in Phase 3 (tracker P3.2): owner-only, enforced by
# _get_owned_task rather than a separate check (single-owner projects).
TASK_STATUSES = ["New", "In Progress", "Completed", "Approved"]

# Export "include" options actually implemented. Mask rendering and image
# bundling are explicit TODOs (see REFACTOR_MANAGEMENT.md §3 Phase 4) — the
# API rejects them rather than silently ignoring the request.
EXPORT_INCLUDE_OPTIONS = ["annotations_only"]

# Canonical export format codes.
#
# "coco" and "annotations_json" are both JSON but are different documents: COCO
# is {images, categories, annotations}, while annotations_json is an array of
# task objects. The old code called the former "json", which left no name for
# the latter — hence the rename.
EXPORT_FORMATS = [
    "coco",                 # COCO JSON, one file
    "annotations_json",     # array of task objects, one file
    "annotations_pertask",  # ZIP of one task object per file
    "yolo",                 # ZIP: classes.txt + annotations/<stem>.txt
    "masks_direct",         # ZIP of RGB PNG masks (pixel = colour)
    "masks_index",          # ZIP of palette PNG masks (pixel = index)
    "csv",                  # flat CSV
]

# Deprecated spellings, still accepted so existing clients and bookmarked UI
# state keep working. Resolved to the canonical code before validation.
EXPORT_FORMAT_ALIASES = {
    "json": "coco",
    "pertask": "annotations_pertask",
}


def canonical_export_format(value: str) -> str:
    """Resolve a possibly-deprecated format code to its canonical spelling."""
    return EXPORT_FORMAT_ALIASES.get(value, value)


class ExportRequest(BaseModel):
    projectId: int
    format: str = "coco"
    # None/omitted means "all statuses".
    statusFilter: Optional[List[str]] = None
    include: str = "annotations_only"

    @field_validator("format")
    @classmethod
    def _resolve_format_alias(cls, v: str) -> str:
        # Normalized here rather than at each use site, so nothing downstream
        # has to know the deprecated spellings exist.
        return canonical_export_format(v)


class ExportJobStatus(BaseModel):
    status: str  # pending | completed | failed
    error: Optional[str] = None
    task_count: Optional[int] = None


class UserCreate(BaseModel):
    username: str
    password: str

class Token(BaseModel):
    access_token: str
    token_type: str

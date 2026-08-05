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
    team_id: Optional[int] = None

class ProjectUpdate(BaseModel):
    # Optional: PATCH /api/projects/{id} takes the id from the path. The legacy
    # POST /api/projects/update requires it in the body.
    id: Optional[int] = None
    name: Optional[str] = None
    status: Optional[str] = None
    team_id: Optional[int] = None

class ProjectTransferOwnership(BaseModel):
    new_owner: str = Field(min_length=1, max_length=64)

class TaskUpdate(BaseModel):
    id: Optional[int] = None
    assignee: Optional[str] = None
    status: Optional[str] = "New"
    description: Optional[str] = None
    time_spent_delta: Optional[int] = Field(0, ge=0, le=MAX_TIME_DELTA_SECONDS)
    annotations: Optional[str] = None
    updated_at: Optional[str] = None
    # Identifies the browser tab that produced this write. Conflict detection
    # compares it against the last writer so a client never 409s against its
    # own previous save — the overwhelmingly common case, since one tab
    # autosaves, flushes a beacon on tab-switch and drains the timer, all
    # against the same task. Only a *different* client is a real conflict.
    # See .devnotes/deployment-hardening/04_ANNOTATION_SAVE_LOSS.md.
    client_id: Optional[str] = Field(None, max_length=64)

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
    team_id: Optional[int] = None
    team_name: Optional[str] = None
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

class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)

class TeamUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=64)

class TeamTransferOwnership(BaseModel):
    new_owner: str = Field(min_length=1, max_length=64)

class TeamResponse(BaseModel):
    id: int
    name: str
    creator: Optional[str] = None
    created_at: datetime

class TeamMemberModel(BaseModel):
    name: str

class TeamMemberCreate(BaseModel):
    name: str
    team_ids: List[int] = Field(default_factory=list)

class TeamMemberResponse(BaseModel):
    name: str
    time_logged: int
    teams: List[TeamResponse] = Field(default_factory=list)
    is_logged_in: Optional[bool] = None
    last_active_at: Optional[datetime] = None

class TeamTimeResponse(BaseModel):
    status: str
    time_logged: int

class TeamTime(BaseModel):
    name: str
    time_logged: int = Field(..., ge=0, le=MAX_TIME_DELTA_SECONDS)

class TeamMemberAssign(BaseModel):
    team_ids: List[int] = Field(default_factory=list)

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

class EmbedPayload(BaseModel):
    image: str
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

# An export is two independent axes: an annotation FORMAT and an IMAGE OUTPUT,
# bundled into one project-named ZIP. Either axis can be chosen without the
# other (image output "none" = annotations only, the historical behaviour).
# See .devnotes/data-refactor/02_IMAGE_OUTPUT_PLAN.md.

# Axis A — annotation format.
#
# "coco" and "annotations_json" are both JSON but are different documents: COCO
# is {images, categories, annotations}, while annotations_json is an array of
# task objects. The old code called the former "json", which left no name for
# the latter — hence the rename.
EXPORT_FORMATS = [
    "coco",                 # COCO JSON, one file
    "annotations_json",     # array of task objects, one file
    "annotations_pertask",  # one task object per file
    "yolo",                 # classes.txt + annotations/<stem>.txt
    "csv",                  # flat CSV (deprecated: dropped from the UI, still accepted)
]

# Axis B — image output. "none" writes no images (annotations only).
IMAGE_OUTPUTS = [
    "none",
    "original",     # the uploaded image, unchanged
    "annotated",    # the image with the committed annotations drawn on it
    "mask_direct",  # RGB PNG masks (pixel = class/instance colour)
    "mask_index",   # palette PNG masks (pixel = class/instance index)
    "mask_binary",  # 8-bit grayscale masks (annotated pixel = 255)
]

# Deprecated single-axis format spellings, still accepted so existing clients
# and bookmarked UI state keep working. Each maps to a (format, imageOutput)
# pair. The two standalone mask "formats" are now an image output combined with
# a default annotation format.
EXPORT_FORMAT_ALIASES = {
    "json": ("coco", "none"),
    "pertask": ("annotations_pertask", "none"),
    "masks_direct": ("coco", "mask_direct"),
    "masks_index": ("coco", "mask_index"),
}


def resolve_export_request(fmt: str, image_output: Optional[str]) -> tuple:
    """Resolve a possibly-deprecated (format, imageOutput) into the two-axis
    canonical pair.

    A deprecated single-axis format code (e.g. "masks_index") expands to its
    pair and *wins* over an omitted/none image output, so an old client that
    only sends `format=masks_index` still gets masks. An explicit image output
    on a canonical format is passed through unchanged.
    """
    if fmt in EXPORT_FORMAT_ALIASES:
        canon_fmt, alias_image = EXPORT_FORMAT_ALIASES[fmt]
        # The alias' image output applies only when the caller didn't ask for
        # one; a caller pairing a legacy format with an explicit image output
        # keeps their choice.
        resolved_image = image_output if image_output and image_output != "none" else alias_image
        return canon_fmt, resolved_image
    return fmt, (image_output or "none")


class ExportRequest(BaseModel):
    projectId: int
    format: str = "coco"
    imageOutput: str = "none"
    # None/omitted means "all statuses".
    statusFilter: Optional[List[str]] = None
    include: str = "annotations_only"


class ExportJobStatus(BaseModel):
    status: str  # pending | completed | failed
    error: Optional[str] = None
    task_count: Optional[int] = None


class TaskDetail(BaseModel):
    """Single-task response for GET /api/tasks/{id} — includes annotations.

    The list endpoint (GET /api/tasks) returns annotation-free rows for the
    gallery shell; this endpoint hydrates the one task that was actually
    opened, keeping the initial page load small (T1.1).
    """
    model_config = {"from_attributes": True}

    id: int
    description: Optional[str] = None
    assignee: Optional[str] = None
    image_path: Optional[str] = None
    status: Optional[str] = None
    time_spent: Optional[int] = None
    updated_at: Optional[datetime] = None
    annotations: List[Any] = Field(default_factory=list)


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str

class UserResponse(BaseModel):
    username: str

class Token(BaseModel):
    access_token: str
    token_type: str
    # Double-submit CSRF token, also set as a readable cookie. Returned in the
    # body so a non-browser client (tests, scripts) can echo it back without
    # having to parse Set-Cookie.
    csrf_token: Optional[str] = None

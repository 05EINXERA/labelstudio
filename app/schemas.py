from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, field_validator

import json

# Upper bound on a single reported time delta. Clients sync far more often than
# once a day; anything larger is a bug or a forged payload. See
# docs/TIMER_AUDIT.md F9.
MAX_TIME_DELTA_SECONDS = 86400

class AnnotationModel(BaseModel):
    model_config = {"from_attributes": True, "populate_by_name": True, "extra": "allow"}

    id: str
    type: str
    label_id: Optional[str] = Field(None, alias="labelId")
    points: Optional[List[dict]] = None
    x: Optional[float] = None
    y: Optional[float] = None
    width: Optional[float] = None
    height: Optional[float] = None
    text: Optional[str] = None
    color: Optional[str] = None
    order: Optional[int] = None
    group_id: Optional[str] = Field(None, alias="groupId")
    extra: Optional[dict] = None

    @field_validator("points", "extra", mode="before")
    @classmethod
    def parse_json_fields(cls, v):
        if isinstance(v, str):
            try:
                return json.loads(v)
            except ValueError:
                return None
        return v

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

class ProjectReviewerCreate(BaseModel):
    """Appoint one annotator as a reviewer of a project.

    The name is a `team_members.name`, the same identity task assignment uses —
    not a login, because the deployment shares one account.
    """
    member_name: str = Field(..., min_length=1, max_length=200)

class ProjectReviewerResponse(BaseModel):
    member_name: str
    appointed_by: Optional[str] = None
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

class ProjectTransferOwnership(BaseModel):
    new_owner: str = Field(min_length=1, max_length=64)

class TaskUpdate(BaseModel):
    id: Optional[int] = None
    # The primary assignee, kept for clients that predate multi-assignment
    # (older tabs running cached JS against the live server still send only
    # this). `assignees` wins when both are present; see _incoming_assignees in
    # api/routers/tasks.py.
    assignee: Optional[str] = None
    # The full assignee set. An empty list means unassign everyone, which is a
    # real instruction; omitting the field entirely leaves assignment alone,
    # which is what an autosave does.
    assignees: Optional[List[str]] = None
    status: Optional[str] = None
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
    # Set only by a deliberate destructive action the annotator took on a
    # canvas they could see (Clear all). The wipe guard cannot tell such a save
    # from a tab that opened without its annotations and autosaved the empty
    # canvas — both arrive as an empty list — so it refused both, and clearing
    # a task by hand was impossible. An explicit intent separates them: a user
    # who can see the shapes they are deleting is allowed to delete them, while
    # every *automatic* save stays guarded.
    intent: Optional[str] = Field(None, max_length=32)

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
    # The caller's own standing, so the list can badge the role; see
    # api/routers/projects.is_project_reviewer.
    is_reviewer: bool = False
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
    status_counts: Dict[str, int] = Field(default_factory=dict)

class BulkDelete(BaseModel):
    ids: List[int]

class BulkUpdate(BaseModel):
    ids: List[int]
    # Legacy single-assignee form: replaces the whole set with this one name.
    assignee: Optional[str] = None
    # Replace each task's assignee set with exactly these names.
    assignees: Optional[List[str]] = None
    # Add these names to each task's existing set, keeping who is already on it.
    # "Also give these images to X" cannot be expressed by a replacing assign,
    # and doing it as a read-modify-write in the client would race other writers.
    add_assignees: Optional[List[str]] = None
    status: Optional[str] = None

class TaskMoveSkip(BaseModel):
    """One task that was left behind by a move, and why."""
    taskId: int
    reason: str

class TaskMove(BaseModel):
    taskIds: List[int] = Field(min_length=1, max_length=1000)
    targetProjectId: int

class TaskMoveResult(BaseModel):
    """Outcome of POST /api/tasks/move.

    `labelsCreated` counts classes that had to be added to the destination
    because no class of that name existed there; `labelsRemapped` counts
    annotations whose label_id was repointed at the destination's own class
    row. Together they are what keeps a moved annotation's *meaning* intact —
    see the module docstring on `move_tasks`.
    """
    status: str = "ok"
    moved: int
    labelsCreated: int
    labelsRemapped: int
    skipped: List[TaskMoveSkip] = Field(default_factory=list)

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
    # Seconds spent logged in during the caller's local day, from LoginSession.
    seconds_today: Optional[int] = None
    # True while the member has an open break (MemberBreak with no ended_at).
    on_break: Optional[bool] = None
    # Seconds spent on break during the caller's local day.
    break_seconds_today: Optional[int] = None

class BreakEntry(BaseModel):
    id: int
    started_at: datetime
    # None while the break is still in progress.
    ended_at: Optional[datetime] = None
    # 'resumed' (clicked End break), 'logout', or 'inactive' (tab closed).
    ended_reason: Optional[str] = None
    duration_seconds: int
    is_open: bool

class BreakStatus(BaseModel):
    """The caller's current break, so a reloaded workspace can restore it."""
    on_break: bool
    current: Optional[BreakEntry] = None

class LoginSessionEntry(BaseModel):
    login_at: datetime
    # None while the session is still open.
    logout_at: Optional[datetime] = None
    # 'logout' (clicked Log out) or 'inactive' (heartbeat went silent).
    ended_reason: Optional[str] = None
    duration_seconds: int
    is_open: bool

class LoginSessionHistory(BaseModel):
    name: str
    date: str
    sessions: List[LoginSessionEntry] = Field(default_factory=list)
    total_seconds: int
    breaks: List[BreakEntry] = Field(default_factory=list)
    break_seconds: int = 0

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
# 'Passed', 'Reviewed' and 'Monitored' are further owner-only review outcomes.
TASK_STATUSES = [
    "New", "In Progress", "Completed", "Approved", "Declined",
    "Verified", "Checked", "Passed", "Reviewed", "Monitored",
]

# ---------------------------------------------------------------------------
# Image size vocabulary (Image Inventory)
# ---------------------------------------------------------------------------
#
# Users do not think in "5184 x 3888"; they think "a full-size one" and "a
# half". These are the named resolutions this deployment actually produces,
# verified against production data on 2026-09-14: of 497 task rows, 395 (79.5%)
# were 2592x1944 and 86 (17.3%) were 5184x3888. The names are literally
# accurate — 2592x1944 is exactly half of 5184x3888 on each axis.
#
# Keyed on an exact (width, height) tuple, so a transposed image (3888x5184, a
# portrait re-export) is deliberately NOT "Full". It is a different image and
# lands in "Other", where somebody can notice it.
#
# Data, not branching logic, and defined in one place: the filter, the summary
# and the spreadsheet all read this dict.
IMAGE_SIZE_NAMED = {
    (5184, 3888): "Full",
    (2592, 1944): "Half",
}

# The two catch-alls. They are structurally different and must not be merged,
# however tempting a two-value enum looks on a dataset where one of them is
# currently empty.
#
# OTHER — measured, but not a known size. A fact about the image. Production
# had 16 such rows across 16 *distinct* one-off resolutions (phone photos,
# crops, re-exports; one 1562x688 is not even the usual aspect ratio). A
# two-value enum misfiles every one of them.
#
# UNKNOWN — no usable dimensions recorded. A gap in our data, not a fact about
# the image. Zero rows today, and that is exactly why the bucket must exist:
# `image_width`/`image_height` are nullable (models.Task), rows predating those
# columns were never measured, and any future import path can create a task
# without them. Folding Unknown into Other would hide that gap behind a
# plausible bucket in a report whose whole purpose is to be trusted about
# sizes — the failure would be silent under-reporting, discovered only after
# somebody acted on it.
IMAGE_SIZE_OTHER = "Other"
IMAGE_SIZE_UNKNOWN = "Unknown"

# Display order: named sizes largest-first, then the two catch-alls last. The
# summary strip renders in this order and includes every entry even at zero, so
# the display keeps a stable shape as filters change instead of reflowing.
IMAGE_SIZE_CATEGORIES = [
    "Full",
    "Half",
    IMAGE_SIZE_OTHER,
    IMAGE_SIZE_UNKNOWN,
]


# Caps. One request must not be able to ask for 50,000 rows and 50,000
# filesystem calls, and the spreadsheet must not be built unboundedly in
# memory.
IMAGE_INVENTORY_MAX_LIMIT = 500
IMAGE_INVENTORY_DEFAULT_LIMIT = 50
IMAGE_INVENTORY_MAX_EXPORT_ROWS = 20000

# Above this many rows in the filtered set, the size total covers only the
# returned page and the response says so (`total_size_is_complete=False`).
# A true total costs one stat per row; this bound keeps a page render cheap
# while staying correct for every project we actually have -- the largest is
# 269 tasks, and the whole database is 497 rows, so in practice the true total
# is always computed. The bound exists for the day somebody bulk-imports.
IMAGE_INVENTORY_FULL_SIZE_MAX_ROWS = 2000


def categorize_image_size(width: Optional[int], height: Optional[int]) -> str:
    """Bucket one (width, height) pair into the vocabulary above.

    Treats zero as unmeasured, not as a real dimension: `formats.common.
    image_size()` returns (0, 0) for an unreadable file, so a row carrying zero
    is in exactly the same state as one carrying NULL and belongs in Unknown.
    A half-measured row (width but no height) is likewise unmeasured — we
    cannot name a resolution we only half know.
    """
    if not width or not height or width <= 0 or height <= 0:
        return IMAGE_SIZE_UNKNOWN
    return IMAGE_SIZE_NAMED.get((width, height), IMAGE_SIZE_OTHER)


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
    # Restrict the export to specific tasks. None/omitted means "no task
    # restriction". This is what backs the Exports page's "Current task"
    # option, which the workspace Export button arms with the open task — it is
    # deliberately a task filter and not a pseudo-status, because "current" is a
    # property of the caller's session, not of the row.
    #
    # Combined with statusFilter as AND: a task must match both to be exported.
    taskIds: Optional[List[int]] = None
    include: str = "annotations_only"


class ExportJobStatus(BaseModel):
    status: str  # pending | completed | failed
    error: Optional[str] = None
    task_count: Optional[int] = None


class AssignmentEvent(BaseModel):
    """One entry in a task's assignment history."""
    model_config = {"from_attributes": True}
    member_name: str
    # 'assigned' | 'unassigned'
    action: str
    # Who made the change; None for rows whose actor was not recorded.
    actor_name: Optional[str] = None
    created_at: Optional[datetime] = None


class TaskAssignmentHistory(BaseModel):
    """Who holds a task now, who has ever held it, and every change between.

    `events` is empty for a task assigned before the history table shipped and
    never reassigned since (migration d7a1b93c5e42 backfills the assignee but
    deliberately invents no event). The UI distinguishes that from "never
    assigned" rather than showing a misleading blank.
    """
    task_id: int
    assignees: List[str] = Field(default_factory=list)
    participants: List[str] = Field(default_factory=list)
    events: List[AssignmentEvent] = Field(default_factory=list)


class TaskDetail(BaseModel):
    """Single-task response for GET /api/tasks/{id} — includes annotations.

    The list endpoint (GET /api/tasks) returns annotation-free rows for the
    gallery shell; this endpoint hydrates the one task that was actually
    opened, keeping the initial page load small (T1.1).
    """
    model_config = {"from_attributes": True}

    id: int
    description: Optional[str] = None
    # Primary assignee; mirrors assignees[0]. Kept so older clients keep working.
    assignee: Optional[str] = None
    # Everyone currently assigned, primary first.
    assignees: List[str] = Field(default_factory=list)
    # Everyone who has ever held this task, current assignees first. This is
    # what answers "who has worked on this image" for the project owner.
    participants: List[str] = Field(default_factory=list)
    image_path: Optional[str] = None
    status: Optional[str] = None
    time_spent: Optional[int] = None
    updated_at: Optional[datetime] = None
    annotations: List[AnnotationModel] = Field(default_factory=list)
    comment_count: int = 0
    class_count: int = 0


class NotificationResponse(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    type: str
    entity_id: Optional[int] = None
    message: str
    # Serialized as timezone-aware UTC (the column is UTCDateTime), so the
    # client can parse it directly instead of appending a "Z" and hoping.
    created_at: Optional[datetime] = None
    # Present so a notice about a task can deep-link to the project that holds
    # it; resolved at read time because notifications store only entity_id.
    project_id: Optional[int] = None
    # Also resolved at read time rather than baked into `message`: a stored
    # message is an immutable snapshot of what was true when it fired, so a
    # renamed project would leave stale text in the bell forever.
    project_name: Optional[str] = None


class MarkReadRequest(BaseModel):
    notification_ids: List[int] = Field(default_factory=list, max_length=500)


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

class TaskListItem(BaseModel):
    model_config = {"from_attributes": True}
    id: int
    description: Optional[str] = None
    # Primary assignee; mirrors assignees[0]. Kept so older clients keep working.
    assignee: Optional[str] = None
    # Everyone currently assigned, primary first.
    assignees: List[str] = Field(default_factory=list)
    image_path: Optional[str] = None
    status: Optional[str] = None
    time_spent: Optional[int] = None
    updated_at: Optional[datetime] = None
    comment_count: int = 0
    class_count: int = 0
    # Only populated by the cross-project listing (no `projectId` given), where
    # a row is meaningless without saying which project it came from. The
    # per-project listing leaves them None rather than repeating the project on
    # every row of a table that already names it.
    project_id: Optional[int] = None
    project_name: Optional[str] = None

class PaginatedTasks(BaseModel):
    items: List[TaskListItem]
    total: int
    limit: int
    offset: int

# ---------------------------------------------------------------------------
# Image Inventory ("Images Info")
# ---------------------------------------------------------------------------

class ImageInventoryRow(BaseModel):
    """One image in the inventory report.

    `file_size` is Optional and a missing file yields None, never 0. A missing
    multi-megabyte original rendered as "0 B" is a quietly wrong number in a
    report whose whole job is to be right about sizes, and zero is plausible
    enough that nobody questions it. The client renders None as a dash.
    """
    id: int
    filename: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    category: str
    file_size: Optional[int] = None
    status: Optional[str] = None


class ImageInventoryCategoryCount(BaseModel):
    category: str
    count: int


class ImageInventorySummary(BaseModel):
    """Totals over the ENTIRE filtered set, not the current page.

    Counted from the page these would change as the user pages, which is not a
    fact about the project and is worse than omitting them.
    """
    total: int
    # Every category, including those with a zero count, so the summary strip
    # keeps a stable shape as filters change instead of reflowing. In
    # particular Unknown is always present: if a project is 40% unmeasured the
    # reader has to see that, or they act on a number describing 60% of their
    # data while it looks complete.
    categories: List[ImageInventoryCategoryCount]
    # Files referenced by a row but absent from disk. Costs nothing once we are
    # already stat-ing, and it is operational information most teams have
    # nowhere else.
    missing_files: int = 0
    # Summed file size in bytes.
    total_size: Optional[int] = None
    # Whether `total_size` covers the whole filtered set or only this page.
    # A whole-project total needs a stat per row, so it is computed only below
    # IMAGE_INVENTORY_FULL_SIZE_MAX_ROWS; above that we sum the page and say
    # so. The UI labels it "this page" when False. A partial total must never
    # be presented silently as a complete one.
    total_size_is_complete: bool = True


class ImageInventoryPage(BaseModel):
    items: List[ImageInventoryRow]
    summary: ImageInventorySummary
    total: int
    limit: int
    offset: int


class TaskSequenceItem(BaseModel):
    id: int
    description: Optional[str] = None
    image_path: Optional[str] = None

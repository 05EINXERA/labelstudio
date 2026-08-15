from datetime import datetime
from typing import Optional, List, Dict, Any, Literal, get_args
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

class ProjectUpdate(BaseModel):
    # Optional: PATCH /api/projects/{id} takes the id from the path. The legacy
    # POST /api/projects/update requires it in the body.
    id: Optional[int] = None
    name: Optional[str] = None
    status: Optional[str] = None

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
    # Explicit confirmation that an empty `annotations` payload is a real
    # delete-all, not an artifact of a client that never finished loading the
    # task's existing work. See .devnotes/offline/INCIDENT_692.md: a save that
    # silently carried an empty in-memory state wiped 403 real annotations.
    # Default False so every existing caller (which never sends this field)
    # gets the safe behavior automatically.
    allow_clear: bool = False

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
    created_at: Optional[datetime] = None
    total: int = 0
    # `completed` counts approved-group tasks (signed off), not tasks merely
    # marked 'Completed' by an annotator — those are `awaiting_review`.
    completed: int = 0
    awaiting_review: int = 0
    in_progress: int = 0
    progress: int = 0
    comments: int = 0
    classes: int = 0
    total_time: int = 0
    avg_time_per_task: int = 0
    # The caller's effective role on this project, and whether they own it. The
    # Phase 4 UI levels every control on these two (04_UI_UX.md). Added fields,
    # never renames — a cached JS bundle ignores them and behaves as before.
    # Optional because a client on an older bundle may not send them back, and
    # because the resolver returns None for "no access" (which cannot appear in
    # this list, but the type should not lie).
    my_role: Optional[str] = None
    is_owner: bool = False

class ProjectMetrics(BaseModel):
    total: int
    # Signed-off tasks — any APPROVED_STATUSES member. Tasks sitting at
    # 'Completed' are counted in `awaiting_review` instead.
    completed: int
    awaiting_review: int = 0
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

class TimeLogOut(BaseModel):
    """One time-log row as returned by /api/time-logs.

    `user_id` is null for historical rows whose free-text name matches no
    account; the UI shows those as unlinked rather than guessing an owner.
    """
    name: str
    time_logged: int = 0
    user_id: Optional[int] = None

class TimeLogUpdateResult(BaseModel):
    status: str
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
#
# ---------------------------------------------------------------------------
# THE APPROVED GROUP — the one place to add a new approval status.
# ---------------------------------------------------------------------------
#
# 'Approved', 'Verified', 'Checked' and 'Passed' are synonyms: they mean exactly
# the same thing about the work (a reviewer signed it off) and differ only in
# *which batch* the sign-off belongs to. The workflow is export-by-batch: tasks
# approved before the last export carry 'Approved', the next week's carry
# 'Verified', and so on, so exporting one status yields only the new work
# instead of re-exporting everything ever approved.
#
# Every rule that applies to 'Approved' applies to all of them, without
# exception — the reviewer-role gate, the completion statistics, the export
# filter, the demote-on-edit rule and the interop mapping all derive from this
# tuple rather than naming a status. **Adding a batch status is one line here.**
#
# Order matters only for display: this is the order the export checkboxes and
# the status dropdowns list them in.
#
# NOTE: a status is a coarse batch marker — one name per batch, and a new name
# means a deploy. If this outlives a handful of batches, the durable fix is an
# `exported_at` column stamped by the export job, which needs no vocabulary at
# all. Deliberately deferred.
APPROVED_STATUSES = ("Approved", "Verified", "Checked", "Passed")

# The working (non-approval) vocabulary, in display order.
WORKING_STATUSES = ("New", "In Progress", "Completed")

# 'Rejected' means "sent back for rework". Without it a reviewer's only way to
# signal a problem is to flip the status back to 'In Progress', which is
# indistinguishable from an annotator re-opening their own work.
#
# Anything added to APPROVED_STATUSES is mapped automatically in
# formats/common.py's TO_EXTERNAL_STATUS — the export filter and the import
# mapping both consume this vocabulary (E-28).
TASK_STATUSES = [*WORKING_STATUSES, *APPROVED_STATUSES, "Rejected"]

# Transitions that require the Reviewer project role. Approving *and* rejecting
# are both review verdicts; only a reviewer may record either.
# .devnotes/teams/01_DESIGN.md § 4.
REVIEW_STATUSES = frozenset(APPROVED_STATUSES) | {"Rejected"}

# Statuses that assert "this work is finished", so editing the annotations
# afterwards must demote the task back to 'In Progress' rather than let amended
# work keep a finished status a reviewer granted to a previous version.
TERMINAL_STATUSES = frozenset(APPROVED_STATUSES) | {"Completed"}


def is_approved(status: Optional[str]) -> bool:
    """True when `status` is any member of the approved group."""
    return status in APPROVED_STATUSES

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

    # Assignment, plus the resolved display names. The canvas decides read-only
    # mode from these before the annotator draws anything, so omitting them here
    # is what made a task assigned elsewhere look editable until Save failed.
    assigned_team_id: Optional[int] = None
    assignee_user_id: Optional[int] = None
    assigned_team_name: Optional[str] = None
    assignee_name: Optional[str] = None
    # Authoritative answer to "may I write this?", computed server-side by the
    # same function that enforces it. The client mirrors the rule for instant
    # feedback, but this is the value that cannot drift.
    can_write: bool = True


class TaskPage(BaseModel):
    """One page of tasks, returned by GET /api/tasks when `page` is given.

    Only the paged form is wrapped. Without `page` the endpoint still returns a
    bare array, so existing callers are untouched
    (.devnotes/tasks-pagination/PLAN.md § 3.2).

    `items` is `List[Any]` rather than a task model on purpose: the list
    endpoint serves two different row shapes depending on `include_annotations`,
    and pinning one here would either force the annotation blobs into the
    annotation-free response or silently drop fields from the other. The rows
    are hand-built dicts in the router; this schema documents the *envelope*.
    """
    items: List[Any] = Field(default_factory=list)
    total: int
    page: int
    page_size: int
    total_pages: int


class TaskOrder(BaseModel):
    """Every task id for a project, in display order — GET /api/tasks/order.

    Ids only, deliberately: the canvas needs the sequence (to walk prev/next and
    to show "39 / 50") but not the rows, and fetching rows for that would undo
    the payload savings of per-task hydration (CLAUDE.md rule 17).
    """
    ids: List[int] = Field(default_factory=list)


# --- Teams -------------------------------------------------------------------
#
# Roles are `Literal`, not `str`: an invalid role is then a 422 at the boundary
# and can never reach the database as a bad row. The team axis and the project
# axis are separate vocabularies (.devnotes/teams/01_DESIGN.md § 2) and are
# deliberately not unified into one enum.

TeamRoleLiteral = Literal["owner", "manager", "member"]


class TeamMemberOut(BaseModel):
    """One roster entry.

    Exposes **only** `user_id` and `username` from the users table — never the
    password hash, the account's created_at, or anything else. Team membership
    is not a reason to learn more about someone's account
    (.devnotes/teams/03_API.md § 2.1).
    """
    user_id: int
    username: str
    role: TeamRoleLiteral
    added_by: Optional[int] = None
    created_at: Optional[datetime] = None


class TeamSummary(BaseModel):
    """A team as it appears in the caller's team list."""
    id: int
    name: str
    slug: str
    description: Optional[str] = None
    # The caller's own role, which is what the UI levels its controls on.
    my_role: TeamRoleLiteral
    is_owner: bool
    member_count: int = 0
    project_count: int = 0
    created_at: Optional[datetime] = None


class TeamProjectOut(BaseModel):
    """A project this team can reach, and what the grant lets it do.

    `role` is the *grant* role (viewer/annotator/reviewer/manager), a different
    vocabulary from the team roles above.
    """
    project_id: int
    name: Optional[str] = None
    slug: Optional[str] = None
    role: str


class TeamDetail(TeamSummary):
    members: List[TeamMemberOut] = Field(default_factory=list)
    projects: List[TeamProjectOut] = Field(default_factory=list)


class TeamCreate(BaseModel):
    # The slug is derived server-side from the name, never accepted from the
    # client: it is a uniqueness key, so letting callers set it invites
    # collisions the user did not cause and cannot fix.
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)


class TeamUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=500)


class TeamMemberAdd(BaseModel):
    """Onboarding is by exact username (.devnotes/teams/01_DESIGN.md § 5.1).

    There is deliberately no user-search endpoint to pair with this, and the
    endpoint is rate limited — together those are what make the username
    disclosure an accepted, scoped trade rather than an open oracle (E-14).
    """
    username: str = Field(min_length=1, max_length=64)
    # "owner" is absent by design: ownership moves by transfer only, so that a
    # team can never end up with two owners and no tiebreak.
    role: Literal["manager", "member"] = "member"


class TeamMemberRoleUpdate(BaseModel):
    role: Literal["manager", "member"] = "member"


class TransferOwnership(BaseModel):
    username: str = Field(min_length=1, max_length=64)


class TeamDeleteResult(BaseModel):
    """What the delete actually removed, echoed back so the UI can confirm it
    matches the counts it warned about (E-06)."""
    status: str
    grants_removed: int
    tasks_unassigned: int
    members_removed: int


# The project-access vocabulary. Deliberately a different set from the team
# roles above: `owner` is absent because project ownership is `Project.owner_id`
# and never a grant row — a grant that could say "owner" would give a project two
# owners with no tiebreak (.devnotes/teams/02_SCHEMA.md § 4).
GrantRoleLiteral = Literal["viewer", "annotator", "reviewer", "manager"]

# What `effective_project_role` can return, which *does* include owner.
ProjectRoleLiteral = Literal["viewer", "annotator", "reviewer", "manager", "owner"]


class GrantOut(BaseModel):
    project_id: int
    team_id: int
    team_name: Optional[str] = None
    team_slug: Optional[str] = None
    role: GrantRoleLiteral
    granted_by: Optional[int] = None
    created_at: Optional[datetime] = None


class GrantCreate(BaseModel):
    team_id: int
    role: GrantRoleLiteral = "annotator"


class GrantRoleUpdate(BaseModel):
    role: GrantRoleLiteral


class GrantRevokeResult(BaseModel):
    status: str
    # Revoking access also returns that team's tasks on this project to the
    # shared pool (E-08); echoed so the UI can report what actually happened.
    tasks_unassigned: int


class AssignableMember(BaseModel):
    """Someone who can be handed a task on a given project.

    Reached through a team that holds a grant. `team_name` is carried so the
    picker can group by team — assigning "someone from Team Alpha" is a more
    meaningful choice than picking a name off a flat list.
    """
    user_id: int
    username: str
    team_id: int
    team_name: str
    team_role: TeamRoleLiteral


class TaskAssignment(BaseModel):
    """Both fields are explicitly nullable: `null` means "unassign", which is
    different from omitting the field. Pydantic cannot distinguish those on its
    own, so the endpoint inspects `model_fields_set`."""
    assigned_team_id: Optional[int] = None
    assignee_user_id: Optional[int] = None


class TaskAssignmentResult(BaseModel):
    status: str
    task_id: int
    assigned_team_id: Optional[int] = None
    assignee_user_id: Optional[int] = None
    # E-10: assigning someone outside the assigned team is allowed, because
    # individual assignment is advisory by design. The caller is told, not
    # blocked.
    warnings: List[str] = Field(default_factory=list)


class BulkAssign(BaseModel):
    ids: List[int]
    assigned_team_id: Optional[int] = None
    assignee_user_id: Optional[int] = None


class BulkAssignResult(BaseModel):
    status: str
    updated: int
    skipped: int
    warnings: List[str] = Field(default_factory=list)


# Review verbs. Every member of APPROVED_STATUSES gets a verb (its lowercased
# status name) so a batch approval goes through the review endpoint and lands in
# the TaskReview audit log exactly like a plain 'Approved' does — a batch marker
# must not cost us the record of who signed the work off.
#
# 'approved' is the historical spelling and stays first so cached bundles that
# only know it keep working unchanged.
APPROVAL_ACTIONS = tuple(s.lower() for s in APPROVED_STATUSES)
REVIEW_ACTIONS = (*APPROVAL_ACTIONS, "rejected", "reopened")

# Verb -> the status it sets. 'reopened' is not a status of its own: sending a
# task back into the working vocabulary is what re-opening means.
REVIEW_ACTION_STATUS = {
    **{s.lower(): s for s in APPROVED_STATUSES},
    "rejected": "Rejected",
    "reopened": "In Progress",
}

# Pydantic needs a literal type, which cannot be built from a variable, so the
# verbs are spelled out once more here. The assert below is the drift guard:
# adding a status to APPROVED_STATUSES without adding its verb here fails at
# import time rather than shipping an endpoint that 422s on the new batch.
ReviewActionLiteral = Literal[
    "approved", "verified", "checked", "passed", "rejected", "reopened"
]

assert set(get_args(ReviewActionLiteral)) == set(REVIEW_ACTIONS), (
    "ReviewActionLiteral is out of sync with APPROVED_STATUSES — add the new "
    f"verb(s) {sorted(set(REVIEW_ACTIONS) - set(get_args(ReviewActionLiteral)))} "
    "to the Literal."
)


class ReviewCreate(BaseModel):
    action: ReviewActionLiteral
    note: Optional[str] = Field(default=None, max_length=1000)


class ReviewOut(BaseModel):
    id: int
    task_id: int
    reviewer_id: Optional[int] = None
    reviewer_username: Optional[str] = None
    action: str
    note: Optional[str] = None
    previous_status: Optional[str] = None
    created_at: Optional[datetime] = None


class ReviewResult(BaseModel):
    status: str
    task_id: int
    task_status: str
    review: ReviewOut


class MeTeam(BaseModel):
    id: int
    name: str
    role: TeamRoleLiteral


class Me(BaseModel):
    """Identity for the frontend.

    The client previously read its identity from
    `localStorage['dataset_username']`, free text the user typed — not
    trustworthy and often not even accurate (CLAUDE.md rule 14). Every levelled
    UI decision needs the real thing.
    """
    id: int
    username: str
    teams: List[MeTeam] = Field(default_factory=list)


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str

class PasswordChange(BaseModel):
    """Self-service password change.

    The current password is required even though the caller is already
    authenticated: a session cookie left behind on a shared annotation PC must
    not be enough to lock the real owner out of their account.
    """
    current_password: str
    new_password: str


class Token(BaseModel):
    access_token: str
    token_type: str
    # Double-submit CSRF token, also set as a readable cookie. Returned in the
    # body so a non-browser client (tests, scripts) can echo it back without
    # having to parse Set-Cookie.
    csrf_token: Optional[str] = None
